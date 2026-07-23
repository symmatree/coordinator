#!/usr/bin/env python3
"""Hardware-free test for the OAK-D still writer (coordinator #72).

Exercises the frame->disk path (`write_frame` / `build_sidecar`) with a synthetic
numpy frame -- no depthai, no OAK-D. Proves: the JPEG is written and decodes back to
the right size, the sidecar carries the timing/exposure fields, and the filename
encodes node + zero-padded seq + wall time. Runs at image build time so a broken
writer fails the build.

    python3 test_capture.py
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

import capture


def main():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        wall = dt.datetime(2026, 7, 9, 12, 34, 56, 789000, tzinfo=dt.timezone.utc)
        img = np.random.randint(0, 255, (3040, 4056, 3), dtype=np.uint8)  # 12 MP BGR
        frame_meta = {
            "sensor_timestamp_ns": 123456789,
            "device_seq": 7,
            "exposure_us": 8000,
            "iso": 200,
        }
        jpeg = capture.write_frame(td, "coord-a", 5, img, wall, 42, frame_meta, 92)

        # JPEG present + decodes to the source resolution.
        if not jpeg.exists():
            print("FAIL: no JPEG written")
            return 1
        dec = cv2.imdecode(np.frombuffer(jpeg.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if dec is None or dec.shape[:2] != img.shape[:2]:
            print(f"FAIL: JPEG decode {None if dec is None else dec.shape} != {img.shape}")
            ok = False

        # Filename: node_seq(8)_walltime.
        if jpeg.name != "coord-a_00000005_20260709T123456_789000Z.jpg":
            print(f"FAIL: unexpected filename {jpeg.name}")
            ok = False

        sidecar = json.loads(jpeg.with_suffix(".json").read_text())
        expect = {
            "node": "coord-a", "seq": 5, "file": jpeg.name,
            "sensor_timestamp_ns": 123456789, "device_seq": 7,
            "exposure_us": 8000, "iso": 200, "width": 4056, "height": 3040,
            "monotonic_ns": 42, "wall_clock_utc": "2026-07-09T12:34:56.789000Z",
        }
        for k, v in expect.items():
            if sidecar.get(k) != v:
                print(f"FAIL: sidecar[{k!r}] = {sidecar.get(k)!r} != {v!r}")
                ok = False

        # #89: durable writes are atomic -- the final JPEG + sidecar exist and no
        # ".tmp" scratch file is left behind (a crash mid-write leaves the tmp, not a
        # torn final file).
        leftover = sorted(p.name for p in Path(td).glob("*.tmp"))
        if leftover:
            print(f"FAIL: durable write left temp files: {leftover}")
            ok = False
        if not jpeg.with_suffix(".json").exists():
            print("FAIL: sidecar json not written")
            ok = False

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
