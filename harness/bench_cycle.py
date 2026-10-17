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

  fleet-control serving the newest commit touching its own paths -- a pending roll means
    the converge runs the OLD image's playbooks
  the campod-camera JOB's conclusion, not the run's -- a skipped job counts as success,
    so a green run says nothing about whether the image was rebuilt (docs/ci.md)
  converges one at a time -- the API caps playbook runs at one fleet-wide and returns 409
    rather than queueing, so serial is enforced, not a preference
  a reboot after converging -- a converge installs and pulls and starts nothing, so the
    reboot is the apply step
  uptime going backwards before trusting any pod state -- the bus documents are retained,
    so a pod that is down still publishes its last reading
  both cameras `ready`, not merely present -- warmup frames are not capture frames
  nothing touched during the window -- every command on a 417 MiB board evicts the
    camera's working set, and timing is what is under test

Environment, all of which the fleet-control pod already has:
  FLEET_URL             the API (default below)
  FLEET_SSH_KEY         private key that reaches pi@coordinator -- the arm stage only,
                        because the arm file has no API verb
  FLEET_SSH_USER        login account on the device (default pi)
  FLEET_GITHUB_TOKEN    optional: listing Actions runs is public, so preflight works without
                        it and uses it when present
  BENCH_REPO            which repo to resolve revisions against (default symmatree/coordinator)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

FLEET_URL = os.environ.get("FLEET_URL", "https://fleet.tiles.symmatree.com")
REPO = os.environ.get("BENCH_REPO", "symmatree/coordinator")
CAMPODS = ("campod-se", "campod-sw")
ARM_FILE = "/var/lib/coordinator/ipc/fc_armed"
BUILD_WORKFLOW = "build-containers.yaml"


def log(msg: str) -> None:
    print(f"[bench] {msg}", flush=True)


