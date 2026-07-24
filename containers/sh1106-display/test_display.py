#!/usr/bin/env python3
"""Hardware-free test for the SH1106 status render (#115). No luma, no I2C: draw the
screen onto a PIL image and assert the render + status-gathering behave. Runs at Docker
build time, so a broken layout or import fails the build.

    python3 test_display.py
"""
import sys
import tempfile

from PIL import Image, ImageDraw

from display import draw_status, gather_status


def _render(status):
    img = Image.new("1", (128, 64), 0)
    draw_status(ImageDraw.Draw(img), 128, 64, status)
    return img


def main():
    ok = True

    # Recording state renders something (not an all-black frame).
    rec = _render({"node": "coord-a", "time": "14:32:05Z",
                   "cam": "18443010B1D8BC0800", "capturing": True, "cap_age": 2.0})
    if rec.getbbox() is None:
        print("FAIL: recording render is blank")
        ok = False

    # Idle-with-no-camera renders too (no crash on None cam / cap_age).
    idle = _render({"node": "x", "time": "00:00:00Z",
                    "cam": None, "capturing": False, "cap_age": None})
    if idle.getbbox() is None:
        print("FAIL: idle render is blank")
        ok = False

    # gather_status on an empty captures dir is safe and reports idle / no camera.
    st = gather_status(tempfile.mkdtemp(), "coord-a")
    if st["capturing"] or st["cam"] is not None:
        print(f"FAIL: empty captures should be idle/no-cam, got {st}")
        ok = False
    if st["node"] != "coord-a" or not st["time"].endswith("Z"):
        print(f"FAIL: status missing node/time, got {st}")
        ok = False

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
