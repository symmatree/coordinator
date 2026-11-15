"""Anchored localization: place frames against a reconstruction held fixed, then triangulate the
points the placed frames add. One run is one layer.

    python3 analysis/mapping_sim/anchored_pass.py <config.yaml> <layer dir> [--workers N]

The config names the OpenSfM run that supplies features, tracks.csv, EXIF and images.json; the
anchor reconstruction; earlier layers, read as part of the anchors; the DTM; an optional
overrides file; and the pass's constants, each with its reason.

1. Posed shots: the anchor reconstruction's shots and every earlier layer's. Anchor points: the
   anchor reconstruction's points that pass the anchor filter (enough posed views, small
   reprojection error, a plausible height above the DTM), and every earlier layer's points.
   Their observations in posed shots come from tracks.csv and the earlier layers'
   observations.json, each with its feature's descriptor.
2. A prior pose for every other image: EXIF GPS in the topocentric frame, and attitude
   C @ R(yaw, pitch) from images.json, with C the rotation fitted on the anchor shots.
3. Candidates: images whose prior sees at least `min_inliers` anchor points -- in front, in frame,
   no farther than the posed shots' 99th-percentile viewing range, and within
   `max_view_angle_deg` of the direction some posed shot saw the point from. Fewer cannot pass the inlier gate, so the image is recorded and not tried.
4. Per candidate, correspondences from tracks.csv (its features already in an anchor point's
   track) and from guided matching: each anchor point's descriptor (from the posed view nearest
   the candidate's direction) against the frame's features inside a window around the point's
   projection. The wide window comes from the anchors' own prior error; after a RANSAC pose fit,
   matching is repeated in a narrow window around the fitted pose's projections, with a
   descriptor-distance bound taken from the first fit's inliers, and the pose is fitted again.
   The anchors do not move.
5. Gates: inliers, horizontal distance from GPS, height against the anchors' GPS height offset,
   angle from the attitude prior. Then the overrides file, which can force accept or reject.
6. Triangulation: every tracks.csv track that is not an anchor point, has an observation in an
   accepted frame and at least two in posed shots is triangulated from its posed observations.
   Observations off by more than `max_reproj_px` are dropped and the point re-solved; a point is
   kept if it still has two views, one of them in this layer, and passes the ray-angle and
   height gates.

Writes into the layer directory:

- `shots.json` -- accepted frames as OpenSfM shots (world-to-camera `rotation`, `translation`,
  `camera`), each with `layer`;
- `points.json` -- new points: `coordinates`, `color`, `layer`, views, ray angle, residual;
- `observations.json` -- [image, feature, point id] for every inlier and every new point's view,
  so a later layer has them without re-matching;
- `frames.json` -- per image not already posed: tried or not, the evidence, the decision, and
  which gates failed;
- `manifest.json` -- code commit, command, config, inputs with SHA-256, derived values, counts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import multiprocessing
import os
import pathlib
import sys

os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "2")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import tifffile  # noqa: E402
import yaml  # noqa: E402
from scipy.spatial.transform import Rotation  # noqa: E402

import manifest  # noqa: E402
import opensfm_data  # noqa: E402


# --- camera ------------------------------------------------------------------------------------


class Brown:
    """OpenSfM's `brown` camera, in OpenSfM's normalised image coordinates (origin at the image
    centre, divided by the longer side). Its distortion is OpenCV's (k1, k2, p1, p2, k3)."""

    def __init__(self, cam: dict):
        self.fx, self.fy, self.cx, self.cy = cam["focal_x"], cam["focal_y"], cam["c_x"], cam["c_y"]
        self.dist = np.array([cam["k1"], cam["k2"], cam["p1"], cam["p2"], cam["k3"]])
        self.size = max(cam["width"], cam["height"])
        self.half = np.array([cam["width"], cam["height"]]) / 2 / self.size

    def distort(self, xy: np.ndarray) -> np.ndarray:
        k1, k2, p1, p2, k3 = self.dist
        x, y = xy[:, 0], xy[:, 1]
        r2 = x * x + y * y
        radial = 1 + r2 * (k1 + r2 * (k2 + r2 * k3))
        xd = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
        yd = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
        return np.stack([self.fx * xd + self.cx, self.fy * yd + self.cy], 1)

    def project(self, Xc: np.ndarray) -> np.ndarray:
        return self.distort(Xc[:, :2] / Xc[:, 2:3])

    def undistort(self, uv: np.ndarray) -> np.ndarray:
        """Normalised image coordinates -> ideal (x/z, y/z)."""
        d = ((uv - [self.cx, self.cy]) / [self.fx, self.fy]).reshape(-1, 1, 2).astype(np.float64)
        crit = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 100, 1e-12)
        return cv2.undistortPointsIter(d, np.eye(3), self.dist, None, None, crit).reshape(-1, 2)

    def in_frame(self, uv: np.ndarray) -> np.ndarray:
        return (np.abs(uv[:, 0]) <= self.half[0]) & (np.abs(uv[:, 1]) <= self.half[1])

    def px(self, d):
        """Normalised-coordinate length -> pixels."""
        return d * self.size

    def ideal_per_px(self) -> float:
        """Ideal (x/z) units per pixel, at the image centre."""
        return 1 / (self.fx * self.size)


