"""Tests for _apply_focus. Run: python3 containers/campod-camera/test_apply_focus.py

The case that matters: a module whose lens is mechanically fixed advertises no
LensPosition control, and must not be treated as a failure.
"""
import importlib.util
import sys
import types

# Neither picamera2 nor libcamera is installed off-device. Stubbed the same way
# test_capture_wait.py does it, so this runs on a build machine.
_pc = types.ModuleType("picamera2")
_pc.Picamera2 = type("Picamera2", (), {"global_camera_info": staticmethod(lambda: [])})
sys.modules["picamera2"] = _pc
_lc = types.ModuleType("libcamera")
_lc.controls = types.SimpleNamespace(AfModeEnum=types.SimpleNamespace(Manual=0))
sys.modules["libcamera"] = _lc

_spec = importlib.util.spec_from_file_location("capture", "capture.py")
capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capture)

FAILED = []


def check(name, fn):
    try:
        fn()
        print(f"  ok   {name}")
    except AssertionError as e:
        FAILED.append(name)
        print(f"  FAIL {name}: {e}")


class FakeCam:
    def __init__(self, controls):
        self.camera_controls = controls
        self.applied = None

    def set_controls(self, c):
        unknown = set(c) - set(self.camera_controls)
        if unknown:
            # picamera2 rejects controls the camera does not advertise.
            raise RuntimeError(f"Control(s) {unknown} not advertised by libcamera")
        self.applied = c


VCM = {"LensPosition": (0.0, 32.0, 1.0), "AfMode": (0, 2, 0)}
FIXED = {"ExposureTime": (1, 100000, 20000)}


def test_vcm_module_gets_pinned():
    cam = FakeCam(VCM)
    assert capture._apply_focus(cam, "infinity") == 0.0
    assert cam.applied["LensPosition"] == 0.0, cam.applied


def test_vcm_module_honours_a_dioptre_value():
    cam = FakeCam(VCM)
    assert capture._apply_focus(cam, "0.8") == 0.8
    assert cam.applied["LensPosition"] == 0.8


def test_fixed_lens_module_does_not_raise():
    """The regression. Setting LensPosition on a fixed-lens module killed startup."""
    cam = FakeCam(FIXED)
    out = capture._apply_focus(cam, "infinity")
    assert out == "mechanically-fixed", out
    assert cam.applied is None, "nothing should have been set on a fixed lens"


def test_fixed_lens_ignores_a_configured_focus_value():
    """A stale CAMPOD_STILL_FOCUS must not fail a fixed-lens module either."""
    cam = FakeCam(FIXED)
    assert capture._apply_focus(cam, "0.8") == "mechanically-fixed"


def test_a_movable_lens_that_cannot_be_set_is_still_fatal():
    """Not softened: if the module HAS a lens and pinning it fails, that is fatal.

    Every frame of a survey is taken at whatever focus this sets, so a movable lens at
    an unknown position is a reason not to fly. Only the no-lens case is benign.
    """
    class Refuses(FakeCam):
        def set_controls(self, c):
            raise RuntimeError("device busy")

    try:
        capture._apply_focus(Refuses(VCM), "infinity")
    except RuntimeError:
        return
    raise AssertionError("a movable lens that refuses to be pinned must raise")


def test_missing_camera_controls_is_treated_as_no_lens():
    """Some stacks report camera_controls as None before configure()."""
    cam = FakeCam(None)
    assert capture._apply_focus(cam, "infinity") == "mechanically-fixed"


for name, fn in sorted((k, v) for k, v in list(globals().items()) if k.startswith("test_")):
    check(name, fn)
print(f"\nRESULT: {'FAIL' if FAILED else 'PASS'} ({len(FAILED)} failed)" if FAILED
      else "\nRESULT: PASS (all)")
sys.exit(1 if FAILED else 0)
