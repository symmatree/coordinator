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

  both converges together -- a play runs in its own pod, so the fleet-wide cap on playbook
    runs is gone and the deploy takes one converge's time rather than two
  a reboot after converging -- a converge installs and pulls and starts nothing, so the
    reboot is the apply step
  uptime going backwards before trusting any pod state -- the bus documents are retained,
    so a pod that is down still publishes its last reading
  both cameras `ready`, not merely present -- warmup frames are not capture frames
  and both `sync_ready` -- warmup finishing does not mean the pair is aligned, and a
    window that opens early spends its head discarding frames
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
CAMPODS = ("campod-se", "campod-sw")
ARM_FILE = "/var/lib/coordinator/ipc/fc_armed"


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


def is_fresh(pods: list[dict], uptime_before: float) -> bool:
    """Did these pods actually reboot?

    Uptime going BACKWARDS, not "a document exists". The bus documents are retained, so a
    pod that is down still publishes its last state -- a check for presence declared the
    cameras up while the pods were mid-reboot, and the run started against stale data.

    BOTH PODS, COUNTED. On 2026-10-18 this passed on one: collect reboots the coordinator
    too, the broker runs there, and the retained documents are gone until each pod
    republishes -- so campod-se came back and published while campod-sw, whose reboot had
    actually failed, was simply absent. `all()` over one pod is true, and the stage reported
    "a fresh boot on both campods: ok" while meaning one. cameras_ready counts; this did not.
    """
    if len(pods) != len(CAMPODS):
        return False
    ups = [(p.get("raw") or {}).get("as_of_boot_s") for p in pods]
    return bool(ups) and all(u is not None and u < uptime_before for u in ups)


def cameras_ready(pods: list[dict]) -> bool:
    """Both cameras past warmup AND synchronised, which are two different waits.

    `ready` and not merely a phase: arming during warmup produced a window whose first
    frames were warmup frames on one pod only.

    AND `sync_ready`, because warmup finishing does not mean the pair is aligned. #542 made
    a paused pod negotiate sync while idle precisely so this wait could be over before
    arming -- but the gate never looked, and got away with it until the window of
    2026-10-18T22:40 opened 18.5 s early: 38 frames on each pod recorded `sync_wait` and
    were discarded, so that run kept 232 frames where its neighbours kept 248 and 250.

    THE THREE-VALUED FIELD. `sync_ready` is null when sync is off (no leader configured),
    false while a client is still counting, true once achieved. So: all null means there is
    no sync to wait for and this must not block; anything else means wait for all of them
    to be true. A mixture -- one null, one false -- is a pod that has not evaluated yet, and
    treating that as "off" is how a gate passes early.
    """
    cams = [(p.get("raw") or {}).get("camera") or {} for p in pods]
    if len(cams) != len(CAMPODS) or not all(c.get("ready") for c in cams):
        return False
    syncs = [c.get("sync_ready") for c in cams]
    if all(s is None for s in syncs):
        return True
    return all(s is True for s in syncs)


# Failure markers, for a play that started and then failed. They are ansible's own words and
# can only appear once a play is running: `fatal: [host]: FAILED! => {...}` and
# `UNREACHABLE!`. They say NOTHING about a job that never ran -- that case produced no marker
# at all and a `status` stuck on `running` (2026-10-17, a missing WorkflowTaskResult CRD), so
# it is `watch_run`'s wall clock that catches it, not these.
_FAILURE_MARKERS = ("fatal:", "FAILED:", "UNREACHABLE")


def run_failed_line(lines: list[str]) -> str | None:
    """The last line that says the play failed, or None."""
    for ln in reversed(lines):
        if any(m in ln for m in _FAILURE_MARKERS):
            return ln.strip()
    return None


# ---- fleet interaction -----------------------------------------------------------------

def campod_pods() -> list[dict]:
    pods = api("/pods", timeout=60).get("pods", [])
    return sorted((p for p in pods if (p.get("node") or "") in CAMPODS),
                  key=lambda p: p["node"])


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


def watch_run(run_id: str, label: str, timeout_s: int = 1800) -> None:
    """Poll a run to completion. Branches on `status`; the lines are for the operator and
    for the failure marker.

    The cap is not belt-and-braces. A converge whose workflow cannot be created at all --
    on 2026-10-17, a missing resource CRD -- holds `status: running` indefinitely and
    repeats `No resources found in fleet-control namespace.`, with no `fatal:`, `FAILED:`
    or `UNREACHABLE` anywhere in it. Status plus markers cannot see that, so without a
    wall clock this polls until someone notices. This IS the bound, not a backstop behind
    one: coordinator#543 would have failed such a run in the submitter after five minutes
    and was withdrawn, because the converge it was written for did eventually run when the
    CRD landed -- so the deadline would have recorded a success as a failure.
    """
    seen = 0
    deadline = time.monotonic() + timeout_s
    while True:
        run = api(f"/runs/{run_id}", timeout=60)
        lines = [l["line"] for l in run.get("lines", [])]
        for ln in lines[seen:]:
            # The lines worth echoing whatever the path: they name what actually ran.
            if ln.startswith("[step]") or ln.startswith("[fleet-control]"):
                log(f"  {label}: {ln.strip()}")
        seen = len(lines)
        status = run.get("status")
        if status == "running":
            if time.monotonic() > deadline:
                raise SystemExit(
                    f"[bench] {label} still `running` after {timeout_s}s with no terminal "
                    f"status; last line: {(lines or ['<none>'])[-1].strip()}")
            time.sleep(20)
            continue
        bad = run_failed_line(lines)
        if status != "succeeded" or bad:
            raise SystemExit(f"[bench] {label} ended {status}: {bad or lines[-1:]}")
        log(f"{label}: succeeded")
        return