def rot(rotvec) -> np.ndarray:
    return cv2.Rodrigues(np.asarray(rotvec, float))[0]


def rotvec(R: np.ndarray) -> list[float]:
    return cv2.Rodrigues(R)[0].ravel().tolist()


def angle_deg(Ra: np.ndarray, Rb: np.ndarray) -> float:
    return float(np.degrees(Rotation.from_matrix(Ra @ Rb.T).magnitude()))


def centre(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    return -R.T @ t


def prior_R(yaw: float, pitch: float) -> np.ndarray:
    """World (east, north, up) -> camera (x right, y down, z forward), looking along `yaw`
    (degrees from north) with elevation `pitch` - 90 degrees (a DJI gimbal pitch of -90 is
    straight down), roll 0."""
    y, el = math.radians(yaw), math.radians(pitch - 90)
    h = np.array([math.sin(y), math.cos(y), 0.0])
    up = np.array([0.0, 0.0, 1.0])
    z = math.cos(el) * h + math.sin(el) * up
    yv = -(-math.sin(el) * h + math.cos(el) * up)
    return np.stack([np.cross(yv, z), yv, z])


# --- geodesy -----------------------------------------------------------------------------------

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


def ecef(lat, lon, alt):
    lat, lon = np.radians(lat), np.radians(lon)
    n = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
    return np.stack([(n + alt) * np.cos(lat) * np.cos(lon), (n + alt) * np.cos(lat) * np.sin(lon), (n * (1 - WGS84_E2) + alt) * np.sin(lat)], -1)


def enu_basis(lat0, lon0) -> np.ndarray:
    la, lo = math.radians(lat0), math.radians(lon0)
    return np.array([[-math.sin(lo), math.cos(lo), 0], [-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la)], [math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)]])


def topocentric(lat, lon, alt, ref: dict) -> np.ndarray:
    """OpenSfM's topocentric frame: east, north, up about `ref` (reference_lla)."""
    o = ecef(ref["latitude"], ref["longitude"], ref["altitude"])
    return (ecef(lat, lon, alt) - o) @ enu_basis(ref["latitude"], ref["longitude"]).T


def lla(P: np.ndarray, ref: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X = ecef(ref["latitude"], ref["longitude"], ref["altitude"]) + P @ enu_basis(ref["latitude"], ref["longitude"])
    x, y, z = X[:, 0], X[:, 1], X[:, 2]
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - WGS84_E2))
    for _ in range(6):
        n = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
        alt = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1 - WGS84_E2 * n / (n + alt)))
    return np.degrees(lat), np.degrees(lon), alt


def utm(lat, lon, zone: int):
    """Transverse Mercator by Krueger's series (to n^3), WGS84, northern hemisphere."""
    n = WGS84_F / (2 - WGS84_F)
    A = WGS84_A / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
    al = [n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16, 13 * n**2 / 48 - 3 * n**3 / 5, 61 * n**3 / 240]
    phi, lam = np.radians(lat), np.radians(lon) - math.radians(-183 + 6 * zone)
    e = 2 * math.sqrt(n) / (1 + n)
    t = np.sinh(np.arctanh(np.sin(phi)) - e * np.arctanh(e * np.sin(phi)))
    xi, eta = np.arctan2(t, np.cos(lam)), np.arctanh(np.sin(lam) / np.sqrt(1 + t * t))
    E = eta + sum(al[j] * np.cos(2 * (j + 1) * xi) * np.sinh(2 * (j + 1) * eta) for j in range(3))
    N = xi + sum(al[j] * np.sin(2 * (j + 1) * xi) * np.cosh(2 * (j + 1) * eta) for j in range(3))
    return 500000 + 0.9996 * A * E, 0.9996 * A * N


class Dtm:
    """An ODM DTM GeoTIFF in UTM, sampled bilinearly at topocentric points."""

    def __init__(self, path: pathlib.Path, zone: int, ref: dict):
        with tifffile.TiffFile(path) as tf:
            p = tf.pages[0]
            self.z = p.asarray().astype(float)
            self.sx, self.sy, _ = p.tags["ModelPixelScaleTag"].value
            _, _, _, self.x0, self.y0, _ = p.tags["ModelTiepointTag"].value
            nod = p.tags.get("GDAL_NODATA")
            if nod is not None:
                self.z[self.z == float(nod.value.strip("\x00"))] = np.nan
        self.zone, self.ref = zone, ref

    def height_above(self, P: np.ndarray) -> np.ndarray:
        lat, lon, alt = lla(P, self.ref)
        E, N = utm(lat, lon, self.zone)
        c = (E - self.x0) / self.sx - 0.5
        r = (self.y0 - N) / self.sy - 0.5
        out = np.full(len(P), np.nan)
        ok = (c >= 0) & (r >= 0) & (c < self.z.shape[1] - 1) & (r < self.z.shape[0] - 1)
        ci, ri = np.floor(c[ok]).astype(int), np.floor(r[ok]).astype(int)
        fc, fr = c[ok] - ci, r[ok] - ri
        z = self.z
        g = z[ri, ci] * (1 - fc) * (1 - fr) + z[ri, ci + 1] * fc * (1 - fr) + z[ri + 1, ci] * (1 - fc) * fr + z[ri + 1, ci + 1] * fc * fr
        out[ok] = alt[ok] - g
        return out


