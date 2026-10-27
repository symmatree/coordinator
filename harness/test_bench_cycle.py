#!/usr/bin/env python3
"""The decisions bench_cycle.py makes, tested without a fleet.

Every check here is a trial that went wrong. The script exists because the chain used to
be prose I retyped per trial; these are the specific steps that got dropped, so they are
the ones worth holding.

    python3 harness/test_bench_cycle.py
"""
import importlib.util
import io
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "bench_cycle", Path(__file__).resolve().parent / "bench_cycle.py")
bc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bc)

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


# ---- session choice -------------------------------------------------------------------
# Picking by file count lost a four-minute capture window to a 23-second boot that had more
# junk in it; picking by first/last_utc found nothing, because those come from sidecars and
# are null without frames or the image's build epoch before chrony's step.
_WINDOW = {"session": "aaaa", "open": False, "files": 4, "frames": 0,
           "last_utc": None, "last_activity_utc": "2026-10-17T13:13:46Z"}
_JUNK = {"session": "bbbb", "open": False, "files": 240, "frames": 120,
         "last_utc": "2026-04-13T19:38:45Z", "last_activity_utc": "2026-04-13T19:38:45Z"}
_OPEN = {"session": "cccc", "open": True, "files": 2, "frames": 0,
         "last_utc": None, "last_activity_utc": "2026-10-17T14:20:00Z"}

check("the run's own session wins over a bigger stale one",
      bc.pick_session([_JUNK, _WINDOW])["session"] == "aaaa")
check("a frameless session is still eligible",
      bc.pick_session([_WINDOW])["session"] == "aaaa")
check("the current boot's open session is never chosen",
      bc.pick_session([_WINDOW, _OPEN])["session"] == "aaaa")
check("no closed session with a stamp gives None", bc.pick_session([_OPEN]) is None)
check("an empty list gives None", bc.pick_session([]) is None)

# ---- liveness -------------------------------------------------------------------------
# Bus documents are RETAINED, so a pod that is down still publishes its last state. A check
# for presence declared the cameras up mid-reboot and the trial ran against stale data.
_UP = [{"raw": {"as_of_boot_s": 7366.0}}, {"raw": {"as_of_boot_s": 7365.0}}]
_FRESH = [{"raw": {"as_of_boot_s": 105.0}}, {"raw": {"as_of_boot_s": 100.0}}]
check("retained pre-reboot documents are not mistaken for a fresh boot",
      not bc.is_fresh(_UP, 7000.0))
check("uptime going backwards is a fresh boot", bc.is_fresh(_FRESH, 7000.0))
check("one pod still stale fails the whole check",
      not bc.is_fresh([_FRESH[0], _UP[0]], 7000.0))
check("a document with no uptime is not fresh", not bc.is_fresh([{"raw": {}}], 7000.0))
check("no documents at all is not fresh", not bc.is_fresh([], 7000.0))
# A pod that is ABSENT is not a fresh boot either, and this is how it got through: collect
# reboots the coordinator, the broker runs there, and the retained documents are gone until
# each pod republishes -- so the pod whose reboot FAILED is the one that is missing, and
# `all()` over the one that came back is true. 2026-10-18: "a fresh boot on both campods:
# ok" with campod-sw still up on its old boot and its ssh dead.
check("one pod missing is not a fresh boot, however fresh the other looks",
      not bc.is_fresh([_FRESH[0]], 7000.0))

# ---- camera readiness -----------------------------------------------------------------
# ONE FIELD, DECIDED ON THE POD. `camera.ready` is warm AND synced, combined by the campod
# (#577); this gate reads it and combines nothing. It used to assemble readiness here from
# `ready` (which then meant warm only) plus three-valued `sync_ready` -- and the front
# panel assembled it differently, which is how the panel came to show a ready pod for the
# ~80 s between warmup ending and the pair aligning.
#
# Why the wait has to be real: the window of 2026-10-18T22:40 opened 18.5 s before the
# pair was aligned, 38 frames on each pod recorded `sync_wait` and were thrown away, and
# that run kept 232 frames where its neighbours kept 248 and 250.
def _cam(ready=True):
    return {"raw": {"camera": {"ready": ready}}}


check("both ready is ready", bc.cameras_ready([_cam(), _cam()]))
check("one pod not ready is not ready", not bc.cameras_ready([_cam(), _cam(ready=False)]))
check("a camera with no document is not ready",
      not bc.cameras_ready([_cam(), {"raw": {}}]))