def api(path: str, method: str = "GET", timeout: int = 300):
    req = urllib.request.Request(f"{FLEET_URL}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read() or b"null")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"{method} {path} -> {exc.code}: {body}") from exc


def github(path: str, timeout: int = 60):
    """One GitHub API call. There is no git checkout and no `gh` in the step's image, and
    wanting either is what kept this script on my laptop."""
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"accept": "application/vnd.github+json"},
    )
    token = os.environ.get("FLEET_GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read() or b"null")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"GET {path} -> {exc.code}: {body}") from exc


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
    """
    ups = [(p.get("raw") or {}).get("as_of_boot_s") for p in pods]
    return bool(ups) and all(u is not None and u < uptime_before for u in ups)


def cameras_ready(pods: list[dict]) -> bool:
    """Both cameras past warmup. `ready` and not merely a phase: arming during warmup
    produced a window whose first frames were warmup frames on one pod only."""
    cams = [(p.get("raw") or {}).get("camera") or {} for p in pods]
    return len(cams) == len(CAMPODS) and all(c.get("ready") for c in cams)


def newest_revision(commits: list[dict]) -> str:
    """The sha from a `/commits?path=` listing, which GitHub returns newest first."""
    if not commits:
        raise SystemExit("[bench] no commits returned for that path")
    return commits[0]["sha"]


def build_run_for(runs: list[dict], revision: str) -> dict | None:
    """The container build for exactly this commit.

    Matched on `head_sha` AND on the workflow's own path: a head_sha has runs from every
    workflow that fires on it, and `workflow_runs[0]` is whichever finished last.
    """
    for run in runs:
        if run.get("head_sha") == revision and str(run.get("path", "")).endswith(BUILD_WORKFLOW):
            return run
    return None


def component_job(jobs: list[dict], component: str) -> dict | None:
    """That build's job for one component, or None if it never ran for this commit."""
    for job in jobs:
        if component in str(job.get("name", "")):
            return job
    return None


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
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        got = check()
        if got:
            log(f"{what}: ok")
            return got
        time.sleep(every_s)
    raise SystemExit(f"[bench] timed out waiting for {what} after {timeout_s}s")


def watch_run(run_id: str, label: str, timeout_s: int = 1800) -> None:
    """Poll a run to completion. Branches on `status`; the lines are for the operator and
    for the failure marker.

    The cap is not belt-and-braces. A converge whose workflow cannot be created at all --
    on 2026-10-17, a missing resource CRD -- holds `status: running` indefinitely and
    repeats `No resources found in fleet-control namespace.`, with no `fatal:`, `FAILED:`
    or `UNREACHABLE` anywhere in it. Status plus markers cannot see that, so without a
    wall clock this polls until someone notices. coordinator#543 bounds it in the
    submitter, which is the real fix; this stays as the guard for a direct `argo submit`,
    which gets none of that.
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


def ssh_coordinator(key: str, command: str) -> str:
    host = next((n["address"] for n in api("/nodes", timeout=60)["nodes"]
                 if n["name"] == "coordinator" and n.get("address")), None)
    if not host:
        raise SystemExit("[bench] the coordinator has no address in the inventory")
    user = os.environ.get("FLEET_SSH_USER", "pi")
    return subprocess.run(
        ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
         "-o", "StrictHostKeyChecking=accept-new", f"{user}@{host}", command],
        capture_output=True, text=True, check=True).stdout.strip()


# ---- the stages, one per workflow step --------------------------------------------------

def preflight() -> None:
    """Both gates before a converge.

    A pending fleet-control roll means the converge runs the OLD image's playbooks. A camera
    image that has not built means the converge pulls the previous one and the trial measures
    last week's code.
    """
    fc_rev = newest_revision(github(
        f"/repos/{REPO}/commits?sha=main&path=containers/fleet-control&per_page=1"))
    cam_rev = newest_revision(github(
        f"/repos/{REPO}/commits?sha=main&path=containers/campod-camera&per_page=1"))
    log(f"want fleet-control at {fc_rev[:7]}, camera image at {cam_rev[:7]}")

    def serving():
        return api("/build", timeout=60).get("revision", "") == fc_rev
    wait_for(f"fleet-control serving {fc_rev[:7]}", serving, 2400, 30)

    # The COMPONENT's job, not the run's conclusion: a skipped job counts as success, so a
    # green run proves nothing about whether the camera image was rebuilt (docs/ci.md).
    runs = github(f"/repos/{REPO}/actions/runs?head_sha={cam_rev}").get("workflow_runs", [])
    build = build_run_for(runs, cam_rev)
    if not build:
        raise SystemExit(f"[bench] no {BUILD_WORKFLOW} run for {cam_rev[:7]} yet")
    if build.get("status") != "completed":
        raise SystemExit(f"[bench] build for {cam_rev[:7]} is {build.get('status')}; wait")
    jobs = github(f"/repos/{REPO}/actions/runs/{build['id']}/jobs").get("jobs", [])
    job = component_job(jobs, "campod-camera")
    if not job or job.get("conclusion") != "success":
        raise SystemExit(f"[bench] campod-camera job for {cam_rev[:7]} is "
                         f"{job.get('conclusion') if job else 'absent'}")
    log(f"camera image for {cam_rev[:7]}: built")


def stage_deploy() -> None:
    preflight()
    before = min((p.get("raw") or {}).get("as_of_boot_s") or 0 for p in campod_pods())
    for node in CAMPODS:
        run = api(f"/nodes/{node}/converge", method="POST", timeout=120)
        log(f"{node}: converge {run['id']}")
        watch_run(run["id"], f"{node} converge")
    for node in CAMPODS:
        api(f"/nodes/{node}/reboot", method="POST", timeout=180)
        log(f"{node}: rebooting")
    wait_for("a fresh boot on both campods",
             lambda: is_fresh(campod_pods(), before) and campod_pods(), 900)
    wait_for("both cameras ready", lambda: cameras_ready(campod_pods()), 900)
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
    ssh_coordinator(key, f"echo 0 | sudo tee {ARM_FILE}")
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
    for node in CAMPODS:
        sessions = api(f"/nodes/{node}/sessions", timeout=300).get("sessions", [])
        best = pick_session(sessions)
        if not best:
            log(f"{node}: no closed session with an activity stamp")
            continue
        log(f"{node}: offloading {best['session'][:8]} "
            f"(activity {best['last_activity_utc']}, frames {best.get('frames')})")
        run = api(f"/nodes/{node}/offload?session={best['session']}&flight={flight}",
                  method="POST", timeout=180)
        watch_run(run["id"], f"{node} offload")
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