# --- inputs ------------------------------------------------------------------------------------


def load_features(opensfm: pathlib.Path, image: str):
    with np.load(opensfm / "features" / f"{image}.features.npz") as f:
        return f["points"][:, :2].astype(np.float64), f["descriptors"], f["colors"]


def descriptors_u8(d: np.ndarray) -> np.ndarray:
    """ODM 3.6.2's DSP-SIFT descriptors are float32 holding integers 0-255."""
    assert d.min() >= 0 and d.max() <= 255 and np.all(d == np.round(d)), "descriptors are not 0-255 integers"
    return d.astype(np.uint8)


def gps_topocentric(opensfm: pathlib.Path, image: str, ref: dict) -> np.ndarray | None:
    g = json.loads((opensfm / "exif" / f"{image}.exif").read_text()).get("gps")
    if not g or "latitude" not in g:
        return None
    return topocentric(np.array(g["latitude"]), np.array(g["longitude"]), np.array(g["altitude"]), ref)


# --- matching and pose -------------------------------------------------------------------------


def guided_match(pred: np.ndarray, radius: np.ndarray, adesc: np.ndarray, fuv: np.ndarray, fdesc: np.ndarray, ratio: float, max_dist: float | None, block: int = 512):
    """For each anchor (predicted position `pred`, window `radius`, both normalised; descriptor
    `adesc`), the nearest frame feature by descriptor among those inside its window, kept if it
    passes the ratio test against the second nearest inside the window (when there is one) and
    `max_dist`. A feature claimed by several anchors goes to the nearest. -> (anchor index,
    feature index, descriptor distance)."""
    F = fdesc.astype(np.float32)
    f2 = (F * F).sum(1)
    U = fuv.astype(np.float64)
    u2 = (U * U).sum(1)
    out_a, out_f, out_d = [], [], []
    for s in range(0, len(pred), block):
        A = adesc[s : s + block].astype(np.float32)
        d2 = (A * A).sum(1)[:, None] + f2[None, :] - 2 * A @ F.T
        Q = pred[s : s + block]
        p2 = (Q * Q).sum(1)[:, None] + u2[None, :] - 2 * Q @ U.T
        d2[p2 > (radius[s : s + block, None] ** 2)] = np.inf
        if d2.shape[1] < 2:
            continue
        two = np.partition(d2, 1, axis=1)[:, :2]
        best = np.argmin(d2, axis=1)
        b, second = np.sqrt(np.maximum(two[:, 0], 0)), np.sqrt(np.maximum(two[:, 1], 0))
        ok = np.isfinite(b) & ((~np.isfinite(second)) | (b <= ratio * second))
        if max_dist is not None:
            ok &= b <= max_dist
        else:
            ok &= np.isfinite(second)
        idx = np.nonzero(ok)[0]
        out_a.append(idx + s)
        out_f.append(best[idx])
        out_d.append(b[idx])
    if not out_a:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    a, f, d = np.concatenate(out_a), np.concatenate(out_f), np.concatenate(out_d)
    order = np.argsort(d)
    _, first = np.unique(f[order], return_index=True)
    keep = order[first]
    return a[keep], f[keep], d[keep]


def fit_pose(X: np.ndarray, xy: np.ndarray, thr_ideal: float, origin: np.ndarray):
    """RANSAC absolute pose, then Levenberg-Marquardt on the inliers. X world, xy ideal
    coordinates. -> (R, t world-to-camera, inlier mask) or None."""
    if len(X) < 6:
        return None
    obj = (X - origin).astype(np.float64)
    ok, rv, tv, inl = cv2.solvePnPRansac(obj, xy.astype(np.float64), np.eye(3), None, iterationsCount=20000, reprojectionError=thr_ideal, confidence=0.9999, flags=cv2.SOLVEPNP_AP3P)
    if not ok or inl is None or len(inl) < 6:
        return None
    inl = inl.ravel()
    rv, tv = cv2.solvePnPRefineLM(obj[inl], xy[inl].astype(np.float64), np.eye(3), None, rv, tv)
    R = rot(rv.ravel())
    t = tv.ravel() - R @ origin
    Xc = X @ R.T + t
    res = np.linalg.norm(Xc[:, :2] / Xc[:, 2:3] - xy, axis=1)
    return R, t, (Xc[:, 2] > 0) & (res <= thr_ideal)


