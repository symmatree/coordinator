"""feat_stream.py -- decode a `vio-ipc-record` / in-tracker-tee `.feat` fixture.

The `.feat` is the estimator's *input*, teed at the tracker (coordinator #78/#125), so it
sits upstream of vins_fusion, of the router, and of anything the FC logs. Format (manifest
version 1, documented in `harness/input_replayer.py`): per datagram

    <double t_mono><double t_unix><uint16 socket_id><uint32 length><length bytes>

little-endian, socket 0 = `chobits_imu` (7 doubles), socket 1 = `chobits_features`.

A feature datagram is `[n_features, device_timestamp_s, then 13 doubles per feature]`:

    id, unL_x, unL_y, rawL_x, rawL_y, vLx, vLy, unR_x, unR_y, rawR_x, rawR_y, vRx, vRy

`un*` are undistorted normalised coordinates, `raw*` are pixels; the packing is
`containers/vio-tracker/overlay/oak_d_vins_cpp/feature_tracker.cpp`. Both are present, so
the intrinsics fall out of the stream itself (see `intrinsics()`), and stereo depth follows
from the normalised disparity without needing a calibration file.

Backs `analysis/vio-quality-experiments.md` E31 (frame loss), E32 (feature supply and scene
depth) and, via `local_pose.py`, E35.
"""

import json
import struct

import numpy as np

FRAME = struct.Struct("<ddHI")
SID_IMU, SID_FEATURES = 0, 1
IMU_PAYLOAD_BYTES = 56  # 7 doubles: t, ax, ay, az, gx, gy, gz
FEATURE_STRIDE = 13     # doubles per stereo-matched feature
BASELINE_M = 0.075      # OAK-D stereo baseline (docs/rekon10/oak-d-mount.md)


