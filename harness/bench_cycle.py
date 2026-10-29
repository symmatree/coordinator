#!/usr/bin/env python3
"""Drive the bench simulated-flight cycle, start to finish, the same way every time.

THE WAY TO RUN THIS IS THE WORKFLOW, not this file by hand: the `bench-simflight`
WorkflowTemplate in tiles (`tanka/environments/fleet-control/main.jsonnet`) is the declared
copy of the procedure, and it calls the stages below in order. Running the stages by hand is
how the cycle drifted trial to trial -- a wait skipped here, a different flight name there --
which is the whole reason it is declared now.

    argo submit --from workflowtemplate/bench-simflight -n fleet-control \
        -p flight=bench-20261017-simflight-11

Each stage is one step of that workflow, and each is a separate process that holds no state
between them. The measurement window is the workflow's `suspend` step rather than a sleep in
here: a suspended step has no pod, so nothing of ours is scheduled, logging or polling while
the cameras are being timed.

The waits that matter, and what each is for:

  every converge together -- a play runs in its own pod, so the fleet-wide cap on
    playbook runs is gone and the deploy takes one converge's time rather than one per node
  a reboot after converging -- a converge installs and pulls and starts nothing, so the
    reboot is the apply step
  uptime going backwards before trusting any pod state -- the bus documents are retained,
    so a pod that is down still publishes its last reading
  every campod camera `ready`, not merely present -- warmup frames are not capture frames.
    That is warm AND `sync_ready`, because warmup finishing does not mean the pair is
    aligned and a window that opens early spends its head discarding frames. The
    coordinator is not waited on: no OAK is attached for this flight (CAMERA_NODES)
  only the FC talker stopped to arm -- it owns the arm file, so it has to stop or it
    overwrites what we write, but oak-capture reads that same file and has to keep running
    or the coordinator's session has nothing in it
  nothing touched during the window -- every command on a 417 MiB board evicts the
    camera's working set, and timing is what is under test

Environment, all of which the fleet-control pod already has:
  FLEET_URL             the API (default below)
  FLEET_SSH_KEY         private key that reaches pi@coordinator -- the arm stage only,
                        because the arm file has no API verb
  FLEET_SSH_USER        login account on the device (default pi)
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

FLEET_URL = os.environ.get("FLEET_URL", "https://fleet.tiles.symmatree.com")
CAMPODS = ("campod-se", "campod-sw", "campod-ne", "campod-nw")
# Every box the cycle converges, reboots, waits on and collects from. The coordinator is a
# capture node too since oak-capture (#609): its session holds the OAK's frames and IMU
# beside the router's tlog and timesync. blackfly is not on the vehicle for this flight.
NODES = ("coordinator", *CAMPODS)
# The nodes whose camera has to be ready before the window opens. Not the coordinator: no
# OAK is attached for this flight, so its camera never reports ready, but its session (the
# tlog, timesync) is still converged, rebooted and collected with the rest.
CAMERA_NODES = CAMPODS
ARM_FILE = "/var/lib/coordinator/ipc/fc_armed"
# The unit that owns the arm file. Stopped by name, not by the bus quiesce: that stops
# every unit in the coordinator's POD_LINK_COLLECTION_UNITS, which since #609 is the
# router AND oak-capture, and a window with oak-capture stopped records nothing from it.
FC_TALKER_UNIT = "coordinator-mavlink"


def log(msg: str) -> None:
    print(f"[bench] {msg}", flush=True)


# The image writes its own provenance here, in the #326 manifest format, because a process
# cannot read its own image labels.
CONTAINER_IMAGE = "/etc/container-image"


def build_revision(path: str = CONTAINER_IMAGE) -> str | None:
    """The commit this image was built from, or None if there is no manifest to read.

    A local run has none, which is not an error -- it means "not from an image".
    """
    try:
        text = pathlib.Path(path).read_text()
    except OSError:
        return None
    match = re.search(r'^ORG_OPENCONTAINERS_IMAGE_REVISION="?([0-9a-f]{7,40})"?',
                      text, re.MULTILINE)
    return match.group(1) if match else None


def api(path: str, method: str = "GET", timeout: int = 300, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    headers = {} if data is None else {"Content-Type": "application/json"}
    req = urllib.request.Request(f"{FLEET_URL}{path}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read() or b"null")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"{method} {path} -> {exc.code}: {body}") from exc


# ---- pure helpers, so the parts that decide things are testable without a fleet ---------

def pick_session(sessions: list[dict]) -> dict | None:
    """The run's own session: newest CLOSED one by last_activity_utc.

    NOT by file count, and NOT by first/last_utc. Those are sidecar-derived, so they are
    null for a session with no frames and the image's build epoch for frames written
    before chrony's step -- picking by either chose the wrong session in three trials,
    once losing a four-minute window to a 23-second boot. last_activity_utc is an mtime
    across the boot's captures and its collectd tree (coordinator#525).
    """
    closed = [s for s in sessions if not s.get("open") and s.get("last_activity_utc")]
    if not closed:
        return None
    return max(closed, key=lambda s: s["last_activity_utc"])


def is_fresh(pods: list[dict], uptime_before: float, expected: int = len(NODES)) -> bool:
    """Did these pods actually reboot?

    Uptime going BACKWARDS, not "a document exists". The bus documents are retained, so a
    pod that is down still publishes its last state -- a check for presence declared the
    cameras up while the pods were mid-reboot, and the run started against stale data.

    EVERY NODE, COUNTED. On 2026-10-18 this passed on one: collect reboots the coordinator
    too, the broker runs there, and the retained documents are gone until each pod
    republishes -- so campod-se came back and published while campod-sw, whose reboot had
    actually failed, was simply absent. `all()` over one pod is true, and the stage reported
    "a fresh boot on both campods: ok" while meaning one. cameras_ready counts; this did not.
    """
    if len(pods) != expected:
        return False
    ups = [(p.get("raw") or {}).get("as_of_boot_s") for p in pods]
    return bool(ups) and all(u is not None and u < uptime_before for u in ups)


def cameras_ready(pods: list[dict], expected: int = len(CAMERA_NODES)) -> bool:
    """Every camera ready, which is the pod's own word for "arming now works".

    ONE FIELD, AND NOT ASSEMBLED HERE. `camera.ready` is warm AND synced, combined by the
    campod that knows both (#577); on the coordinator it is the OAK warm on all three of
    its cameras, and that document has the same shape. This gate used to read `ready` --
    which then meant warm only -- and separately reason about three-valued `sync_ready`,
    and arming is not the only reader: the front panel has to answer the same question for
    someone standing in front of it with no network. Two readers deciding what readiness
    means is how they disagree, which they did: this gate was fixed on 2026-10-19 and the
    panel was not.

    Why it matters that the wait is real: the window of 2026-10-18T22:40 opened 18.5 s
    early, 38 frames on each pod recorded `sync_wait` and were discarded, and that run kept
    232 frames where its neighbours kept 248 and 250.
    """
    cams = [(p.get("raw") or {}).get("camera") or {} for p in pods]
    return len(cams) == expected and all(c.get("ready") is True for c in cams)


# ---- fleet interaction -----------------------------------------------------------------

def fleet_pods() -> list[dict]:
    """The status documents of every node in the cycle, including the coordinator's."""
    pods = api("/pods", timeout=60).get("pods", [])
    return sorted((p for p in pods if (p.get("node") or "") in NODES),
                  key=lambda p: p["node"])


def oldest_boot() -> float:
    """The lowest uptime on the bus, the reference `is_fresh` wants to see go backwards."""
    return min((p.get("raw") or {}).get("as_of_boot_s") or 0 for p in fleet_pods())


def wait_for(what: str, check, timeout_s: int, every_s: int = 15):
    """Poll until the check passes, or the time is up.

    A FAILING POLL IS NOT A FAILED WAIT. `collect` reboots the coordinator, which is where
    the pod bus broker runs, and then waits for the campods to come back -- so /pods answers
    502 `connack timeout` for as long as the broker's own host is down. That is the expected
    middle of this stage, and treating it as fatal killed a cycle whose window had already
    completed. The last error is carried into the timeout message instead, so a wait that
    really never came good still says why.
    """
    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            got = check()
        except SystemExit as exc:  # api() raises this for any HTTP error
            last_error = str(exc)
            got = None
        if got:
            log(f"{what}: ok")
            return got
        time.sleep(every_s)
    raise SystemExit(f"[bench] timed out waiting for {what} after {timeout_s}s"
                     + (f"; last error was {last_error}" if last_error else ""))


def watch_workflow(name: str, label: str, timeout_s: int = 3600) -> None:
    """Poll a workflow to completion. Branches on `phase`, which Argo owns.

    No line scanning: the steps' output is their pods' logs, collected into Loki rather
    than routed through the service, and a step that fails fails the workflow. So the
    phase IS the answer. Converges and collects alike (coordinator#566): a converge is a
    workflow the service submitted and nothing more, so the same poll follows both.

    The wall clock IS the bound for a workflow that never gets a pod. On 2026-10-17 a
    missing WorkflowTaskResult CRD held a converge in `Pending` indefinitely with nothing
    in its phase to say so; coordinator#543 would have failed it in the submitter after
    five minutes and was withdrawn, because the converge did eventually run when the CRD
    landed -- so a short deadline would have recorded a success as a failure. An hour is
    long enough for a collect too: 336 and 361 MB took about seven minutes each on
    2026-10-18, and a full card is larger.
    """
    finished = {"Succeeded", "Failed", "Error"}
    said = None
    deadline = time.monotonic() + timeout_s
    while True:
        wf = api(f"/workflows/{name}", timeout=60)
        phase = wf.get("phase")
        if phase != said:
            log(f"  {label}: {phase}{': ' + wf['message'] if wf.get('message') else ''}")
            said = phase
        if phase in finished:
            if phase != "Succeeded":
                raise SystemExit(f"[bench] {label} ended {phase}: {wf.get('message') or ''} "
                                 f"{wf.get('url') or ''}")
            log(f"{label}: succeeded")
            return
        if time.monotonic() > deadline:
            raise SystemExit(
                f"[bench] {label} still {phase} after {timeout_s}s; {wf.get('url') or ''}")
        time.sleep(20)


def ssh_coordinator(key: str, command: str) -> str:
    # `host`, which is what the roster calls it, defaulting to the node name -- the field is
    # optional there and the name is also a usable address. This read `n["address"]`, a
    # field the roster has never had, so every arm and disarm failed with "the coordinator
    # has no address in the inventory".
    host = next((n.get("host") or n["name"] for n in api("/nodes", timeout=60)
                 if n["name"] == "coordinator"), None)
    if not host:
        raise SystemExit("[bench] the roster has no coordinator")
    user = os.environ.get("FLEET_SSH_USER", "pi")
    done = subprocess.run(
        ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
         "-o", "StrictHostKeyChecking=accept-new", f"{user}@{host}", command],
        capture_output=True, text=True)
    if done.returncode != 0:
        # A SystemExit rather than a CalledProcessError: this is a reachability fact, and a
        # python traceback in the run record buries it under twelve lines of frames.
        raise SystemExit(f"[bench] ssh {user}@{host} failed ({done.returncode}): "
                         f"{(done.stderr or '').strip()[:200]}")
    return done.stdout.strip()


