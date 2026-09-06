#!/usr/bin/env python3
"""Synthetic-fixture tests for the analysis modules -- no NAS, no hardware.

These exist because each of the three modules produced a wrong number by hand before it was
written down: a frame-loss count off a mis-detected frame grid, a band pitch that was the
fit railing against its own bound, and a relative pose whose RANSAC threshold rejected every
distant point. Each test pins the failure mode, not just the happy path.

Run: python3 analysis/test_analysis_modules.py   (exit 0 = pass; no pytest needed)
"""

import struct
import sys
import os

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

import capture_align
import feat_stream
import local_pose
import still_banding


# ---------------------------------------------------------------- fixture builders

def _feature_row(fid, x, y, disp, fx=400.0, cx=320.0, cy=200.0):
    """One 13-double feature with consistent raw/undistorted pairs and a given disparity."""
    un_x, un_y = (x - cx) / fx, (y - cy) / fx
    return [fid, un_x, un_y, x, y, 0, 0, un_x - disp / fx, un_y, x - disp, y, 0, 0]


def write_feat(path, frames, t0=100.0, step=0.05, wall_offset=1.0e9, step_at=None, step_size=0.0):
    """Write a .feat. `frames` is a list of (device_ts_index, [feature rows])."""
    with open(path, "wb") as fh:
        for k, (idx, rows) in enumerate(frames):
            t_mono = t0 + k * step
            t_unix = wall_offset + t_mono
            if step_at is not None and t_mono >= step_at:
                t_unix += step_size
            payload = np.array([len(rows), t0 + idx * step] + [v for r in rows for v in r], dtype="<f8")
            b = payload.tobytes()
            fh.write(struct.Struct("<ddHI").pack(t_mono, t_unix, 1, len(b)))
            fh.write(b)


# ---------------------------------------------------------------- feat_stream

def test_frame_loss_counts_skipped_frames(tmp_path):  # noqa: D103
    p = tmp_path / "a.feat"
    # emitted at device indices 0,1,2, then a jump to 12 (nine frames lost), then 13,14
    write_feat(p, [(i, [_feature_row(1, 300, 200, 8)]) for i in [0, 1, 2, 12, 13, 14]])
    _, feats = feat_stream.decode(p)
    fl = feat_stream.frame_loss(feats)
    assert fl["emitted"] == 6
    assert fl["implied"] == 15
    assert fl["lost"] == 9
    assert fl["grid_residual_s"] < 1e-9  # the grid was found; the count means something


def test_frame_loss_flags_a_non_grid_stream(tmp_path):
    """If timestamps are not on a grid the residual must expose it rather than counting junk."""
    p = tmp_path / "b.feat"
    rng = np.random.default_rng(0)
    idx = np.cumsum(rng.uniform(0.4, 3.7, 40))
    write_feat(p, [(float(i), [_feature_row(1, 300, 200, 8)]) for i in idx])
    _, feats = feat_stream.decode(p)
    assert feat_stream.frame_loss(feats)["grid_residual_s"] > 1e-6


def test_intrinsics_and_triangulation_round_trip(tmp_path):
    p = tmp_path / "c.feat"
    rows = [_feature_row(i, 100 + 20 * i, 150 + 3 * i, 6.0) for i in range(12)]
    write_feat(p, [(0, rows)])
    _, feats = feat_stream.decode(p)
    fx, cx, _, _ = feat_stream.intrinsics(feats[0][2])
    assert abs(fx - 400.0) < 1e-6 and abs(cx - 320.0) < 1e-6
    _, xyz, _ = feat_stream.triangulate(feats[0][2])
    # Z = baseline * fx / disparity_px
    assert np.allclose(xyz[:, 2], feat_stream.BASELINE_M * 400.0 / 6.0)


def test_truncated_tail_is_ignored(tmp_path):
    p = tmp_path / "d.feat"
    write_feat(p, [(i, [_feature_row(1, 300, 200, 8)]) for i in range(5)])
    with open(p, "ab") as fh:
        fh.write(struct.Struct("<ddHI").pack(1.0, 1.0, 1, 4096))  # header promising bytes that follow
    _, feats = feat_stream.decode(p)
    assert len(feats) == 5


# ---------------------------------------------------------------- capture_align

def test_clock_step_is_found_and_repaired():
    mono = np.arange(0, 200, 0.5)
    wall = 1.7e9 + mono + np.where(mono >= 70, 184.288, 0.0)
    st = capture_align.clock_step(mono, wall)
    assert abs(st["step_s"] - 184.288) < 1e-6
    assert abs(st["at_monotonic_s"] - 70.0) < 0.51
    fixed = capture_align.repair_wall_clock(mono, wall)
    assert np.ptp(fixed - mono) < 1e-6  # one clock again


def test_clock_step_returns_none_when_clean():
    mono = np.arange(0, 50, 0.5)
    assert capture_align.clock_step(mono, 1.7e9 + mono) is None


def test_fc_time_fit_drops_pre_step_rows():
    fc = np.arange(0.0, 300.0, 0.1)
    co = fc - 20.0 + np.where(fc < 60, -184.288, 0.0)   # coordinator stepped at fc t=60
    fn, info = capture_align.fc_time_from_visp({"t_s": fc, "RTimeUS": co * 1e6})
    assert info["dropped_pre_step"] == 600
    assert info["residual_rms_s"] < 1e-6
    assert abs(float(fn(co[-1])) - fc[-1]) < 1e-6


