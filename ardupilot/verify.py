#!/usr/bin/env python3
"""Verify the decomposed fragments against the ground-truth FC export.

Round-trip check: every KEY,VALUE in inputs/*.param must be present in
rekon10-methodi.param with the same value. The fragments are what we edit and apply
to the FC; the export is the last full dump off the FC. If they disagree, either a
fragment edit wasn't applied, or the FC drifted from the fragments -- either way you
want to know. Exit non-zero on any mismatch, duplicate, or fragment key missing from
the export.

Usage: python3 ardupilot/verify.py
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GROUND_TRUTH = os.path.join(HERE, "rekon10-methodi.param")
INPUTS_GLOB = os.path.join(HERE, "inputs", "*.param")


def parse_param(path):
    """Return {KEY: raw_value_str} from an ArduPilot KEY,VALUE file ('#' comments)."""
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, val = line.partition(",")
            out[key.strip()] = val.strip()
    return out


def values_equal(a, b):
    try:
        fa, fb = float(a), float(b)
        return abs(fa - fb) <= 1e-6 * max(1.0, abs(fa), abs(fb))
    except ValueError:
        return a == b


def main():
    export = parse_param(GROUND_TRUTH)

    pinned = {}          # KEY -> (value, source_file)
    errors = []
    for path in sorted(glob.glob(INPUTS_GLOB)):
        name = os.path.basename(path)
        for key, val in parse_param(path).items():
            if key in pinned:
                errors.append(f"[duplicate] {key} in both {pinned[key][1]} and {name}")
            pinned[key] = (val, name)

    for key, (val, name) in sorted(pinned.items()):
        if key not in export:
            errors.append(f"[missing] {key} ({name}) is not in the export")
        elif not values_equal(val, export[key]):
            errors.append(f"[mismatch] {key} ({name}) = {val} but export = {export[key]}")

    print(f"export   : {os.path.basename(GROUND_TRUTH)} ({len(export)} params)")
    print(f"fragments: {len(glob.glob(INPUTS_GLOB))} files pinning {len(pinned)} params")
    print()
    for e in errors:
        print("FAIL ", e)
    if errors:
        print(f"\nFAILED: {len(errors)} error(s)")
        return 1
    print("OK: every fragment value matches the export")
    return 0


if __name__ == "__main__":
    sys.exit(main())
