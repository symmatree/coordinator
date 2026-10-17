#!/usr/bin/env python3
"""Drive the bench simulated-flight cycle, start to finish, the same way every time.

The cycle runs as one unit so that its waits are not optional. docs/campod.md carries the
same sequence with the same reasons; this is the executable copy, and the one to use.

The waits that matter, and what each is for:

  fleet-control serving the newest commit touching its own paths -- a pending roll means
    the converge runs the OLD image's playbooks, and today the roll also kills the run
  the campod-camera JOB's conclusion, not the run's -- a skipped job counts as success,
    so a green run says nothing about whether the image was rebuilt (docs/ci.md)
  converges one at a time -- the service caps playbook runs at one, because each is an
    ansible-runner in its own memory and three at once OOM-killed it
  a reboot after converging -- a converge installs and pulls and starts nothing, so the
    reboot is the apply step
  uptime going backwards before trusting any pod state -- the bus documents are retained,
    so a pod that is down still publishes its last reading
  both cameras `ready`, not merely present -- warmup frames are not capture frames
  nothing touched during the window -- every command on a 417 MiB board evicts the
    camera's working set, and timing is what is under test

    harness/bench_cycle.py --flight bench-20261017-simflight-11
    harness/bench_cycle.py --flight ... --stage deploy      # just converge + reboot
    harness/bench_cycle.py --flight ... --stage window      # just arm/wait/disarm
    harness/bench_cycle.py --flight ... --stage collect     # just reboot + offload

Needs FLEET_URL (default below) and, for the window stage, an ssh key that reaches the
coordinator: the arm file has no API verb, which is why that one step touches a device.
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
CAMPODS = ("campod-se", "campod-sw")
ARM_FILE = "/var/lib/coordinator/ipc/fc_armed"


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


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          check=True).stdout.strip()


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


# Failure markers for BOTH line formats, because the run lines are changing under it.
#
# Today fleet-control renders them itself (src/ansible.ts): `  FAILED: <host> <task> --
# <msg> (rc=N)` and `  UNREACHABLE: <host> -- <msg>`. There is no `fatal:` in that
# renderer at all, so a grep for `fatal:` alone -- which is what my monitors used -- has
# been matching nothing, and failures were reaching me only through `status`.
#
# Under coordinator#540 the lines become raw ansible, which prints `fatal: [host]: FAILED!
# => {...}` and `UNREACHABLE!`. So: `fatal:` covers the new path, `FAILED:` the current
# renderer, and the bare word UNREACHABLE both. The raw-ansible shapes are from
# ground-platform's expectation rather than an observed run through that path, so they want
# confirming against a real one once it is live.
_FAILURE_MARKERS = ("fatal:", "FAILED:", "UNREACHABLE")


def run_failed_line(lines: list[str]) -> str | None:
    """The last line that says the play failed, in either format, or None."""
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


def watch_run(run_id: str, label: str) -> None:
    """Poll a run to completion. Branches on `status` only; the lines are for the operator
    and for the failure marker."""
    seen = 0
    while True:
        run = api(f"/runs/{run_id}", timeout=60)
        lines = [l["line"] for l in run.get("lines", [])]
        for ln in lines[seen:]:
            # The three lines worth echoing whatever the path: they name what actually ran.
            if ln.startswith("[step]") or ln.startswith("[fleet-control]"):
                log(f"  {label}: {ln.strip()}")
        seen = len(lines)
        status = run.get("status")
        if status == "running":
            time.sleep(20)
            continue
        bad = run_failed_line(lines)
        if status != "succeeded" or bad:
            raise SystemExit(f"[bench] {label} ended {status}: {bad or lines[-1:] }")
        log(f"{label}: succeeded")
        return


def preflight() -> None:
    """Both gates before a converge.

    A pending fleet-control roll means the converge runs the OLD image's playbooks, and
    today it also means the roll kills the run outright. A camera image that has not built
    means the converge pulls the previous one and the trial measures last week's code.
    """
    fc_rev = git("log", "-1", "--format=%H", "origin/main", "--",
                 "containers/fleet-control", "host/ansible")
    cam_rev = git("log", "-1", "--format=%H", "origin/main", "--", "containers/campod-camera")
    log(f"want fleet-control at {fc_rev[:7]}, camera image at {cam_rev[:7]}")

    def serving():
        rev = api("/build", timeout=60).get("revision", "")
        return rev == fc_rev
    wait_for(f"fleet-control serving {fc_rev[:7]}", serving, 2400, 30)

    # The COMPONENT's job, not the run's conclusion: a skipped job counts as success, so a
    # green run proves nothing about whether the camera image was rebuilt (docs/ci.md).
    out = subprocess.run(
        ["gh", "run", "list", "--workflow", "build-containers.yaml", "--limit", "15",
         "--json", "headSha,databaseId,status,conclusion"],
        capture_output=True, text=True, check=True).stdout
    runs = [r for r in json.loads(out) if r["headSha"] == cam_rev]
    if not runs:
        raise SystemExit(f"[bench] no build-containers run for {cam_rev[:7]} yet")
    if runs[0]["status"] != "completed":
        raise SystemExit(f"[bench] build for {cam_rev[:7]} is {runs[0]['status']}; wait")
    jobs = json.loads(subprocess.run(
        ["gh", "run", "view", str(runs[0]["databaseId"]), "--json", "jobs"],
        capture_output=True, text=True, check=True).stdout)["jobs"]
    cam_jobs = [j for j in jobs if "campod-camera" in j["name"]]
    if not cam_jobs or cam_jobs[0]["conclusion"] != "success":
        raise SystemExit(
            f"[bench] campod-camera job for {cam_rev[:7]} is "
            f"{cam_jobs[0]['conclusion'] if cam_jobs else 'absent'}")
    log(f"camera image for {cam_rev[:7]}: built")


def stage_deploy() -> None:
    preflight()
    before = min((p.get("raw") or {}).get("as_of_boot_s") or 0 for p in campod_pods())
    # SERIAL, because the service caps playbook runs at one: each is an ansible-runner in
    # its own memory and three at once OOM-killed it.
    for node in CAMPODS:
        run = api(f"/nodes/{node}/converge", method="POST", timeout=120)
        log(f"{node}: converge {run['id'][:8]}")
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


def ssh_coordinator(key: str, command: str) -> str:
    host = next((n["address"] for n in api("/nodes", timeout=60)["nodes"]
                 if n["name"] == "coordinator" and n.get("address")), None)
    if not host:
        raise SystemExit("[bench] the coordinator has no address in the inventory")
    return subprocess.run(
        ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
         f"pi@{host}", command],
        capture_output=True, text=True, check=True).stdout.strip()


def stage_window(key: str, window_s: int) -> None:
    # The FC talker owns the arm file, so it has to stop or it overwrites what we write.
    # On the coordinator the bus quiesce stops exactly coordinator-mavlink.
    api("/nodes/coordinator/stop-capture", method="POST", timeout=180)
    log("coordinator: FC talker stopped")
    ssh_coordinator(key, f"echo 1 | sudo tee {ARM_FILE}")
    log(f"armed; holding {window_s}s and touching NOTHING -- every command on a 417 MiB "
        "board evicts the camera's working set, and timing is what is under test")
    time.sleep(window_s)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--flight", required=True, help="flight directory name for the bundles")
    ap.add_argument("--stage", choices=("all", "deploy", "window", "collect"), default="all")
    ap.add_argument("--window-s", type=int, default=240)
    ap.add_argument("--ssh-key", default=os.path.expanduser("~/.ssh/OnePKey"),
                    help="key that reaches pi@coordinator, for the arm file only")
    args = ap.parse_args()

    if args.stage in ("all", "deploy"):
        stage_deploy()
    if args.stage in ("all", "window"):
        stage_window(args.ssh_key, args.window_s)
    if args.stage in ("all", "collect"):
        stage_collect(args.flight)
    return 0


if __name__ == "__main__":
    sys.exit(main())