# ---------------------------------------------------------------- still_banding

def _banded(pitch_rows, shape=(3040, 4032), seed=0, phase=0.0):
    """Synthetic frame: fixed scene texture, blurred periodically down the rows."""
    rng = np.random.default_rng(seed)
    scene = rng.normal(0, 40, shape).astype(np.float32) + 128
    rows = np.arange(shape[0])
    sharp = 0.5 + 0.5 * np.cos(2 * np.pi * rows / pitch_rows + phase)
    from scipy.ndimage import gaussian_filter1d
    blurred = gaussian_filter1d(scene, 3.0, axis=1)
    return (scene * sharp[:, None] + blurred * (1 - sharp[:, None])).astype(np.float32)


def test_band_pitch_recovers_a_known_pitch():
    a = _banded(900, phase=0.0)
    b = _banded(900, phase=np.pi)      # same scene, bands in antiphase
    ps, ve, _ = still_banding.band_pitch(a, b)
    pitch, r2, status = still_banding.pitch_from_periodogram(ps, ve)
    assert status == "ok"
    assert abs(pitch - 900) / 900 < 0.10
    assert r2 > 0.5


def test_pitch_at_the_search_bound_is_rejected():
    """The failure that produced a wrong cross-flight number by hand: the fit railing."""
    ps = np.linspace(300, 2600, 900)
    ve = np.linspace(0.0, 0.9, 900)          # monotonic -> peak sits in the last bin
    pitch, _, status = still_banding.pitch_from_periodogram(ps, ve)
    assert pitch is None and status == "peak_at_bound"


def test_pitch_to_hz_needs_an_explicit_readout():
    assert abs(still_banding.pitch_to_hz(900, 0.033) - (3040 / 0.033) / 900) < 1e-9


# ---------------------------------------------------------------- local_pose

def test_kabsch_recovers_a_known_transform():
    rng = np.random.default_rng(1)
    P = rng.normal(0, 5, (30, 3))
    from scipy.spatial.transform import Rotation as Rot
    R_true = Rot.from_euler("xyz", [3, -7, 11], degrees=True).as_matrix()
    t_true = np.array([0.4, -0.2, 1.1])
    R, t = local_pose.kabsch(P, P @ R_true.T + t_true)
    assert np.allclose(R, R_true, atol=1e-9) and np.allclose(t, t_true, atol=1e-9)


def test_ransac_rigid_survives_outliers():
    rng = np.random.default_rng(2)
    P = rng.normal(0, 5, (40, 3)) + np.array([0, 0, 15])
    from scipy.spatial.transform import Rotation as Rot
    R_true = Rot.from_euler("xyz", [1, 2, -1], degrees=True).as_matrix()
    Q = P @ R_true.T + np.array([0.1, 0.0, -0.05])
    Q[:8] += rng.normal(0, 5, (8, 3))                      # 20% gross outliers
    R, t, inl, rms, n = local_pose.ransac_rigid(P, Q, threshold=0.5, seed=3)
    assert inl >= 30 and rms < 0.05
    assert np.allclose(R, R_true, atol=1e-3)


def test_depth_scaled_threshold_keeps_distant_points():
    """A fixed metric threshold rejects far points; the depth-scaled one must not (T12)."""
    rng = np.random.default_rng(4)
    near = rng.normal(0, 1, (20, 3)) + np.array([0, 0, 5])
    far = rng.normal(0, 1, (20, 3)) + np.array([0, 0, 40])
    P = np.vstack([near, far])
    Q = P + np.array([0.05, 0, 0]) + rng.normal(0, 0.02, P.shape)
    tight = local_pose.ransac_rigid(P, Q, threshold=0.05, seed=5)
    scaled = local_pose.ransac_rigid(P, Q, threshold=max(0.05, 0.03 * np.median(P[:, 2])), seed=5)
    assert scaled[2] >= tight[2]


def test_integrate_never_bridges_a_gap():
    poses = ([{"ok": True, "t": float(i), "R_bearing": np.eye(3), "t_vec": np.zeros(3)} for i in range(50)]
             + [{"ok": False, "t": 50.0}]
             + [{"ok": True, "t": float(i), "R_bearing": np.eye(3), "t_vec": np.zeros(3)} for i in range(51, 101)])
    segs = local_pose.integrate(poses, min_poses=10)
    assert len(segs) == 2
    assert segs[0]["t"][-1] < segs[1]["t"][0]


# ---------------------------------------------------------------- runner

def main():
    import inspect
    import tempfile
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    with tempfile.TemporaryDirectory() as td:
        import pathlib
        tmp = pathlib.Path(td)
        for name, fn in tests:
            try:
                if "tmp_path" in inspect.signature(fn).parameters:
                    fn(tmp)
                else:
                    fn()
                print("  ok   %s" % name)
            except Exception:
                failed.append(name)
                print("  FAIL %s" % name)
                traceback.print_exc()
    print("RESULT: %s (%d/%d)" % ("PASS" if not failed else "FAIL", len(tests) - len(failed), len(tests)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