# ---- the stages, one per workflow step --------------------------------------------------

def stage_deploy() -> None:
    before = oldest_boot()
    # ALL AT ONCE. A play runs in its own pod, so each further converge costs the service a
    # submit rather than another ansible interpreter in its memory -- the reason the
    # fleet-wide cap existed. Submitted together, then watched in turn; a later watch
    # returns immediately if that workflow has already finished.
    workflows = {}
    for node in NODES:
        sub = api(f"/nodes/{node}/converge", method="POST", timeout=120)
        workflows[node] = sub["workflow"]
        log(f"{node}: converge {sub['workflow']} ({sub.get('playbook')} from "
            f"{(sub.get('revision') or '')[:10]})")
    for node, name in workflows.items():
        try:
            watch_workflow(name, f"{node} converge")
        except SystemExit:
            # Submitting both means a failure here leaves the other one RUNNING and
            # unwatched -- serial never submitted it at all. Name it and say what it is
            # doing, so the operator is not left to find a converge nobody mentioned.
            for other, other_name in workflows.items():
                if other == node:
                    continue
                try:
                    phase = api(f"/workflows/{other_name}", timeout=60).get("phase")
                except SystemExit:
                    phase = "unknown -- the API did not answer"
                log(f"{other} converge {other_name} is {phase} and no longer watched")
            raise
    for node in NODES:
        api(f"/nodes/{node}/reboot", method="POST", timeout=180)
        log(f"{node}: rebooting")
    wait_for("a fresh boot on every node",
             lambda: is_fresh(fleet_pods(), before) and fleet_pods(), 900)
    wait_for("every campod camera ready (warm and synced)",
             lambda: cameras_ready([p for p in fleet_pods() if p["node"] in CAMERA_NODES]),
             900)
    for p in fleet_pods():
        cam = (p.get("raw") or {}).get("camera") or {}
        log(f"  {p['node']}: ready at mono {cam.get('ready_at_mono_s')}s")


