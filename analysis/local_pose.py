"""local_pose.py -- frame-to-frame relative pose straight from the tracker's stereo features.

VINS entirely out of the loop: triangulate each frame's stereo-matched points from the
`.feat` (`feat_stream.triangulate`), match to the previous frame **by tracker feature id**,
and solve the rigid transform. This is the floor test -- if the local deltas are bad, no
fusion architecture downstream rescues them.

Rotation and translation are solved separately on purpose, because they are conditioned very
differently on this vehicle:

* **Rotation** from unit **bearing** vectors needs no depth at all, so it does not inherit
  the stereo range error. At ~15 m scene depth and <2 m/s, per-frame translation is well
  under 1% of depth, so bearings are a good rotation cue.
* **Translation** needs triangulated depth, which at 2-3 px disparity on a 7.5 cm baseline is
  where T12 lives. The RANSAC inlier threshold therefore **scales with scene depth** -- a
  fixed metric threshold silently rejects everything far away and biases the fit toward
  near points (the mechanism E26/T12 describe).

Integration breaks at gaps: with the tracker dropping frames (E31) the usable trajectory is a
set of segments, not one track. `integrate` returns segments and never bridges a gap it
cannot see across.

Backs `analysis/vio-quality-experiments.md` E35.
"""

import numpy as np

from feat_stream import BASELINE_M, triangulate


def kabsch(P, Q):
    """Least-squares rigid transform mapping P onto Q (no scale). Returns (R, t)."""
    cp, cq = P.mean(0), Q.mean(0)
    U, _, Vt = np.linalg.svd((P - cp).T @ (Q - cq))
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T
    return R, cq - R @ cp


def ransac_rigid(P, Q, threshold, iters=80, seed=0):
    """RANSAC rigid fit. Returns (R, t, n_inliers, rms, n_points) or None."""
    rng = np.random.default_rng(seed)
    n = len(P)
    if n < 4:
        return None
    best = (-1, None)
    for _ in range(iters):
        idx = rng.choice(n, 3, replace=False)
        try:
            R, t = kabsch(P[idx], Q[idx])
        except np.linalg.LinAlgError:
            continue
        inl = np.linalg.norm((P @ R.T + t) - Q, axis=1) < threshold
        if inl.sum() > best[0]:
            best = (int(inl.sum()), inl)
    if best[0] < 3:
        return None
    inl = best[1]
    R, t = kabsch(P[inl], Q[inl])
    e = np.linalg.norm((P[inl] @ R.T + t) - Q[inl], axis=1)
    return R, t, int(inl.sum()), float(np.sqrt((e ** 2).mean())), n


def ransac_rotation(A, B, threshold_deg=1.0, iters=80, seed=0):
    """RANSAC rotation from unit bearing vectors -- no depth. Returns (R, n_inliers, rms_deg)."""
    rng = np.random.default_rng(seed)
    n = len(A)
    if n < 4:
        return None
    thr = np.radians(threshold_deg)
    best = (-1, None)
    for _ in range(iters):
        idx = rng.choice(n, 3, replace=False)
        U, _, Vt = np.linalg.svd(A[idx].T @ B[idx])
        d = np.sign(np.linalg.det(Vt.T @ U.T))
        R = Vt.T @ np.diag([1, 1, d]) @ U.T
        inl = np.arccos(np.clip(np.einsum("ij,ij->i", A @ R.T, B), -1, 1)) < thr
        if inl.sum() > best[0]:
            best = (int(inl.sum()), inl)
    if best[0] < 3:
        return None
    inl = best[1]
    U, _, Vt = np.linalg.svd(A[inl].T @ B[inl])
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T
    ang = np.arccos(np.clip(np.einsum("ij,ij->i", A[inl] @ R.T, B[inl]), -1, 1))
    return R, int(inl.sum()), float(np.degrees(np.sqrt((ang ** 2).mean())))