# --- the pass ----------------------------------------------------------------------------------

G: dict = {}  # shared with the worker processes by fork


def visible(R: np.ndarray, c: np.ndarray, p: dict):
    """Anchor points the pose (R, c) could see: (point indices, predicted normalised positions,
    depths, index of the posed observation nearest in direction)."""
    X, cam = G["X"], G["cam"]
    Xc = (X - c) @ R.T
    rng = np.linalg.norm(X - c, axis=1)
    front = (Xc[:, 2] > 0.1) & (rng <= G["derived"]["anchor_view_range_p99_m"])
    idx = np.nonzero(front)[0]
    uv = cam.project(Xc[idx])
    inf = cam.in_frame(uv)
    idx, uv = idx[inf], uv[inf]
    # nearest posed view in direction, per point (observations are sorted by point)
    to_c = c[None, :] - X[idx]
    to_c /= np.linalg.norm(to_c, axis=1, keepdims=True)
    start, count = G["obs_start"][idx], G["obs_count"][idx]
    rep = np.repeat(np.arange(len(idx)), count)
    oi = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count) + np.repeat(start, count)
    cosang = (G["obs_dir"][oi] * to_c[rep]).sum(1)
    best_cos = np.full(len(idx), -2.0)
    np.maximum.at(best_cos, rep, cosang)
    is_best = cosang >= best_cos[rep] - 1e-12
    best_obs = np.zeros(len(idx), int)
    best_obs[rep[is_best]] = oi[is_best]
    ok = best_cos >= math.cos(math.radians(p["max_view_angle_deg"]))
    return idx[ok], uv[ok], Xc[idx[ok], 2], best_obs[ok]


def locate(image: str) -> dict:
    p, cam, D = G["params"], G["cam"], G["derived"]
    rec: dict = {"image": image}
    gps = G["gps"].get(image)
    yp = G["yawpitch"].get(image)
    if gps is None or yp is None:
        rec.update(tried=False, reason="no GPS or no yaw/pitch")
        return rec
    R0 = G["C"] @ prior_R(*yp)
    idx, uv, depth, best_obs = visible(R0, gps, p)
    rec["visible_anchor_points"] = int(len(idx))
    if len(idx) < p["min_inliers"]:
        rec.update(tried=False, reason=f"prior sees {len(idx)} anchor points, fewer than min_inliers")
        return rec
    rec["tried"] = True
    fuv, fdesc, _ = load_features(G["opensfm"], image)
    fxy = cam.undistort(fuv)
    adesc = G["obs_desc"][best_obs]
    # tracks.csv: this frame's features already in an anchor point's track
    tf, tp = G["track_corr"].get(image, (np.zeros(0, int), np.zeros(0, int)))
    rec["track_correspondences"] = int(len(tf))

    def combine(a_idx, f_idx):
        pts = np.concatenate([tp, idx[a_idx]])
        fts = np.concatenate([tf, f_idx])
        _, u = np.unique(fts, return_index=True)  # a feature from tracks.csv wins (it comes first)
        return pts[u], fts[u]

    # wide window: the anchors' prior error, attitude plus position at the point's depth
    ang = math.radians(D["prior_attitude_p95_deg"]) + np.arctan(D["gps_offset_p95_m"] / np.maximum(depth, 0.1))
    radius = cam.fx * np.tan(np.minimum(ang, math.radians(60)))
    a1, f1, d1 = guided_match(uv, radius, adesc, fuv, fdesc, p["lowes_ratio"], None)
    rec["wide_window_matches"] = int(len(a1))
    pts, fts = combine(a1, f1)
    origin = gps
    fit = fit_pose(G["X"][pts], fxy[fts], p["wide_ransac_px"] * cam.ideal_per_px(), origin)
    if fit is None or fit[2].sum() < p["min_seed_inliers"]:
        rec.update(decision="rejected", failed=["no seed pose"], seed_inliers=0 if fit is None else int(fit[2].sum()))
        return rec
    R, t, inl = fit
    rec["seed_inliers"] = int(inl.sum())
    is_guided = np.isin(fts, f1)
    dl = dict(zip(f1.tolist(), d1.tolist()))
    seed_d = [dl[f] for f in fts[inl & is_guided]]
    max_d = float(np.percentile(seed_d, p["descriptor_bound_percentile"])) if len(seed_d) >= 10 else None
    rec["narrow_descriptor_bound"] = max_d
    rec["narrow_window_matches"] = None
    for _ in range(p["narrow_iterations"]):
        c = centre(R, t)
        idx, uv, depth, best_obs = visible(R, c, p)
        adesc = G["obs_desc"][best_obs]
        a2, f2, _ = guided_match(uv, np.full(len(uv), p["narrow_window_px"] / cam.size), adesc, fuv, fdesc, p["lowes_ratio"], max_d)
        pts2, fts2 = combine(a2, f2)
        fit = fit_pose(G["X"][pts2], fxy[fts2], p["max_reproj_px"] * cam.ideal_per_px(), c)
        if fit is None:
            break
        (R, t, inl), pts, fts = fit, pts2, fts2
        rec["narrow_window_matches"] = int(len(a2))
    Xc = G["X"][pts] @ R.T + t
    res_px = cam.px(np.linalg.norm(cam.project(Xc) - fuv[fts], axis=1))
    inl = inl & (res_px <= p["max_reproj_px"])
    c = centre(R, t)
    cells = np.unique((np.floor((fuv[fts[inl]] + cam.half) / (2 * cam.half) * [8, 6])).astype(int), axis=0)
    off = c - gps
    rec.update(
        inliers=int(inl.sum()),
        inliers_from_tracks=int(np.isin(fts[inl], tf).sum()),
        inlier_residual_px_median=float(np.median(res_px[inl])) if inl.any() else None,
        inlier_image_cells_of_48=int(len(cells)),
        inliers_by_layer={str(k): int(v) for k, v in zip(*np.unique(G["X_layer"][pts[inl]], return_counts=True))},
        gps_offset_horizontal_m=float(np.hypot(off[0], off[1])),
        gps_offset_up_m=float(off[2]),
        attitude_from_prior_deg=angle_deg(R, R0),
        rotation=rotvec(R),
        translation=t.tolist(),
    )
    failed = []
    if rec["inliers"] < p["min_inliers"]:
        failed.append("inliers")
    if rec["gps_offset_horizontal_m"] > p["max_gps_horizontal_m"]:
        failed.append("gps horizontal")
    if abs(off[2] - D["gps_offset_up_median_m"]) > p["max_gps_up_from_anchor_median_m"]:
        failed.append("gps height")
    if rec["attitude_from_prior_deg"] > p["max_attitude_from_prior_deg"]:
        failed.append("attitude prior")
    rec["failed"] = failed
    rec["decision"] = "accepted" if not failed else "rejected"
    rec["_inlier_obs"] = [[image, int(f), G["X_id"][q]] for f, q in zip(fts[inl], pts[inl])]
    return rec


