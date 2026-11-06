#!/usr/bin/env python3
"""The decisions set_rtc.py makes, tested without a coordinator.

    python3 harness/test_set_rtc.py
"""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "set_rtc", Path(__file__).resolve().parent / "set_rtc.py")
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


# ---- reading the chip -----------------------------------------------------------------
# The 2026-11-01 reading, with the hub board's chip unpowered: every register 0x00.
_DARK = """control  0x00  pin=square-wave rate=1Hz osc=running bbsqw=off alarms=none
status   0x00  osf=clear
time     2000-00-00T00:00:00"""
check("an all-zero chip is not a time", sr.chip_time(_DARK) is None)

# A fresh cell: OSF set and 2000-01-01. A real date, so the chip is answering and can be set.
_FRESH = """control  0x00  pin=square-wave rate=1Hz osc=running bbsqw=off alarms=none
status   0x88  osf=set
time     2000-01-01T00:00:07
         osf is set: the oscillator stopped, so the time above is not
         to be trusted until something writes it."""
check("a fresh cell is a time", sr.chip_time(_FRESH) == 946684807, str(sr.chip_time(_FRESH)))

check("no time line is not a time", sr.chip_time("control  0x00") is None)

# ---- the offset after -----------------------------------------------------------------
check("offset is taken at the round trip's midpoint",
      abs(sr.offset_s(100.5, 100.0, 101.0) - 0.0) < 1e-9)
check("a coordinator ahead is positive", sr.offset_s(105.0, 100.0, 100.2) > 4.8)

print(f"\n{len(failures)} failure(s)" if failures else "\nall ok")
sys.exit(1 if failures else 0)