def stage_arm(key: str) -> None:
    # The FC talker owns the arm file, so it has to stop or it overwrites what we write --
    # and ONLY it. The bus quiesce (`stop-capture`) stops everything in the coordinator's
    # POD_LINK_COLLECTION_UNITS, which since #609 includes oak-capture, the thing that reads
    # the file we are about to write. fleet-control has no per-unit verb, so this is the
    # same ssh the file write already needs, with the stop in front of it.
    ssh_coordinator(key, f"sudo systemctl stop {FC_TALKER_UNIT} && echo 1 | sudo tee {ARM_FILE}")
    log(f"coordinator: {FC_TALKER_UNIT} stopped, oak-capture left running; armed. The "
        "window is the workflow's suspend step, so nothing of ours runs during it")


def stage_disarm(key: str) -> None:
    # RETRIED, because this also runs as the workflow's onExit handler -- and the thing it
    # most needs to survive is a cycle that failed during `collect`, which reboots the
    # coordinator. Disarming has to actually land, so it waits for the box rather than
    # reporting that a rebooting machine would not answer.
    wait_for("the coordinator to take the disarm",
             lambda: ssh_coordinator(key, f"echo 0 | sudo tee {ARM_FILE}") or True, 300, 20)
    log("disarmed; 20s for the writers to close out")
    time.sleep(20)