def triangulate(posed: dict, accepted: set, cam: Brown, names, cols, skip_ids: set, dtm: Dtm, p: dict, layer: int, colors_of):
    img_name = np.array(names, dtype=object)
    im = np.asarray(cols["image"])
    trk = np.asarray(cols["track"]).astype(str)
    feat = np.asarray(cols["feature"])
    in_posed = np.array([n in posed for n in names])[im]
    in_new = np.array([n in accepted for n in names])[im]
    new_tracks = set(trk[in_new]) - skip_ids
    sel = in_posed & np.isin(trk, list(new_tracks))
    trk, im, feat = trk[sel], im[sel], feat[sel]
    uv = np.stack([np.asarray(cols["x"])[sel], np.asarray(cols["y"])[sel]], 1).astype(np.float64)
    order = np.argsort(trk, kind="stable")
    trk, im, feat, uv = trk[order], im[order], feat[order], uv[order]
    tid, ti = np.unique(trk, return_inverse=True)
    Rs = {n: rot(posed[n]["rotation"]) for n in set(img_name[im])}
    ts = {n: np.array(posed[n]["translation"]) for n in Rs}
    R = np.stack([Rs[n] for n in img_name[im]])
    t = np.stack([ts[n] for n in img_name[im]])
    C = -np.einsum("nji,nj->ni", R, t)
    xy = cam.undistort(uv)
    d = np.einsum("nji,nj->ni", R, np.c_[xy, np.ones(len(xy))])
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    new = np.array([n in accepted for n in img_name[im]])
    M = np.eye(3)[None] - d[:, :, None] * d[:, None, :]
    MC = np.einsum("nij,nj->ni", M, C)

    def solve(keep):
        """Least-squares point nearest the kept rays of each track, and every observation's
        reprojection residual (px) against it."""
        A = np.zeros((len(tid), 3, 3))
        b = np.zeros((len(tid), 3))
        np.add.at(A, ti[keep], M[keep])
        np.add.at(b, ti[keep], MC[keep])
        good = np.bincount(ti[keep], minlength=len(tid)) >= 2
        P = np.full((len(tid), 3), np.nan)
        P[good] = np.linalg.solve(A[good], b[good][..., None])[..., 0]
        Xc = np.einsum("nij,nj->ni", R, P[ti]) + t
        with np.errstate(invalid="ignore", divide="ignore"):
            res = cam.px(np.linalg.norm(cam.project(Xc) - uv, axis=1))
        res[~np.isfinite(res) | ~(Xc[:, 2] > 0)] = np.inf
        return P, res

    keep = np.ones(len(trk), bool)
    for _ in range(4):
        P, res = solve(keep)
        drop = keep & (res > p["max_reproj_px"])
        if not drop.any():
            break
        keep &= ~drop
    P, res = solve(keep)
    keep &= res <= p["max_reproj_px"]
    n_obs = np.bincount(ti[keep], minlength=len(tid))
    n_new = np.bincount(ti[keep & new], minlength=len(tid))
    max_res = np.zeros(len(tid))
    np.maximum.at(max_res, ti[keep], res[keep])
    ok = (n_obs >= 2) & (n_new >= 1) & np.all(np.isfinite(P), axis=1)
    hag = np.full(len(tid), np.nan)
    hag[ok] = dtm.height_above(P[ok])
    ok &= (hag >= p["min_height_above_dtm_m"]) & (hag <= p["max_height_above_dtm_m"])
    points, observations = {}, []
    stats = {"tracks_considered": int(len(tid)), "kept": 0}
    starts = np.r_[0, np.cumsum(np.bincount(ti, minlength=len(tid)))]
    for k in np.nonzero(ok)[0]:
        rows = np.arange(starts[k], starts[k + 1])
        rows = rows[keep[rows]]
        dd = d[rows]
        ang = float(np.degrees(np.arccos(np.clip((dd @ dd.T).min(), -1, 1))))
        if ang < p["min_ray_angle_deg"]:
            continue
        views = [img_name[im[r]] for r in rows]
        col = np.mean([colors_of(img_name[im[r]])[feat[r]] for r in rows], axis=0)
        points[tid[k]] = {
            "coordinates": P[k].tolist(),
            "color": [round(float(x), 1) for x in col],
            "layer": layer,
            "views": views,
            "max_ray_angle_deg": round(ang, 3),
            "max_residual_px": round(float(max_res[k]), 3),
            "height_above_dtm_m": round(float(hag[k]), 3),
        }
        observations += [[img_name[im[r]], int(feat[r]), tid[k]] for r in rows]
    stats["kept"] = len(points)
    return points, observations, stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config", type=pathlib.Path)
    ap.add_argument("out", type=pathlib.Path)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    started = dt.datetime.now(dt.timezone.utc)
    cfg = yaml.safe_load(args.config.read_text())
    p = cfg["constants"]
    run = pathlib.Path(cfg["run"])
    opensfm = run / "opensfm"
    args.out.mkdir(parents=True, exist_ok=False)
    layer = int(cfg["layer"])

    anchors = json.loads(pathlib.Path(cfg["anchors"]).read_text())[0]
    ref = anchors["reference_lla"]
    (cam_d,) = anchors["cameras"].values()  # one camera: the run is one DJI body
    cam = Brown(cam_d)
    dtm = Dtm(pathlib.Path(cfg["dtm"]), cfg["utm_zone"], ref)
    posed = {k: {"rotation": v["rotation"], "translation": v["translation"], "layer": 0} for k, v in anchors["shots"].items()}
    layer_points, layer_obs = {}, []
    for ld in cfg.get("layers") or []:
        ld = pathlib.Path(ld)
        posed.update(json.loads((ld / "shots.json").read_text()))
        layer_points.update(json.loads((ld / "points.json").read_text()))
        layer_obs += json.loads((ld / "observations.json").read_text())

    images = {i["filename"]: i for i in json.loads((run / "images.json").read_text())}
    print(f"{len(posed)} posed shots ({len(anchors['shots'])} anchor, {len(posed) - len(anchors['shots'])} from layers)", flush=True)

    # derived: prior attitude rotation C and the prior's error, on the anchor reconstruction's shots
    gps = {n: gps_topocentric(opensfm, n, ref) for n in images}
    yawpitch = {n: (images[n]["yaw"], images[n]["pitch"]) for n in images if images[n].get("yaw") is not None and images[n].get("pitch") is not None}
    base = [n for n in anchors["shots"] if n in yawpitch and gps.get(n) is not None]
    M = sum(rot(anchors["shots"][n]["rotation"]) @ prior_R(*yawpitch[n]).T for n in base)
    U, _, Vt = np.linalg.svd(M)
    Cfit = U @ np.diag([1, 1, np.linalg.det(U @ Vt)]) @ Vt
    att = np.array([angle_deg(rot(anchors["shots"][n]["rotation"]), Cfit @ prior_R(*yawpitch[n])) for n in base])
    offs = np.array([centre(rot(anchors["shots"][n]["rotation"]), np.array(anchors["shots"][n]["translation"])) - gps[n] for n in base])
    horiz = np.hypot(offs[:, 0], offs[:, 1])
    derived = {
        "prior_rotation_C": Cfit.tolist(),
        "prior_attitude_deg": {"shots": len(base), "median": float(np.median(att)), "p95": float(np.percentile(att, 95)), "max": float(att.max())},
        "prior_attitude_p95_deg": float(np.percentile(att, 95)),
        "gps_offset_horizontal_m": {"median": float(np.median(horiz)), "p95": float(np.percentile(horiz, 95)), "max": float(horiz.max())},
        "gps_offset_up_m": {"median": float(np.median(offs[:, 2])), "p05": float(np.percentile(offs[:, 2], 5)), "p95": float(np.percentile(offs[:, 2], 95))},
        "gps_offset_up_median_m": float(np.median(offs[:, 2])),
        "gps_offset_p95_m": float(np.percentile(np.linalg.norm(offs - [0, 0, np.median(offs[:, 2])], axis=1), 95)),
    }
    print("derived:", json.dumps({k: v for k, v in derived.items() if k != "prior_rotation_C"}), flush=True)

    # anchor points and their observations
    names, cols = opensfm_data.read_tracks(opensfm / "tracks.csv")
    trk = np.asarray(cols["track"]).astype(str)
    im = np.asarray(cols["image"])
    feat = np.asarray(cols["feature"])
    uv_all = np.stack([np.asarray(cols["x"]), np.asarray(cols["y"])], 1).astype(np.float64)
    name_idx = {n: i for i, n in enumerate(names)}
    posed_mask = np.array([n in posed for n in names])
    ids0 = list(anchors["points"])
    X0 = np.array([anchors["points"][k]["coordinates"] for k in ids0])
    # reprojection of every anchor-reconstruction point in every posed anchor shot that sees it
    pos0 = {k: i for i, k in enumerate(ids0)}
    in0 = np.isin(trk, ids0) & np.array([n in anchors["shots"] for n in names])[im]
    r_pt = np.array([pos0[k] for k in trk[in0]])
    r_im = im[in0]
    res = np.full(len(r_pt), np.inf)
    for i in np.unique(r_im):
        s = anchors["shots"][names[i]]
        m = r_im == i
        Xc = X0[r_pt[m]] @ rot(s["rotation"]).T + np.array(s["translation"])
        res[m] = cam.px(np.linalg.norm(cam.project(Xc) - uv_all[in0][m], axis=1))
    good_obs = res <= p["max_reproj_px"]
    n_good = np.bincount(r_pt[good_obs], minlength=len(ids0))
    hag0 = dtm.height_above(X0)
    keep0 = (n_good >= p["min_views"]) & (hag0 >= p["min_height_above_dtm_m"]) & (hag0 <= p["max_height_above_dtm_m"])
    derived["anchor_filter"] = {
        "reconstruction_points": len(ids0),
        "kept": int(keep0.sum()),
        "fewer_than_min_views_within_max_reproj_px": int((n_good < p["min_views"]).sum()),
        "outside_height_band": int(((hag0 < p["min_height_above_dtm_m"]) | (hag0 > p["max_height_above_dtm_m"]) | ~np.isfinite(hag0)).sum()),
        "observation_residual_px": {"median": float(np.median(res[np.isfinite(res)])), "p95": float(np.percentile(res[np.isfinite(res)], 95))},
    }
    print("anchor filter:", derived["anchor_filter"], flush=True)

    X_id = [k for k, kk in zip(ids0, keep0) if kk] + list(layer_points)
    X = np.concatenate([X0[keep0], np.array([v["coordinates"] for v in layer_points.values()]).reshape(-1, 3)])
    X_layer = np.r_[np.zeros(int(keep0.sum()), int), np.array([v["layer"] for v in layer_points.values()], int)]
    pid = {k: i for i, k in enumerate(X_id)}
    # observations: anchor-reconstruction observations within max_reproj_px, layer points' tracks.csv
    # observations in posed shots, and the earlier layers' recorded observations
    o_pt, o_im, o_ft = [], [], []
    kept_obs = good_obs & keep0[r_pt]
    o_pt.append(np.array([pid[ids0[q]] for q in r_pt[kept_obs]], int))
    o_im.append(r_im[kept_obs])
    o_ft.append(feat[in0][kept_obs])
    if layer_points:
        lm = np.isin(trk, list(layer_points)) & posed_mask[im]
        o_pt.append(np.array([pid[k] for k in trk[lm]], int))
        o_im.append(im[lm])
        o_ft.append(feat[lm])
        lo = [o for o in layer_obs if o[2] in pid and o[0] in name_idx]
        o_pt.append(np.array([pid[o[2]] for o in lo], int))
        o_im.append(np.array([name_idx[o[0]] for o in lo], int))
        o_ft.append(np.array([o[1] for o in lo], int))
    o_pt, o_im, o_ft = (np.concatenate(a) for a in (o_pt, o_im, o_ft))
    u = np.unique(np.stack([o_pt, o_im, o_ft], 1), axis=0)
    o_pt, o_im, o_ft = u[:, 0], u[:, 1], u[:, 2]
    order = np.argsort(o_pt, kind="stable")
    o_pt, o_im, o_ft = o_pt[order], o_im[order], o_ft[order]
    obs_count = np.bincount(o_pt, minlength=len(X_id))
    obs_start = np.r_[0, np.cumsum(obs_count)[:-1]]
    centres = {n: centre(rot(s["rotation"]), np.array(s["translation"])) for n, s in posed.items()}
    obs_dir = np.stack([centres[names[i]] for i in o_im]) - X[o_pt]
    obs_range = np.linalg.norm(obs_dir, axis=1)
    obs_dir /= obs_range[:, None]
    derived["anchor_view_range_p99_m"] = float(np.percentile(obs_range, 99))
    obs_desc = np.zeros((len(o_pt), 128), np.uint8)
    for i in np.unique(o_im):
        m = o_im == i
        _, d, _ = load_features(opensfm, names[i])
        obs_desc[m] = descriptors_u8(d[o_ft[m]])
    print(f"{len(X_id)} anchor points ({int(keep0.sum())} from the anchor reconstruction, {len(layer_points)} from layers), {len(o_pt)} observations", flush=True)

    # tracks.csv correspondences for every unposed image
    unposed = sorted(n for n in images if n not in posed)
    track_corr = {}
    tmask = np.isin(trk, X_id) & ~posed_mask[im]
    for i in np.unique(im[tmask]):
        m = tmask & (im == i)
        track_corr[names[i]] = (feat[m].astype(int), np.array([pid[k] for k in trk[m]], int))

    G.update(params=p, cam=cam, derived=derived, X=X, X_id=X_id, X_layer=X_layer, obs_start=obs_start, obs_count=obs_count, obs_dir=obs_dir, obs_desc=obs_desc, gps=gps, yawpitch=yawpitch, C=Cfit, opensfm=opensfm, track_corr=track_corr)
    with multiprocessing.get_context("fork").Pool(args.workers) as pool:
        frames = []
        for r in pool.imap_unordered(locate, unposed):
            frames.append(r)
            print(f"{r['image']}: {r.get('decision', 'not tried')} {r.get('inliers', '')} {r.get('failed', r.get('reason', ''))}", flush=True)
    frames.sort(key=lambda r: r["image"])

    overrides = {}
    if cfg.get("overrides"):
        for o in yaml.safe_load(pathlib.Path(cfg["overrides"]).read_text()) or []:
            overrides[o["image"]] = o
    for r in frames:
        if r["image"] in overrides:
            o = overrides[r["image"]]
            r["override"] = {"decision": o["decision"], "reason": o["reason"]}
            r["decision"] = o["decision"]
    accepted = {r["image"]: r for r in frames if r.get("decision") == "accepted" and "rotation" in r}
    shots = {n: {"rotation": r["rotation"], "translation": r["translation"], "camera": next(iter(anchors["cameras"])), "layer": layer} for n, r in accepted.items()}
    observations = [o for r in accepted.values() for o in r["_inlier_obs"]]
    for r in frames:
        r.pop("_inlier_obs", None)

    all_posed = {**posed, **shots}
    colour_cache: dict = {}

    def colors_of(n):
        if n not in colour_cache:
            colour_cache[n] = load_features(opensfm, n)[2]
        return colour_cache[n]

    skip = set(ids0) | set(layer_points)
    points, new_obs, tri = triangulate(all_posed, set(shots), cam, names, cols, skip, dtm, p, layer, colors_of)
    observations += new_obs
    print(f"accepted {len(shots)}; triangulation: {tri}", flush=True)

    out = args.out
    (out / "shots.json").write_text(json.dumps(shots, indent=1))
    (out / "points.json").write_text(json.dumps(points))
    (out / "observations.json").write_text(json.dumps(observations))
    (out / "frames.json").write_text(json.dumps(frames, indent=1))
    inputs = [pathlib.Path(cfg["anchors"]), opensfm / "tracks.csv", run / "images.json", pathlib.Path(cfg["dtm"])]
    for ld in cfg.get("layers") or []:
        inputs += [pathlib.Path(ld) / f for f in ("shots.json", "points.json", "observations.json")]
    if cfg.get("overrides"):
        inputs.append(pathlib.Path(cfg["overrides"]))
    finished = dt.datetime.now(dt.timezone.utc)
    m = {
        "kind": "mapping_sim anchored pass",
        "layer": layer,
        "software": {
            "repo": manifest._git("config", "--get", "remote.origin.url"),
            "commit": manifest._git("rev-parse", "HEAD"),
            "uncommitted_changes": manifest._git("status", "--porcelain", "--", str(manifest.CODE_DIR), str(args.config.resolve())).splitlines(),
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "opencv": cv2.__version__,
        },
        "command": sys.argv,
        "config": {"path": manifest._repo_path(args.config), "sha256": manifest._sha256(args.config), "contents": cfg},
        "inputs": {str(f): manifest._sha256(f) for f in inputs},
        "derived": derived,
        "counts": {
            "posed_before": len(posed),
            "unposed": len(unposed),
            "tried": sum(1 for r in frames if r.get("tried")),
            "accepted": len(shots),
            "overridden": len([r for r in frames if "override" in r]),
            "new_points": len(points),
            "triangulation": tri,
        },
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "files": {f: manifest._sha256(out / f) for f in ("shots.json", "points.json", "observations.json", "frames.json")},
    }
    (out / "manifest.json").write_text(json.dumps(m, indent=1))


if __name__ == "__main__":
    main()
