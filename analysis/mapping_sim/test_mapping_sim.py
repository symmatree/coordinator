"""Synthetic tests for mapping_sim. No pytest: `python3 analysis/mapping_sim/test_mapping_sim.py`.

Each pins a way the tool could hand ODM, or the scorer, something confidently wrong: a lens whose
render and recorded intrinsics disagree, a camera pointed somewhere other than its config says, a
truth surface that is not where the pixels put it, a texture that repeats, or a scorer that reports
error where there is none or misses it where there is.
"""

from __future__ import annotations

import copy
import pathlib
import tempfile

import numpy as np
import yaml
from scipy.ndimage import gaussian_filter

from lens import KannalaBrandt
from render import render, render_image
from rig import Pose, mount_rotation, rig_from_config, rot_z
from score import rodrigues, score
from scene import Scene

CONFIG = yaml.safe_load((pathlib.Path(__file__).parent / "configs" / "as-built.yaml").read_text())


def test_lens_round_trip() -> None:
    lens = KannalaBrandt(400, 300, 180.0, 181.0, 203.0, 148.0, (0.05, -0.02, 0.004, -0.001))
    uv = np.stack(np.meshgrid(np.linspace(0, 400, 9), np.linspace(0, 300, 7)), -1).reshape(-1, 2)
    rays, _ = lens.unproject(uv)
    assert np.allclose(np.linalg.norm(rays, axis=1), 1)
    assert np.allclose(lens.project(rays), uv, atol=1e-6)


def test_equidistant_fov() -> None:
    lens = KannalaBrandt(4608, 2592, 2200.0, 2200.0, 2304.0, 1296.0)
    fov = lens.fov_deg()
    assert abs(fov["h"] - np.degrees(4608 / 2200)) < 1e-6
    assert abs(fov["v"] - np.degrees(2592 / 2200)) < 1e-6


def test_mount_rotation() -> None:
    down = mount_rotation(0, 90)
    assert np.allclose(down[:, 2], [0, 0, -1])
    fr = mount_rotation(45, 0)
    assert np.allclose(fr[:, 2], [np.sqrt(0.5), -np.sqrt(0.5), 0])  # front-right is -y in FLU
    assert np.allclose(fr[:, 1], [0, 0, -1])  # image down is world down when level
    for a, d, r in ((30, 50, 0), (200, 10, 15), (315, 80, -90)):
        R = mount_rotation(a, d, r)
        assert np.allclose(R.T @ R, np.eye(3)) and np.isclose(np.linalg.det(R), 1)


def test_intersections_land_on_their_surfaces() -> None:
    scene = Scene.from_config(CONFIG["scene"])
    rng = np.random.default_rng(0)
    o = np.column_stack([rng.uniform(-25, 25, 20000), rng.uniform(-25, 25, 20000), np.full(20000, 15.0)])
    d = rng.normal(size=(20000, 3))
    d[:, 2] = -np.abs(d[:, 2]) - 0.3
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    t, idx, _ = scene.intersect(o, d)
    hit = idx >= 0
    assert hit.mean() > 0.99 and (idx[hit] > 0).sum() > 100  # the solids get hit, not just the ground
    p = o[hit] + t[hit, None] * d[hit]
    assert scene.distance(p).max() < 1e-6


def test_straight_down_hits_the_box_top() -> None:
    scene = Scene.from_config({"solids": [{"kind": "plane"}, {"kind": "box", "base": [0, 0, 0], "size": [2, 2, 3], "yaw_deg": 30}]})
    t, idx, n = scene.intersect(np.array([[0.0, 0, 10], [5, 5, 10]]), np.array([[0.0, 0, -1], [0, 0, -1]]))
    assert np.allclose(t, [7, 10]) and list(idx) == [1, 0] and np.allclose(n, [[0, 0, 1], [0, 0, 1]])