def decode(path):
    """Parse a fixture into (imu, features).

    imu      -- (n, 8) float array: t_mono, then the 7 logged doubles.
    features -- list of (t_mono, device_ts, arr) where arr is (n_feat, 13).

    A truncated tail (recorder killed mid-write) stops the parse cleanly, matching
    `input_replayer.decode_frames`.
    """
    data = memoryview(open(path, "rb").read())
    n, off = len(data), 0
    imu, feats = [], []
    while off + FRAME.size <= n:
        t_mono, _t_unix, sid, ln = FRAME.unpack_from(data, off)
        off += FRAME.size
        if off + ln > n:
            break
        if sid == SID_IMU and ln == IMU_PAYLOAD_BYTES:
            imu.append((t_mono,) + struct.unpack_from("<7d", data, off))
        elif sid == SID_FEATURES and ln >= 16:
            a = np.frombuffer(data, dtype="<f8", count=ln // 8, offset=off)
            k = int(a[0])
            feats.append((t_mono, float(a[1]), a[2:2 + FEATURE_STRIDE * k].reshape(k, FEATURE_STRIDE)))
        off += ln
    return np.array(imu, dtype=float).reshape(-1, 8), feats


def frame_loss(features, fps=None):
    """Frames the camera produced vs frames the tracker emitted (E31, #156).

    Device timestamps land on the sensor's frame grid, so the number of frames skipped
    between two emitted datagrams is `round(dt / frame_step)`. `frame_step` is taken as
    the median device dt unless `fps` is given. `grid_residual_s` is the diagnostic: if it
    is not ~0 the timestamps are not on a grid and the count below is meaningless.
    """
    if len(features) < 3:
        return {}
    dev = np.array([f[1] for f in features])
    d = np.diff(dev)
    d = d[(d > 0) & (d < 30)]
    step = 1.0 / fps if fps else float(np.median(d))
    k = np.round(d / step)
    implied = float(k.sum() + 1)
    return {
        "emitted": len(features),
        "implied": implied,
        "lost": implied - len(features),
        "loss_frac": 1.0 - len(features) / implied,
        "frame_step_s": step,
        "grid_residual_s": float(np.median(np.abs(d - k * step))),
        "longest_gap_s": float(d.max()),
        "gaps_over_1s": int((d > 1.0).sum()),
        "gaps_over_2s": int((d > 2.0).sum()),
    }


def intrinsics(arr):
    """Recover (fx, cx, fy, cy) from one frame: raw pixels vs undistorted normalised."""
    if len(arr) < 3:
        return None
    fx, cx = np.linalg.lstsq(np.column_stack([arr[:, 1], np.ones(len(arr))]), arr[:, 3], rcond=None)[0]
    fy, cy = np.linalg.lstsq(np.column_stack([arr[:, 2], np.ones(len(arr))]), arr[:, 4], rcond=None)[0]
    return float(fx), float(cx), float(fy), float(cy)


def triangulate(arr, baseline=BASELINE_M, min_disparity=5e-4):
    """Stereo-triangulate one frame's features in the left camera frame.

    Works in the undistorted normalised coordinates, so normalised disparity is
    `unL_x - unR_x` and `Z = baseline / disparity` with no focal length needed.
    Returns (ids, xyz, disparity_norm); features at or below `min_disparity` are dropped.
    """
    d = arr[:, 1] - arr[:, 7]
    ok = d > min_disparity
    z = baseline / d[ok]
    return arr[ok, 0].astype(int), np.column_stack([arr[ok, 1] * z, arr[ok, 2] * z, z]), d[ok]


def depth_stats(features, t0=None, t1=None, times=None, baseline=BASELINE_M):
    """Pooled disparity and depth percentiles over a window (E32).

    `times` is a per-frame time array in whatever clock the window is expressed in; when
    omitted every frame is used. Disparity is reported in pixels via `intrinsics()`, so the
    number is comparable with E27.
    """
    sel = range(len(features))
    if times is not None and (t0 is not None or t1 is not None):
        t = np.asarray(times)
        sel = np.where((t >= (t0 if t0 is not None else -np.inf)) & (t <= (t1 if t1 is not None else np.inf)))[0]
    dn, zs, fxs = [], [], []
    for i in sel:
        arr = features[i][2]
        if len(arr) < 3:
            continue
        k = intrinsics(arr)
        if k:
            fxs.append(k[0])
        _, xyz, d = triangulate(arr, baseline)
        dn.append(d)
        zs.append(xyz[:, 2])
    if not dn:
        return {}
    fx = float(np.median(fxs)) if fxs else np.nan
    d_px = np.concatenate(dn) * fx
    z = np.concatenate(zs)
    q = lambda a, p: [round(float(v), 3) for v in np.percentile(a, p)]
    return {
        "n_observations": int(z.size),
        "fx_px": round(fx, 2),
        "disparity_px": dict(zip(["p5", "p25", "median", "p75", "p95"], q(d_px, [5, 25, 50, 75, 95]))),
        "frac_under_2px": round(float((d_px < 2).mean()), 4),
        "depth_m": dict(zip(["p5", "p25", "median", "p75", "p95"], q(z, [5, 25, 50, 75, 95]))),
        "frac_beyond_10m": round(float((z > 10).mean()), 4),
    }


def feature_counts(features):
    """Per-frame stereo-matched feature count (E32)."""
    return np.array([len(f[2]) for f in features])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("fixture")
    ap.add_argument("--json", action="store_true", help="emit the summary as JSON")
    a = ap.parse_args()
    imu, feats = decode(a.fixture)
    out = {"fixture": a.fixture, "imu_datagrams": len(imu), "frame_loss": frame_loss(feats),
           "depth": depth_stats(feats)}
    counts = feature_counts(feats)
    if counts.size:
        out["features_per_frame"] = {"median": float(np.median(counts)), "p5": float(np.percentile(counts, 5)),
                                     "min": int(counts.min()), "max": int(counts.max())}
    print(json.dumps(out, indent=2))
