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

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_bench_cycle: all checks passed")
