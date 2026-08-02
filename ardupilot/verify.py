#!/usr/bin/env python3
"""Verify the decomposed input files against the ground-truth FC export.

Two checks:
  1. Round-trip: every KEY,VALUE in inputs/*.param must be present in the
     ground-truth export with the same value. This proves the decomposition
     faithfully reflects what was on the FC, and flags any value that drifts on
     a future re-export.
  2. Coverage: every 'config'-kind override in overrides.csv (params that differ
     from the ArduCopter 4.7.0 default) must be covered by some input file --
     except a small runtime/identity allowlist. 'calibration/identity'-kind
     overrides are excluded by design (they are produced on-vehicle by
     calibration/learning and live only in the ground-truth export).

Exit non-zero if any round-trip mismatch or coverage gap is found.

Usage: python3 ardupilot/verify.py
"""
import csv
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GROUND_TRUTH = os.path.join(HERE, "rekon10-methodi.param")
INPUTS_GLOB = os.path.join(HERE, "inputs", "*.param")
OVERRIDES = os.path.join(HERE, "overrides.csv")

# 'config'-kind overrides intentionally NOT decomposed: pure runtime/identity
# state that happens to differ from the code default but is not configuration.
KNOWN_EXCLUDED = {"FORMAT_VERSION", "MIS_TOTAL"}

# Params pinned in a fragment that currently equal the 4.7.0 default (so they are
# absent from overrides.csv) but are pinned on purpose:
#   - physical / wiring facts that won't move if ArduPilot's default does: the motor
#     pole count, the RPM source, and which device sits on which serial protocol;
#   - load-bearing fusion / notch / config values we rely on holding at this number.
INTENTIONAL_AT_DEFAULT = {
    "FRAME_TYPE", "SERIAL2_PROTOCOL", "SERIAL3_PROTOCOL", "SERIAL4_PROTOCOL",
    "SERIAL8_PROTOCOL", "SERVO_BLH_POLES", "SERVO_BLH_BDMASK",
    "EK3_ALT_M_NSE", "VISO_POS_M_NSE", "VISO_VEL_M_NSE", "VISO_YAW_M_NSE",
    "INS_HNTC2_ENABLE", "INS_HNTC3_ENABLE", "INS_GYRO_RATE", "MOT_THST_EXPO",
    "FS_OPTIONS", "BATT_ESC_MASK", "BATT_LOW_MAH",
}


def parse_param(path):
    """Return {KEY: raw_value_str} from an ArduPilot KEY,VALUE file ('#' comments)."""
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sep = "," if "," in line else None
            key, _, val = line.partition(sep) if sep else (None, None, None)
            if sep is None:
                parts = line.split()
                if len(parts) != 2:
                    continue
                key, val = parts
            key, val = key.strip(), val.strip()
            out[key] = val
    return out


def values_equal(a, b):
    try:
        fa, fb = float(a), float(b)
        return abs(fa - fb) <= 1e-6 * max(1.0, abs(fa), abs(fb))
    except ValueError:
        return a == b


def main():
    export = parse_param(GROUND_TRUTH)

    inputs = {}          # KEY -> (value, source_file)
    dup = []
    for path in sorted(glob.glob(INPUTS_GLOB)):
        name = os.path.basename(path)
        for key, val in parse_param(path).items():
            if key in inputs:
                dup.append((key, inputs[key][1], name))
            inputs[key] = (val, name)

    errors, warnings = [], []

    # --- Check 1: round-trip ---
    for key, (val, name) in sorted(inputs.items()):
        if key not in export:
            errors.append(f"[round-trip] {key} ({name}) not present in export")
        elif not values_equal(val, export[key]):
            errors.append(
                f"[round-trip] {key} ({name}) = {val} but export = {export[key]}"
            )
    for key, a, b in dup:
        errors.append(f"[duplicate] {key} appears in both {a} and {b}")

    # --- Check 2: coverage ---
    config_overrides, calib_overrides = set(), set()
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES) as f:
            for row in csv.DictReader(f):
                if row["kind"] == "config":
                    config_overrides.add(row["key"])
                else:
                    calib_overrides.add(row["key"])
        for key in sorted(config_overrides):
            if key not in inputs and key not in KNOWN_EXCLUDED:
                errors.append(f"[coverage] config override {key} not in any input file")
        # Pinned lines that match the default (not an override) -- allowed, but note.
        override_keys = config_overrides | calib_overrides
        for key in sorted(inputs):
            if key not in override_keys and key not in INTENTIONAL_AT_DEFAULT:
                warnings.append(
                    f"[at-default] {inputs[key][1]} pins {key} which is not an "
                    f"override (equals the 4.7.0 default) and is not in "
                    f"INTENTIONAL_AT_DEFAULT -- intentional pin?"
                )
    else:
        warnings.append("overrides.csv missing -- coverage check skipped")

    # --- Report ---
    print(f"ground truth : {os.path.basename(GROUND_TRUTH)} ({len(export)} params)")
    print(f"input files  : {len(glob.glob(INPUTS_GLOB))}  "
          f"pinning {len(inputs)} params")
    if config_overrides:
        covered = sum(1 for k in config_overrides if k in inputs)
        print(f"coverage     : {covered}/{len(config_overrides)} config overrides "
              f"pinned; {len(KNOWN_EXCLUDED)} runtime-excluded; "
              f"{len(calib_overrides)} calibration/identity excluded by design")
    print()
    for w in warnings:
        print("WARN ", w)
    for e in errors:
        print("FAIL ", e)
    print()
    if errors:
        print(f"FAILED: {len(errors)} error(s)")
        return 1
    print("OK: decomposition round-trips and covers all config overrides")
    return 0


if __name__ == "__main__":
    sys.exit(main())
