"""Synthetic tests for mapping_sim. No pytest: `python3 analysis/mapping_sim/test_mapping_sim.py`.

Each pins a way the tool could hand ODM, or the scorer, something confidently wrong: a lens whose
render and recorded intrinsics disagree, a camera pointed somewhere other than its config says, a
truth surface that is not where the pixels put it, a texture that repeats, or a scorer that reports
error where there is none or misses it where there is.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import pathlib
import tempfile

import numpy as np
import yaml
from scipy.ndimage import gaussian_filter

import manifest
from lens import KannalaBrandt
from render import render, render_image
from rig import Pose, attitude_flu_enu, flown, mission, mount_rotation, rig_from_config, rot_z
from score import rodrigues, score
from scene import Box, Scene, box_ring

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


def test_many_boxes_cull_to_the_same_answer() -> None:
    # Culling by bounding sphere must not change any hit: compare one shared origin, which culls,
    # against per-ray origins that differ by nothing, which take the general path.
    cfg = {"solids": [{"kind": "plane"}, {"kind": "box_ring", "centre": [0, 0], "radius": 12, "spacing_m": 4, "size": [2, 2, 6]}]}
    scene = Scene.from_config(cfg)
    assert len(scene.solids) == 1 + len(box_ring((0, 0), 12, 4, (2, 2, 6)))
    rng = np.random.default_rng(3)
    d = rng.normal(size=(5000, 3))
    d[:, 2] = -np.abs(d[:, 2])
    o = np.tile([1.0, 2.0, 20.0], (len(d), 1))
    t1, i1, n1 = scene.intersect(o, d)
    o2 = o.copy()
    o2[0, 0] = np.nextafter(o2[0, 0], 2)
    t2, i2, n2 = scene.intersect(o2, d)
    assert (i1 > 0).sum() > 100 and np.array_equal(i1[1:], i2[1:]) and np.allclose(t1[1:], t2[1:])


def test_decal_is_a_star_on_walls_and_roof_only() -> None:
    box = Box(base=(0, 0, 0), size=(10, 8, 6), decals=True)
    fine = np.full(4, 1e-4)
    # Near a wall's centre, either side of a sector edge, the star is black or white.
    a = np.pi / Box.STAR_SECTOR_PAIRS  # one sector width in angle
    wall = np.array([[5.0, 1.5 * np.cos(a * 0.5), 3 + 1.5 * np.sin(a * 0.5)], [5.0, 1.5 * np.cos(a * 1.5), 3 + 1.5 * np.sin(a * 1.5)]])
    roof, bottom = [0.5, 0.5, 6.0], [0.5, 0.5, 0.0]
    v = box.decal(np.vstack([wall, [roof, bottom]]), fine)
    assert sorted(np.round(v[:2])) == [0, 1] and 0 <= v[2] <= 1 and np.isnan(v[3])
    # Off the star (face corner) there is no decal, and too coarse a footprint fades to grey.
    assert np.isnan(box.decal(np.array([[5.0, 3.9, 5.9]]), fine[:1]))[0]
    assert np.isclose(box.decal(wall[:1], np.array([1.0]))[0], 0.5)


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


def test_mission_follows_the_waypoints() -> None:
    # Home, a speed change, then an L: 0.001 deg of latitude north, then east, at 30 m.
    lines = [
        "QGC WPL 110",
        "0\t1\t0\t16\t0\t0\t0\t0\t40.0\t-80.0\t300.0\t1",
        "1\t0\t3\t178\t0\t3\t0\t0\t0\t0\t0\t1",
        "2\t0\t3\t16\t0\t0\t0\t0\t40.0\t-80.0\t30.0\t1",
        "3\t0\t3\t16\t0\t0\t0\t0\t40.001\t-80.0\t30.0\t1",
        "4\t0\t3\t16\t0\t0\t0\t0\t40.001\t-79.999\t30.0\t1",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "m.waypoints"
        path.write_text("\n".join(lines) + "\n")
        poses = mission({"mission": str(path), "speed_mps": 3, "capture_hz": 1})
    north = np.radians(0.001) * 6378137.0
    east = north * np.cos(np.radians(40.0))
    assert len(poses) == int((north + east) // 3) + 1
    assert np.allclose(poses[0].p_wb, [0, 0, 30]) and np.allclose(poses[1].p_wb, [0, 3, 30])
    assert np.allclose(poses[1].R_wb @ [1, 0, 0], [0, 1, 0])  # nose north on the first leg
    last = poses[-1]
    assert np.allclose(last.R_wb @ [1, 0, 0], [1, 0, 0]) and np.isclose(last.p_wb[1], north)
    assert np.isclose(last.time_s, len(poses) - 1)


def test_ardupilot_attitude_becomes_flu_in_enu() -> None:
    nose, left = [1, 0, 0], [0, 1, 0]
    assert np.allclose(attitude_flu_enu(0, 0, 0) @ nose, [0, 1, 0])  # yaw 0: nose north
    assert np.allclose(attitude_flu_enu(0, 0, 90) @ nose, [1, 0, 0])  # yaw 90: nose east (clockwise)
    assert attitude_flu_enu(0, 10, 0)[2] @ nose > 0  # positive pitch: nose up
    assert attitude_flu_enu(10, 0, 0)[2] @ left > 0  # positive roll: right wing down, left up
    R = attitude_flu_enu(7, -4, 233)
    assert np.allclose(R @ R.T, np.eye(3)) and np.isclose(np.linalg.det(R), 1)


def test_flown_path_reads_the_committed_poses() -> None:
    poses = flown({"poses": "analysis/mapping_sim/configs/flown/2026-10-30-auto.csv"})
    assert len(poses) == 318 and poses[0].time_s == 0 and poses[-1].time_s == 317
    up = np.array([p.p_wb[2] for p in poses])
    assert 20 < up.min() and up.max() < 32
    tilt = np.degrees(np.arccos([p.R_wb[2, 2] for p in poses]))
    assert tilt.max() < 30  # the crash at the end of the log is cut off


def test_manifest_records_the_config_and_every_file() -> None:
    config_path = pathlib.Path(__file__).parent / "configs" / "as-built.yaml"
    with tempfile.TemporaryDirectory() as d:
        out = pathlib.Path(d)
        started = dt.datetime.now(dt.timezone.utc)
        truth = render(CONFIG, out, scale=1 / 64, limit=2)
        m = manifest.write(out, config_path, CONFIG, truth, ["render.py"], started, dt.datetime.now(dt.timezone.utc))
        on_disk = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
        assert on_disk == sorted([*m["files"], "manifest.json", "README.md"])
        assert m["files"]["geo.txt"] == hashlib.sha256((out / "geo.txt").read_bytes()).hexdigest()
        assert m["config"]["path"] == "analysis/mapping_sim/configs/as-built.yaml" and m["config"]["contents"] == CONFIG
        assert len(m["software"]["commit"]) == 40 and m["images"]["count"] == 8
        assert m["software"]["commit"] in (out / "README.md").read_text()


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