def _frame_points(arr, baseline):
    ids, xyz, _ = triangulate(arr, baseline)
    bear = np.column_stack([arr[:, 1], arr[:, 2], np.ones(len(arr))])
    bear /= np.linalg.norm(bear, axis=1, keepdims=True)
    keep = {int(i): k for k, i in enumerate(arr[:, 0].astype(int))}
    return {i: (xyz[k], bear[keep[i]]) for k, i in enumerate(ids)}


def relative_poses(features, times, max_dt=2.0, min_shared=6, depth_rel_threshold=0.03,
                   min_threshold_m=0.05, baseline=BASELINE_M):
    """Solve each consecutive pair. Returns a list of dicts (`ok=False` where it failed).

    `max_dt` allows chaining across a dropped-frame gap when tracker ids survive it -- they do
    up to about 2 s, because the device-side tracker keeps running through a host-side stall.
    """
    pts = [_frame_points(f[2], baseline) if len(f[2]) else {} for f in features]
    out = []
    for k in range(1, len(features)):
        dt = times[k] - times[k - 1]
        a, b = pts[k - 1], pts[k]
        shared = sorted(set(a) & set(b))
        if dt > max_dt or len(shared) < min_shared:
            out.append({"t": times[k], "dt": dt, "ok": False, "n_shared": len(shared)})
            continue
        P = np.array([a[i][0] for i in shared])
        Q = np.array([b[i][0] for i in shared])
        A = np.array([a[i][1] for i in shared])
        B = np.array([b[i][1] for i in shared])
        thr = max(min_threshold_m, depth_rel_threshold * float(np.median(P[:, 2])))
        rigid = ransac_rigid(P, Q, thr)
        rot = ransac_rotation(A, B)
        if rigid is None or rot is None:
            out.append({"t": times[k], "dt": dt, "ok": False, "n_shared": len(shared)})
            continue
        R, t, inl, rms, n = rigid
        Rb, inl_b, rms_b = rot
        out.append({"t": times[k], "dt": dt, "ok": True, "n_shared": n, "inliers": inl,
                    "residual_m": rms, "depth_median_m": float(np.median(P[:, 2])),
                    "threshold_m": thr, "R": R, "t_vec": t,
                    "R_bearing": Rb, "inliers_bearing": inl_b, "residual_deg": rms_b})
    return out


def integrate(poses, rotation_key="R_bearing", min_poses=40):
    """Chain solved pairs into segments, breaking wherever a pair failed.

    Returns a list of {t, positions, rotations}. Nothing bridges a gap: a broken pair ends the
    segment, because there is no information across it.
    """
    segs, cur = [], None
    Rw, pw = np.eye(3), np.zeros(3)
    for p in poses:
        if not p.get("ok"):
            if cur and len(cur["t"]) >= min_poses:
                segs.append(cur)
            cur, Rw, pw = None, np.eye(3), np.zeros(3)
            continue
        Rw = Rw @ p[rotation_key].T
        pw = pw - Rw @ p["t_vec"]
        if cur is None:
            cur = {"t": [], "positions": [], "rotations": []}
        cur["t"].append(p["t"])
        cur["positions"].append(pw.copy())
        cur["rotations"].append(Rw.copy())
    if cur and len(cur["t"]) >= min_poses:
        segs.append(cur)
    for s in segs:
        s["t"] = np.array(s["t"])
        s["positions"] = np.array(s["positions"])
    return segs


def umeyama(P, Q, with_scale=True):
    """Similarity fit P -> Q. Returns (R, scale, t). Same convention as vio_ekf_compare."""
    cp, cq = P.mean(0), Q.mean(0)
    X, Y = P - cp, Q - cq
    U, S, Vt = np.linalg.svd(X.T @ Y / len(P))
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1, 1, d])
    R = Vt.T @ D @ U.T
    c = (S * np.diag(D)).sum() / ((X ** 2).sum() / len(P)) if with_scale else 1.0
    return R, c, cq - c * R @ cp
