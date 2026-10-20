#!/usr/bin/env python3
"""Who paces whom, derived from one fleet-wide leader name.

The deployment can only give every pod the same value -- stacks/campod/ is one file copied
to each pod and a converge passes only device_role -- so the server/client split has to come
from each pod comparing that value against its own name.
"""
import importlib.util
import sys
import types
from pathlib import Path

# picamera2 and libcamera are stubbed, the same way test_capture_wait.py does it and for
# the same reason: the real import is the expensive thing and there is no camera on a
# builder. This test needs neither -- _sync_mode_for is pure.
_picam = types.ModuleType("picamera2")
_picam.Picamera2 = type("Picamera2", (), {})
# capture.py maps the camera buffer rather than copying per frame, so it imports
# MappedArray; the stub only has to satisfy the import.
_picam.MappedArray = type("MappedArray", (), {})
sys.modules["picamera2"] = _picam
_libcamera = types.ModuleType("libcamera")
_libcamera.controls = types.SimpleNamespace()
sys.modules["libcamera"] = _libcamera
# NOT STUBBED. capture.py encodes with simplejpeg, so the test should check the call
# against the real encoder -- argument order and colorspace string included. It is
# python3-simplejpeg in the camera image, where these run during the build, and
# simplejpeg in the notebook image for running them by hand.

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("capture_sync", HERE / "capture.py")
cap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cap)

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


f = cap._sync_mode_for

# The deployed value, on each of the four pods in turn.
check("the named pod is the server", f("campod-sw", "campod-sw") == "server")
check("every other pod is a client",
      [f("campod-sw", n) for n in ("campod-se", "campod-ne", "campod-nw")]
      == ["client", "client", "client"])

# No leader is a configuration error, not a mode. There is no unsynced capture -- a lone
# pod names itself and runs as a server with no followers -- and returning "off" here used
# to look like a supported setup while nothing paced anything.
for empty in ("", None):
    try:
        f(empty, "campod-sw")
        check(f"no leader ({empty!r}) is refused", False, "returned a mode")
    except ValueError:
        check(f"no leader ({empty!r}) is refused", True)

# A leader that is not in the fleet leaves everybody a client. That is a real
# misconfiguration -- a typo in one shared value disables pacing for the whole fleet --
# which is why capture.py logs the leader AND what this pod resolved to.
check("a misspelled leader leaves nobody pacing",
      f("campod-sww", "campod-sw") == "client",
      "capture.py must log the resolution so this is visible")

# Exact match, not a prefix or a substring: campod-s must not make campod-sw the server.
check("the match is exact", f("campod-s", "campod-sw") == "client")
check("and not a suffix either", f("sw", "campod-sw") == "client")

# ---- the pinned frame duration ------------------------------------------------------
#
# The sync algorithm needs min == max. libcamera clamps whatever it is given into the
# sensor mode's range and publishes the validated pair back, so a value outside that range
# silently becomes something else -- which is why _pin_frame_duration reads the achievable
# range and RETURNS what it got rather than what was asked.


class _FakeCam:
    def __init__(self, lo, hi):
        self.camera_controls = {"FrameDurationLimits": (lo, hi, lo)}
        self.applied = None

    def set_controls(self, c):
        self.applied = c


class _NoLimitsCam:
    camera_controls = {}

    def set_controls(self, c):  # pragma: no cover - must not be reached
        raise AssertionError("set_controls called with no FrameDurationLimits to clamp to")


LO, HI = 69669, 220535845  # measured on campod-se at 4608x2592

for label, lo, hi, want, expect in (
    ("5 fps is inside this mode", LO, HI, 200_000, 200_000),
    ("faster than the mode allows clamps up", LO, HI, 1_000, LO),
    ("slower than the mode allows clamps down", LO, 100_000, 200_000, 100_000),
):
    cam = _FakeCam(lo, hi)
    got = cap._pin_frame_duration(cam, want)
    check(f"{label}", got == expect, f"got {got}, want {expect}")
    # MIN AND MAX THE SAME VALUE is the whole point; a range here is the bug being fixed.
    check(f"{label}: set as a single value",
          cam.applied == {"FrameDurationLimits": (expect, expect)}, str(cam.applied))

# A camera that does not report the range is reported, not guessed at: pinning blind could
# land outside the mode and be clamped to something nobody chose.
check("no reported range means no pin, and a warning",
      cap._pin_frame_duration(_NoLimitsCam(), 200_000) is None)

# NOT checked here: that the quadlet asks for 5 fps. This file runs at IMAGE BUILD time,
# where the context is containers/campod-camera only and stacks/ does not exist -- so that
# assertion lives in device-sw/tests/test_quadlet_units.py, which walks the quadlets from
# the repo. Same trap caught the same way in coordinator#499.

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_sync_leader: all checks passed")