def test_texture_does_not_repeat() -> None:
    scene = Scene.from_config(CONFIG["scene"])
    lens = KannalaBrandt(64, 64, 2000.0, 2000.0, 32.0, 32.0)  # narrow: a near-orthographic patch
    cams = rig_from_config({"lenses": {"l": {"width": 64, "height": 64, "focal_px": 2000.0}},
                            "cameras": [{"name": "d", "lens": "l", "azimuth_deg": 0, "depression_deg": 90}]})
    cams[0].lens = lens
    patches = [render_image(scene, cams[0], Pose(0, rot_z(0), np.array([x, 0, 10.0])))
               for x in np.arange(-20, 20, 2.0)]
    # A 0.3 m patch mostly sees a smooth swell of the metre-scale octaves, and any two swells
    # correlate; high-pass first so what is compared is the detail a feature detector would use.
    detail = [(p - gaussian_filter(p, 4.0)).ravel() for p in patches]
    z = [q / np.linalg.norm(q) for q in detail]
    corr = np.array([[a @ b for b in z] for a in z])
    off = corr[~np.eye(len(z), dtype=bool)]
    assert all(p.std() > 0.02 for p in patches)
    assert np.abs(off).max() < 0.6, np.abs(off).max()


def _synthetic_reconstruction(truth: dict, s: float, R: np.ndarray, t: np.ndarray) -> dict:
    """What OpenSfM would write if it recovered the truth exactly, in a frame related to the
    scene's by X_rec = s R X_true + t."""
    shots = {}
    for name, sh in truth["shots"].items():
        R_cw = np.array(sh["R_wc"]).T @ R.T
        C = s * R @ np.array(sh["center"]) + t
        ang = np.arccos(np.clip((np.trace(R_cw) - 1) / 2, -1, 1))
        axis = np.array([R_cw[2, 1] - R_cw[1, 2], R_cw[0, 2] - R_cw[2, 0], R_cw[1, 0] - R_cw[0, 1]])
        r = axis / np.linalg.norm(axis) * ang if np.linalg.norm(axis) > 1e-12 else np.zeros(3)
        assert np.allclose(rodrigues(r), R_cw, atol=1e-9)
        shots[name] = {"rotation": r.tolist(), "translation": (-R_cw @ C).tolist(), "camera": "c"}
    pts_true = np.array([[x, y, 0.0] for x in range(-10, 11, 4) for y in range(-10, 11, 4)])
    pts = (s * (R @ pts_true.T)).T + t
    return {"cameras": {}, "shots": shots, "points": {str(i): {"coordinates": p.tolist()} for i, p in enumerate(pts)}}


def test_score_exact_reconstruction_is_zero_error() -> None:
    with tempfile.TemporaryDirectory() as d:
        truth = render(CONFIG, pathlib.Path(d), scale=1 / 64, limit=3)
    R = mount_rotation(17, 33, 5)
    rec = _synthetic_reconstruction(truth, 0.7, R, np.array([100.0, -40, 3]))
    rep = score(truth, [rec, {"shots": {}}])
    assert rep["images"]["reconstructed"] == rep["images"]["total"] == 12
    assert abs(rep["alignment"]["scale"] - 1 / 0.7) < 1e-9
    assert rep["position_error_m"]["max"] < 1e-9
    assert rep["rotation_error_deg"]["max"] < 1e-6
    assert rep["point_to_surface_m"]["max"] < 1e-9
    assert rep["reconstructions"] == [12, 0]


def test_score_sees_a_bad_shot_and_a_missing_one() -> None:
    with tempfile.TemporaryDirectory() as d:
        truth = render(CONFIG, pathlib.Path(d), scale=1 / 64, limit=3)
    rec = _synthetic_reconstruction(truth, 1.0, np.eye(3), np.zeros(3))
    names = sorted(rec["shots"])
    del rec["shots"][names[0]]
    bad = copy.deepcopy(rec["shots"][names[5]])
    R_cw = rodrigues(np.array(bad["rotation"]))
    C = -R_cw.T @ np.array(bad["translation"]) + [0.5, 0, 0]
    bad["translation"] = (-R_cw @ C).tolist()
    rec["shots"][names[5]] = bad
    rep = score(truth, [rec])
    assert rep["images"]["missing"] == [names[0]]
    assert rep["images"]["reconstructed"] == 11
    assert 0.3 < rep["position_error_m"]["max"] < 0.6  # the similarity fit spreads a little of it


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)


if __name__ == "__main__":
    main()
