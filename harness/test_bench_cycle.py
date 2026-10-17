#!/usr/bin/env python3
"""The decisions bench_cycle.py makes, tested without a fleet.

Every check here is a trial that went wrong. The script exists because the chain used to
be prose I retyped per trial; these are the specific steps that got dropped, so they are
the ones worth holding.

    python3 harness/test_bench_cycle.py
"""
import importlib.util
import sys
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

# ---- camera readiness -----------------------------------------------------------------
# Arming while a camera was still in warmup produced a window whose first frames were
# warmup frames on one pod only. `phase` being set is not enough; `ready` is the flag.
check("both ready is ready", bc.cameras_ready(
    [{"raw": {"camera": {"ready": True}}}, {"raw": {"camera": {"ready": True}}}]))
check("a camera in warmup is not ready", not bc.cameras_ready(
    [{"raw": {"camera": {"ready": True}}},
     {"raw": {"camera": {"phase": "warmup", "ready": False}}}]))
check("a camera with no document is not ready", not bc.cameras_ready(
    [{"raw": {"camera": {"ready": True}}}, {"raw": {}}]))
check("one pod reporting is not both", not bc.cameras_ready(
    [{"raw": {"camera": {"ready": True}}}]))

# ---- failure detection, in both line formats ------------------------------------------
# fleet-control renders FAILED:/UNREACHABLE: today and has no `fatal:` at all, so a grep
# for `fatal:` alone was inert. #540 switches the lines to raw ansible, which prints
# `fatal:` and `UNREACHABLE!`. Both have to match.
check("today's rendered failure is caught",
      bc.run_failed_line(["TASK Install", "  FAILED: 10.0.5.237 Install -- boom (rc=2)"])
      is not None)
check("today's rendered unreachable is caught",
      bc.run_failed_line(["  UNREACHABLE: 10.0.2.49 -- timed out"]) is not None)
check("raw ansible's fatal is caught",
      bc.run_failed_line(["fatal: [10.0.5.237]: FAILED! => {\"msg\": \"boom\"}"]) is not None)
check("raw ansible's unreachable is caught",
      bc.run_failed_line(["fatal: [10.0.2.49]: UNREACHABLE! => {}"]) is not None)
check("a clean play reports no failure",
      bc.run_failed_line(["PLAY [all]", "ok: [10.0.5.237]", "PLAY RECAP", "failed=0"]) is None)
check("the LAST failure is the one reported, not the first",
      "second" in bc.run_failed_line(["  FAILED: a first", "  FAILED: b second"]))

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
        self._boot = 9000.0

    def __call__(self, path, method="GET", timeout=300):
        self.calls.append((method, path.split("?")[0]))
        if path in _REAL_SHAPES:
            return _REAL_SHAPES[path]
        if path == "/pods":
            self._boot -= 5000.0
            return {"pods": [{"node": n, "raw": {"as_of_boot_s": max(self._boot, 20.0),
                                                 "camera": {"ready": True,
                                                            "ready_at_mono_s": 126.9}}}
                             for n in bc.CAMPODS]}
        if path.endswith("/converge") or path.endswith("/offload") or "offload?" in path:
            return {"id": "11111111-2222-3333-4444-555555555555"}
        if path.startswith("/runs/"):
            return {"status": "succeeded", "lines": [{"line": "PLAY RECAP"},
                                                     {"line": "ok=41 changed=4 failed=0"}]}
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
    return rc, fleet.calls, sent


for stage, extra in (("deploy", ()), ("arm", ()), ("disarm", ()),
                     ("collect", ("--flight", "bench-test"))):
    try:
        rc, calls, sent = _drive(stage, extra)
        check(f"stage {stage} runs end to end", rc == 0, f"returned {rc}")
    except BaseException as exc:  # SystemExit included: a stage must not bail on good input
        check(f"stage {stage} runs end to end", False, f"{type(exc).__name__}: {exc}")
        continue

    if stage == "deploy":
        check("deploy converges both pods, serially",
              [c for c in calls if c[1].endswith("/converge")]
              == [("POST", f"/nodes/{n}/converge") for n in bc.CAMPODS])
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
        check("collect offloads the chosen session for each pod",
              len([c for c in calls if "/offload" in c[1]]) == len(bc.CAMPODS))

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_bench_cycle: all checks passed")
