"""Score an OpenSfM reconstruction against the truth a render wrote.

    python3 analysis/mapping_sim/score.py <render_dir> <reconstruction.json> [--json out.json]

The reconstruction's world frame is not the scene's (OpenSfM works in a topocentric frame about
its own reference point), so recovered camera centres are first fitted to the true ones by a
similarity transform (Umeyama). The fitted scale and rotation are reported, not hidden: with
exact priors they should be 1 and a near-identity. Everything else is measured after that fit:

- per-image camera-centre error (m) and rotation error (deg), and which images were not
  reconstructed at all;
- each sparse point's distance to the nearest true surface (m);
- each recovered camera model against the one rendered with.

Only the largest reconstruction is scored; how many there were and their sizes are reported.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import yaml

from scene import Scene


def rodrigues(r: np.ndarray) -> np.ndarray:
    th = np.linalg.norm(r)
    if th < 1e-15:
        return np.eye(3)
    k = r / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(th) * K + (1 - np.cos(th)) * K @ K


def rotation_angle_deg(R: np.ndarray) -> float:
    """Angle of a rotation matrix. atan2 rather than arccos of the trace, which loses about
    1e-6 deg near zero -- the regime an ideal reconstruction lives in."""
    sin = np.linalg.norm([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / 2
    return float(np.degrees(np.arctan2(sin, (np.trace(R) - 1) / 2)))


def umeyama(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """s, R, t minimising |dst - (s R src + t)|^2."""
    ms, md = src.mean(0), dst.mean(0)
    a, b = src - ms, dst - md
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U @ Vt))
    R = U @ D @ Vt
    s = np.trace(np.diag(S) @ D) / (a * a).sum(1).mean()
    return s, R, md - s * R @ ms


def _stats(x: np.ndarray) -> dict:
    if len(x) == 0:
        return {"n": 0}
    return {
        "n": int(len(x)),
        "median": float(np.median(x)),
        "p90": float(np.percentile(x, 90)),
        "max": float(np.max(x)),
        "rms": float(np.sqrt(np.mean(x * x))),
    }


def score(truth: dict, recs: list[dict]) -> dict:
    sizes = sorted((len(r.get("shots", {})) for r in recs), reverse=True)
    rec = max(recs, key=lambda r: len(r.get("shots", {})))
    true_shots = truth["shots"]
    names = [n for n in rec["shots"] if n in true_shots]
    missing = sorted(set(true_shots) - set(rec["shots"]))

    R_cw_rec = {n: rodrigues(np.array(rec["shots"][n]["rotation"])) for n in names}
    C_rec = np.array([-R_cw_rec[n].T @ np.array(rec["shots"][n]["translation"]) for n in names])
    C_true = np.array([true_shots[n]["center"] for n in names])
    s, R, t = umeyama(C_rec, C_true)
    C_fit = (s * (R @ C_rec.T)).T + t

    pos_err = np.linalg.norm(C_fit - C_true, axis=1)
    rot_err = []
    for n in names:
        R_wc_fit = R @ R_cw_rec[n].T
        dR = np.array(true_shots[n]["R_wc"]).T @ R_wc_fit
        rot_err.append(rotation_angle_deg(dR))
    rot_err = np.array(rot_err)

    pts = np.array([p["coordinates"] for p in rec.get("points", {}).values()]).reshape(-1, 3)
    pts_fit = (s * (R @ pts.T)).T + t if len(pts) else pts
    scene = Scene.from_config(truth["config"]["scene"])
    pt_err = scene.distance(pts_fit) if len(pts) else np.array([])

    by_cam = {}
    for n in names:
        by_cam.setdefault(true_shots[n]["camera"], []).append(n)

    return {
        "reconstructions": sizes,
        "images": {"total": len(true_shots), "reconstructed": len(names), "missing": missing},
        "alignment": {
            "scale": float(s),
            "rotation_deg": rotation_angle_deg(R),
        },
        "position_error_m": _stats(pos_err),
        "rotation_error_deg": _stats(rot_err),
        "per_camera": {
            c: {
                "reconstructed": len(ns),
                "position_error_m": _stats(pos_err[[names.index(n) for n in ns]]),
                "rotation_error_deg": _stats(rot_err[[names.index(n) for n in ns]]),
            }
            for c, ns in sorted(by_cam.items())
        },
        "point_to_surface_m": _stats(pt_err),
        "intrinsics": {"truth": truth["cameras"], "recovered": rec.get("cameras", {})},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("render_dir", type=pathlib.Path)
    ap.add_argument("reconstruction", type=pathlib.Path)
    ap.add_argument("--json", type=pathlib.Path, default=None)
    a = ap.parse_args()
    truth = json.loads((a.render_dir / "truth.json").read_text())
    report = score(truth, json.loads(a.reconstruction.read_text()))
    text = json.dumps(report, indent=1)
    if a.json:
        a.json.write_text(text)
    print(yaml.safe_dump({k: v for k, v in report.items() if k != "intrinsics"}, sort_keys=False))


if __name__ == "__main__":
    main()