check("one pod reporting is not both", not bc.cameras_ready([_cam()]))
# Absent, not false: a pod running an image from before #577 publishes no `ready` at all,
# and "the field is missing" must not read as ready.
check("a missing ready field is not ready",
      not bc.cameras_ready([_cam(), {"raw": {"camera": {"warm": True}}}]))
# The halves are detail now. A gate that still looked at them would be the second
# definition this change exists to delete.
check("warm and sync_ready cannot make a pod ready on their own",
      not bc.cameras_ready([_cam(),
                            {"raw": {"camera": {"warm": True, "sync_ready": True}}}]))

# ---- every stage actually runs ---------------------------------------------------------
# The first real workflow submission died on `NameError: name 'stage_deploy' is not defined`.
# An edit had taken the `def` line with it, and because a column-0 comment does not end a
# function body, the orphaned block became dead code after the previous function's `return`
# -- so the file parsed, imported, and passed every check above, which only ever touched the
# pure helpers. The onExit step then died on `api("/nodes")["nodes"]`, indexing a bare list:
# that route serves the roster as a list and never had a "nodes" key, so that line could not
# have worked on any run.
#
# So: drive main() for each stage against a stub that answers with the SHAPES THE SERVICE
# ACTUALLY SERVES. Nothing here checks timing or policy -- only that the stage is reachable
# and that what it does with each response is possible.

# COPIED FROM THE LIVE SERVICE, not written from memory. The first version of this stub
# invented an `address` field because that is what the code read; the roster calls it `host`
# and never had `address`, so the test passed and the step failed on the device. A stub is a
# claim about another system -- if it is not taken from that system it tests nothing but my
# own assumption. `campod-ne` is in here with no host on purpose: the roster carries nodes
# that have no address at all.
_REAL_SHAPES = {
    # app.get('/nodes', async () => cfg.inventory.nodes) -- a list, not an object.
    "/nodes": [{"name": "coordinator", "host": "10.0.99.75", "role": "coordinator"},
               {"name": "campod-se", "host": "10.0.5.237", "role": "campod"},
               {"name": "campod-sw", "host": "10.0.2.49", "role": "campod"},
               {"name": "campod-ne", "role": "campod"}],
}


class _FakeFleet:
    """Records calls; answers in the service's shapes. Uptime falls on each /pods poll, so
    `is_fresh` can see a reboot, and both cameras are ready from the start."""

    def __init__(self):
        self.calls = []
        self.bodies = []
        self._boot = 9000.0

    def __call__(self, path, method="GET", timeout=300, body=None):
        self.calls.append((method, path.split("?")[0]))
        self.bodies.append(body)
        if path in _REAL_SHAPES:
            return _REAL_SHAPES[path]
        if path == "/pods":
            self._boot -= 5000.0
            return {"pods": [{"node": n, "raw": {"as_of_boot_s": max(self._boot, 20.0),
                                                 "camera": {"ready": True,
                                                            "ready_at_mono_s": 126.9}}}
                             for n in bc.CAMPODS]}
        if path.endswith("/collect"):
            return {"workflow": "collect-f-abcde", "url": "https://argo/w/collect-f-abcde",
                    "flight": "f", "targets": (body or {}).get("targets", [])}
        if path.startswith("/workflows/"):
            return {"name": path.rsplit("/", 1)[-1], "phase": "Succeeded"}
        if path.endswith("/converge"):
            # 202 {workflow, url, playbook, revision, node, action} -- a submit, nothing more.
            node = path.split("/")[2]
            return {"workflow": f"converge-{node}-abcde", "url": f"https://argo/w/converge-{node}-abcde",
                    "playbook": "deploy.yaml", "revision": "c" * 40, "node": node, "action": "converge"}
        if path.endswith("/sessions"):
            return {"sessions": [{"session": "aaaa", "open": False, "frames": 250,
                                  "last_activity_utc": "2026-10-17T18:00:00Z"}]}
        return {}


def _drive(stage, extra=()):
    """Run one stage with the fleet stubbed, ssh stubbed, and no sleeping."""
    fleet = _FakeFleet()
    sent = []
    real_api, real_sleep, real_run, real_argv = bc.api, bc.time.sleep, bc.subprocess.run, sys.argv

    class _Done:
        returncode = 0
        stdout = "1"

    def fake_run(argv, **kw):
        # The WHOLE command line: the login target is an argument too, and asserting only
        # on the trailing command is how an unreachable host goes unnoticed.
        sent.append(" ".join(argv))
        return _Done()

    bc.api, bc.time.sleep, bc.subprocess.run = fleet, lambda *_: None, fake_run
    sys.argv = ["bench-cycle", stage, *extra]
    try:
        rc = bc.main()
    finally:
        bc.api, bc.time.sleep, bc.subprocess.run, sys.argv = real_api, real_sleep, real_run, real_argv
    return rc, fleet.calls, sent, fleet.bodies


