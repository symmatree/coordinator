#!/usr/bin/env python3
"""Tests for motors.py. No NAS, no pytest: `python3 analysis/test_motors.py`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motors

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


# rekon10's export: outputs 1-4 are Motor1, Motor3, Motor4, Motor2
PARMS = {"SERVO1_FUNCTION": 33.0, "SERVO2_FUNCTION": 35.0, "SERVO3_FUNCTION": 36.0, "SERVO4_FUNCTION": 34.0,
         "SERVO9_FUNCTION": 0.0, "SERVO13_FUNCTION": 120.0}

m = motors.esc_motors(PARMS)
check("ESC index -> motor goes through SERVOn_FUNCTION", m == {0: "Motor1", 1: "Motor3", 2: "Motor4", 3: "Motor2"}, m)

labels = motors.esc_labels(PARMS)
check("ESC index 1 is NW, not 'motor 2'", labels[1] == "NW (front-left)", labels)
check("ESC index 2 is SE (rear-right)", labels[2] == "SE (rear-right)", labels)

# the front pair is outputs 1 + 2, which is ESC indices 0 + 1 -- not 0 + 2 (a diagonal)
front = {i for i, mot in m.items() if motors.CORNER[mot] in motors.FRONT}
check("front pair is ESC indices 0 and 1", front == {0, 1}, front)
diag = {i for i, mot in m.items() if motors.CORNER[mot] in motors.CCW_DIAGONAL}
check("CCW diagonal is ESC indices 0 and 3", diag == {0, 3}, diag)
check("same-spin pairs are the diagonals", {motors.SPIN[x] for x in ("Motor1", "Motor2")} == {"CCW"}
      and {motors.SPIN[x] for x in ("Motor3", "Motor4")} == {"CW"})

sys.exit(1 if failures else 0)