def stage_collect(flight: str) -> None:
    before = oldest_boot()
    for node in NODES:
        api(f"/nodes/{node}/reboot", method="POST", timeout=180)
    log("every node rebooting -- this is what makes the data durable, since the router's "
        "own disarm sync() cannot run with the router stopped")
    wait_for("a fresh boot on every node",
             lambda: is_fresh(fleet_pods(), before) and fleet_pods(), 900)
    time.sleep(60)
    targets = []
    for node in NODES:
        sessions = api(f"/nodes/{node}/sessions", timeout=300).get("sessions", [])
        best = pick_session(sessions)
        if not best:
            log(f"{node}: no closed session with an activity stamp")
            continue
        log(f"{node}: {best['session'][:8]} "
            f"(activity {best['last_activity_utc']}, frames {best.get('frames')})")
        targets.append({"node": node, "kind": "session", "id": best["session"]})
    if not targets:
        raise SystemExit("[bench] no node had a closed session to collect")

    # ONE WORKFLOW, A STEP PER NODE. The transfers are this stage's long pole and they used
    # to run one after the other because the service moved the bytes itself -- 336 and 361 MB
    # landing at 18:23 and 18:30 on 2026-10-18. In pods of their own they go at once, and
    # each writes its own record into the flight's collected/, so nothing has to be merged.
    sub = api(f"/flights/{flight}/collect", method="POST",
              body={"targets": targets}, timeout=180)
    log(f"collect {sub['workflow']}")
    log(f"  {sub['url']}")
    watch_workflow(sub["workflow"], "collect")
    log(f"collected into {flight}; read each session's manifests/ FIRST to see which image "
        "wrote it, then journal.log, then the sidecars")


STAGES = ("deploy", "arm", "disarm", "collect")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=STAGES, help="which step of the workflow to run")
    ap.add_argument("--flight", help="flight directory name; required by `collect`")
    ap.add_argument("--ssh-key", default=os.environ.get("FLEET_SSH_KEY", "/secrets/ssh/id"),
                    help="key that reaches the coordinator, for the arm file only")
    args = ap.parse_args()

    # WHICH BUILD IS RUNNING, first line, the way run-playbook.sh prints `[step] driver`.
    # bench-simflight-g4pd2 failed printing an error message the fix under test had already
    # replaced: the step image's tag floats, the node had a cached one, and nothing in the
    # run record said which copy ran. A version that has to be deduced from the wording of
    # an error message is not recorded at all.
    rev = build_revision()
    log(f"stage {args.stage}, from {rev or 'no image manifest -- a local run'}")

    if args.stage == "deploy":
        stage_deploy()
    elif args.stage == "arm":
        stage_arm(args.ssh_key)
    elif args.stage == "disarm":
        stage_disarm(args.ssh_key)
    elif args.stage == "collect":
        if not args.flight:
            ap.error("collect needs --flight")
        stage_collect(args.flight)
    return 0


if __name__ == "__main__":
    sys.exit(main())