for stage, extra in (("deploy", ()), ("arm", ()), ("disarm", ()),
                     ("collect", ("--flight", "bench-test"))):
    try:
        rc, calls, sent, bodies = _drive(stage, extra)
        check(f"stage {stage} runs end to end", rc == 0, f"returned {rc}")
    except BaseException as exc:  # SystemExit included: a stage must not bail on good input
        check(f"stage {stage} runs end to end", False, f"{type(exc).__name__}: {exc}")
        continue

    if stage == "deploy":
        # Both submitted before either is watched: the fleet-wide cap on playbook runs is
        # gone, so the deploy costs one converge's wall time rather than two.
        conv = [i for i, c in enumerate(calls) if c[1].endswith("/converge")]
        first_watch = next(i for i, c in enumerate(calls) if c[1].startswith("/workflows/"))
        check("deploy converges both pods",
              [calls[i] for i in conv]
              == [("POST", f"/nodes/{n}/converge") for n in bc.CAMPODS])
        check("both converges are submitted before either is watched",
              max(conv) < first_watch, f"converges at {conv}, first watch at {first_watch}")
        check("deploy reboots both pods",
              sorted(c[1] for c in calls if c[1].endswith("/reboot"))
              == sorted(f"/nodes/{n}/reboot" for n in bc.CAMPODS))
    if stage == "arm":
        check("arm stops the FC talker before writing the file",
              ("POST", "/nodes/coordinator/stop-capture") in calls
              and calls.index(("POST", "/nodes/coordinator/stop-capture"))
              < len(calls))
        check("arm writes 1 to the arm file", any("echo 1 |" in c for c in sent), str(sent))
    if stage == "disarm":
        check("disarm writes 0 to the arm file", any("echo 0 |" in c for c in sent), str(sent))
        check("disarm reads the roster as a list, which is what /nodes serves",
              ("GET", "/nodes") in calls)
        check("it addresses the coordinator by the roster's own host field",
              any("10.0.99.75" in c for c in sent), str(sent))
    if stage == "collect":
        check("collect reboots all three",
              sorted(c[1] for c in calls if c[1].endswith("/reboot"))
              == sorted(f"/nodes/{n}/reboot" for n in ("campod-se", "campod-sw", "coordinator")))
        # ONE submission carrying BOTH campods, not one call per node: the offloads are
        # steps of a single workflow, which is what lets them run at the same time.
        submits = [c for c in calls if c[1].endswith("/collect")]
        check("collect submits one workflow for the whole flight", len(submits) == 1, str(submits))
        sent_targets = next(b for b in bodies if b and "targets" in b)["targets"]
        check("and it names the chosen session for every campod",
              sorted(t["node"] for t in sent_targets) == sorted(bc.CAMPODS)
              and all(t["kind"] == "session" and t["id"] == "aaaa" for t in sent_targets),
              str(sent_targets))
        check("then follows the workflow rather than a run of its own",
              any(c[1].startswith("/workflows/") for c in calls), str(calls))

# ---- which build is running ------------------------------------------------------------
# bench-simflight-g4pd2's step failed printing a message the fix under test had already
# replaced, and nothing in the run record said which copy of the script ran -- the image tag
# floats and the node had a cached one. The version has to be in the log, not deducible from
# the wording of an error.
_tdv = tempfile.mkdtemp(prefix="bench-manifest-")
_manifest = Path(_tdv) / "container-image"
_manifest.write_text(
    '# fleet-control build manifest, written by its Dockerfile.\n'
    'ORG_OPENCONTAINERS_IMAGE_SOURCE="https://github.com/symmatree/coordinator"\n'
    'ORG_OPENCONTAINERS_IMAGE_REVISION="5b9643c0a929fef22cb9e2e30558e8c638a93933"\n'
    'FLEET_SOURCE_REF="refs/heads/main"\n'
    'FLEET_UNIT="fleet-control"\n')

check("the build revision comes out of the image's own manifest",
      bc.build_revision(str(_manifest)) == "5b9643c0a929fef22cb9e2e30558e8c638a93933",
      str(bc.build_revision(str(_manifest))))
check("a local run has no manifest, which is not an error",
      bc.build_revision(str(Path(_tdv) / "absent")) is None)
# A local build leaves the field empty rather than omitting it (see the Dockerfile).
(Path(_tdv) / "empty").write_text('ORG_OPENCONTAINERS_IMAGE_REVISION=""\n')
check("an empty revision reads as absent rather than as a build",
      bc.build_revision(str(Path(_tdv) / "empty")) is None)