def watch_workflow(name: str, label: str, timeout_s: int = 3600) -> None:
    """Poll a workflow to completion. Branches on `phase`, which Argo owns.

    No line scanning, unlike `watch_run`: the steps' output is their pods' logs, collected
    into Loki rather than routed through the service, and a step that fails fails the
    workflow. So the phase IS the answer here, and the failure marker heuristics that
    `watch_run` needs for an in-service run have nothing to add.

    The wall clock is longer than a converge's because this moves the flight's data -- 336
    and 361 MB took about seven minutes each on 2026-10-18, and a full card is larger.
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
    before = min((p.get("raw") or {}).get("as_of_boot_s") or 0 for p in campod_pods())
    # BOTH AT ONCE. A play runs in its own pod now, so the second converge costs this
    # service a submit and a watch rather than a second ansible interpreter in its memory
    # -- the reason the fleet-wide cap existed. Submitted together, then watched in turn;
    # the second watch returns immediately if that run has already finished.
    runs = {}
    for node in CAMPODS:
        run = api(f"/nodes/{node}/converge", method="POST", timeout=120)
        runs[node] = run["id"]
        log(f"{node}: converge {run['id']}")
    for node, run_id in runs.items():
        try:
            watch_run(run_id, f"{node} converge")
        except SystemExit:
            # Submitting both means a failure here leaves the other one RUNNING and
            # unwatched -- serial never submitted it at all. Name it and say what it is
            # doing, so the operator is not left to find a converge nobody mentioned.
            for other, other_id in runs.items():
                if other == node:
                    continue
                try:
                    status = api(f"/runs/{other_id}", timeout=60).get("status")
                except SystemExit:
                    status = "unknown -- the API did not answer"
                log(f"{other} converge {other_id} is {status} and no longer watched")
            raise
    for node in CAMPODS:
        api(f"/nodes/{node}/reboot", method="POST", timeout=180)
        log(f"{node}: rebooting")
    wait_for("a fresh boot on both campods",
             lambda: is_fresh(campod_pods(), before) and campod_pods(), 900)
    wait_for("both cameras ready and synced", lambda: cameras_ready(campod_pods()), 900)
    for p in campod_pods():
        cam = (p.get("raw") or {}).get("camera") or {}
        log(f"  {p['node']}: ready at mono {cam.get('ready_at_mono_s')}s")


def stage_arm(key: str) -> None:
    # The FC talker owns the arm file, so it has to stop or it overwrites what we write.
    # On the coordinator the bus quiesce stops exactly coordinator-mavlink.
    api("/nodes/coordinator/stop-capture", method="POST", timeout=180)
    log("coordinator: FC talker stopped")
    ssh_coordinator(key, f"echo 1 | sudo tee {ARM_FILE}")
    log("armed; the window is the workflow's suspend step, so nothing of ours runs during it")


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
    before = min((p.get("raw") or {}).get("as_of_boot_s") or 0 for p in campod_pods())
    for node in ("coordinator", *CAMPODS):
        api(f"/nodes/{node}/reboot", method="POST", timeout=180)
    log("all three rebooting -- this is what makes the data durable, since the router's "
        "own disarm sync() cannot run with the router stopped")
    wait_for("a fresh boot on both campods",
             lambda: is_fresh(campod_pods(), before) and campod_pods(), 900)
    time.sleep(60)
    targets = []
    for node in CAMPODS:
        sessions = api(f"/nodes/{node}/sessions", timeout=300).get("sessions", [])
        best = pick_session(sessions)
        if not best:
            log(f"{node}: no closed session with an activity stamp")
            continue
        log(f"{node}: {best['session'][:8]} "
            f"(activity {best['last_activity_utc']}, frames {best.get('frames')})")
        targets.append({"node": node, "session": best["session"]})
    if not targets:
        raise SystemExit("[bench] no campod had a closed session to collect")

    # ONE WORKFLOW, A STEP PER CAMPOD. The transfers are this stage's long pole and they used
    # to run one after the other because the service moved the bytes itself -- 336 and 361 MB
    # landing at 18:23 and 18:30 on 2026-10-18. In pods of their own they go at once, and
    # each writes its own record into the flight's collected/, so nothing has to be merged.
    sub = api(f"/flights/{flight}/collect", method="POST",
              body={"targets": targets}, timeout=180)
    log(f"collect {sub['workflow']}")
    log(f"  {sub['url']}")
    watch_workflow(sub["workflow"], "collect")
    log(f"collected into {flight}; read manifests/campod-camera FIRST to see which image "
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
