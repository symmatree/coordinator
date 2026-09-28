"""Synthetic tests for pose_calibration.py. Run: python3 analysis/test_pose_calibration.py

Each pins a way the answer can be confidently wrong, in the style of test_ringdown.py.
"""
import sys
import numpy as np
import pose_calibration as pc

FAILED = []


def check(name, fn):
    try:
        fn()
        print(f"  ok   {name}")
    except AssertionError as e:
        FAILED.append(name)
        print(f"  FAIL {name}: {e}")


def _poses(n=7, seed=0):
    """n well-separated unit gravity directions."""
    rng = np.random.default_rng(seed)
    base = np.array([[0, 0, -1.], [0, 0, 1.], [1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [1, 1, 1]])
    v = base[:n] + rng.normal(0, 0.05, (n, 3))
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_recovers_scale_and_bias():
    true_s = np.array([0.97, 1.02, 0.99]); true_b = np.array([0.03, -0.02, 0.05])
    raw = _poses() / true_s + true_b
    out = pc.fit_scale_bias(raw)
    assert np.allclose(out["scale"], true_s, atol=1e-6), out["scale"]
    assert np.allclose(out["bias"], true_b, atol=1e-6), out["bias"]


def test_six_poses_is_exactly_determined_and_says_so():
    out = pc.fit_scale_bias(_poses(6))
    assert out["exactly_determined"], "six poses and six parameters must be flagged"
    assert out["rms_g"] < 1e-9, "an exactly determined fit has no residual to report"


def test_a_seventh_pose_leaves_a_real_residual_when_the_model_is_wrong():
    # cross-axis coupling is NOT in the diagonal model, so it must show up
    v = _poses(7)
    m = np.array([[1, 0.02, 0], [0, 1, 0.03], [0.01, 0, 1.]])
    out = pc.fit_scale_bias(v @ m.T)
    assert out["rms_g"] > 1e-4, f"cross-axis error vanished into the fit: {out['rms_g']}"


def test_recovers_relative_rotation():
    th = np.radians(94.0)
    r = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1.]])
    a = _poses()
    out = pc.relative_rotation(a, a @ r.T)
    assert abs(out["angle_deg"] - 94.0) < 1e-6, out["angle_deg"]
    # 1e-4 deg, not 1e-9: this is an SVD on floats, and the measurement it will be compared
    # against has a standard error near 0.02 deg.
    assert out["rms_deg"] < 1e-4, out["rms_deg"]


def test_rotation_residual_detects_a_non_rigid_pair():
    """One pose perturbed about an axis PERPENDICULAR to it, which is the part that matters.

    An earlier version of this test rotated pose 3 about x while pose 3 lay along x, so the
    perturbation did nothing and the test passed a detector that could not detect. Rotating a
    vector about itself is a no-op, so a test of a rotation residual has to choose its axis.
    """
    a = _poses()
    b = a.copy()
    v = b[4]
    axis = np.cross(v, [0, 0, 1.0])
    axis /= np.linalg.norm(axis)
    th = np.radians(3.0)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rot = np.eye(3) + np.sin(th) * k + (1 - np.cos(th)) * (k @ k)
    moved = rot @ v
    assert np.degrees(np.arccos(np.clip(v @ moved, -1, 1))) > 2.9, "the perturbation must move it"
    b[4] = moved
    out = pc.relative_rotation(a, b)
    assert out["max_deg"] > 0.5, f"a moved sensor left no trace: {out['max_deg']}"


def test_centre_window_avoids_the_edges():
    # a 20 s span must yield a window centred in it, not starting at its first second
    w = pc.centre_windows([(100, 119)], window_s=8.0)
    assert len(w) == 1
    lo, hi = w[0]
    assert abs((lo + hi) / 2 - 109.5) < 1e-9, w
    assert lo > 100 + 3, f"window starts too near the leading edge: {w}"


def test_span_shorter_than_the_window_is_dropped_not_stretched():
    assert pc.centre_windows([(100, 104)], window_s=8.0) == []


class _Cap:
    """Minimal stand-in for AccelCapture: a sequence of held orientations."""
    def __init__(self, dirs, hold_s=20, rate=100.0, noise=0.02, seed=1):
        rng = np.random.default_rng(seed)
        n = int(hold_s * rate)
        xs, ys, zs = [], [], []
        for d in dirs:
            xs.append(d[0] + rng.normal(0, noise, n))
            ys.append(d[1] + rng.normal(0, noise, n))
            zs.append(d[2] + rng.normal(0, noise, n))
        self.x, self.y, self.z = (np.concatenate(v) for v in (xs, ys, zs))
        self.rate_hz = rate
        self.time_s = np.arange(len(self.x)) / rate
        self.magnitude = np.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)


def test_finds_each_held_orientation_once():
    dirs = [[0, 0, -1.], [1, 0, 0], [0, 1, 0]]
    spans = pc.held_spans(_Cap(dirs), tol_deg=2.0, min_s=10)
    assert len(spans) == 3, f"expected one span per orientation, got {spans}"


def test_a_noisier_but_still_held_pose_is_not_rejected():
    """The failure that motivated selecting on direction rather than on quietness."""
    quiet = _Cap([[0, 0, -1.]], hold_s=20, noise=0.01, seed=2)
    noisy = _Cap([[0, 0, -1.]], hold_s=20, noise=0.05, seed=3)
    assert len(pc.held_spans(quiet)) == 1
    assert len(pc.held_spans(noisy)) == 1, "a held but noisier pose must still be found"


for name, fn in sorted((k, v) for k, v in list(globals().items()) if k.startswith("test_")):
    check(name, fn)
print(f"\nRESULT: {'FAIL' if FAILED else 'PASS'} ({len(FAILED)} failed)"
      if FAILED else f"\nRESULT: PASS (all)")
sys.exit(1 if FAILED else 0)