shutil.rmtree(_tdv, ignore_errors=True)

# ---- a failing poll is not a failed wait -----------------------------------------------
# bench-simflight-4mkp7 completed its window and then died in collect on
# `GET /pods -> 502: mqtt://10.0.99.75:1883: connack timeout`. collect reboots the
# coordinator, the broker runs there, so /pods is down for the middle of the stage by
# design -- and api() raises SystemExit for any HTTP error, which killed the wait.
_attempts = []


def _down_then_up():
    _attempts.append(1)
    if len(_attempts) <= 3:
        raise SystemExit("GET /pods -> 502: {\"error\":\"mqtt://10.0.99.75:1883: connack timeout\"}")
    return "back"


_real_sleep = bc.time.sleep
bc.time.sleep = lambda *_: None
try:
    check("a wait rides out the broker being down and returns when it comes back",
          bc.wait_for("the bus", _down_then_up, 60, 1) == "back", str(len(_attempts)))
    check("it polled through every failure rather than stopping at the first",
          len(_attempts) == 4, str(len(_attempts)))

    # And a wait that never comes good still fails -- carrying the reason, which is the
    # thing a bare timeout message threw away.
    def _always_down():
        raise SystemExit("GET /pods -> 502: connack timeout")

    try:
        bc.wait_for("the bus", _always_down, 2, 1)
        _timed_out = None
    except SystemExit as exc:
        _timed_out = str(exc)
    check("a wait that never succeeds times out and says what it last saw",
          _timed_out is not None and "connack timeout" in _timed_out, str(_timed_out))
finally:
    bc.time.sleep = _real_sleep

# ---- disarm has to land even on a rebooting coordinator --------------------------------
# It is also the onExit handler, and the failure it most has to survive is a cycle that died
# in `collect` -- which reboots the coordinator. 4mkp7's onExit disarm tried once, got
# `ssh: connect to host ... port 22: No route to host`, and reported the cycle as failed on
# top of the failure it was cleaning up after.
_tries = []


def _ssh_down_then_up(key, command):
    _tries.append(command)
    if len(_tries) <= 2:
        raise SystemExit("[bench] ssh pi@10.0.99.75 failed (255): No route to host")
    return "0"


_real_ssh, _real_sleep2 = bc.ssh_coordinator, bc.time.sleep
bc.ssh_coordinator, bc.time.sleep = _ssh_down_then_up, lambda *_: None
try:
    try:
        bc.stage_disarm("/secrets/ssh/id")
        _disarmed = True
    except SystemExit as exc:
        _disarmed = f"gave up: {exc}"
finally:
    bc.ssh_coordinator, bc.time.sleep = _real_ssh, _real_sleep2

check("disarm waits for a rebooting coordinator instead of failing the cycle",
      _disarmed is True, str(_disarmed))
check("and it wrote 0 to the arm file when the box came back",
      _tries and _tries[-1].startswith("echo 0 |"), str(_tries[-1:]))

# ---- a failed converge names the one still running ------------------------------------
# Submitting both before watching either means a failure on the first leaves the second
# RUNNING and unwatched; serial never submitted it. Ground-platform flagged it on #563: the
# operator should not have to discover a converge nobody mentioned.
_fail_calls = []


class _FailingFleet(_FakeFleet):
    """campod-se's run fails; campod-sw's is still running when it does."""

    def __call__(self, path, method="GET", timeout=300, body=None):
        _fail_calls.append(path)
        if path.startswith("/workflows/"):
            name = path.split("/")[2]
            if name.endswith("se"):
                return {"name": name, "phase": "Failed", "message": "step main exited 2"}
            return {"name": name, "phase": "Running"}
        if path.endswith("/converge"):
            return {"workflow": f"run-for-{path.split('/')[2]}", "url": "https://argo/w/x"}
        return super().__call__(path, method, timeout, body)


_fleet = _FailingFleet()
_buf = io.StringIO()
_real_api, _real_sleep3 = bc.api, bc.time.sleep
bc.api, bc.time.sleep = _fleet, lambda *_: None
try:
    with redirect_stdout(_buf):
        bc.stage_deploy()
    _result = "returned"
except SystemExit as exc:
    _result = str(exc)
finally:
    bc.api, bc.time.sleep = _real_api, _real_sleep3
_said = _buf.getvalue()

check("a failed converge stops the stage", _result != "returned", _result)
check("and it names the other converge and its state",
      "run-for-campod-sw is Running" in _said, _said[-300:])

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_bench_cycle: all checks passed")
