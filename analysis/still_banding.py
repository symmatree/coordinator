"""still_banding.py -- measure the horizontal blur banding in the 12 MP colour stills.

The IMX378 still camera is rolling-shutter (docs/rekon10/oak-d-mount.md), so a periodic
disturbance during the ~33 ms readout writes itself into the frame as horizontal bands of
varying blur. Measuring the band *pitch* from a single frame does not work -- var-Laplacian
and directional-cutoff detectors are dominated by scene content and fire on ground frames
(that is E24's over-counting caveat, and it reproduces).

This measures it differently. Take **two stills of the same scene**, register them, and take
the **ratio** of per-row gradient energy. Scene texture appears in both frames and divides
out; what survives is the difference in per-row blur, which is camera-side. Fitting a
sinusoid to that ratio gives the pitch.

Two failure modes this module exists to prevent, both of which produced wrong numbers by
hand first:

* **The fit railing against its own bounds.** `band_pitch` returns the full periodogram, and
  `pitch_from_periodogram` refuses a peak sitting in the end bins.
* **Averaging over unregistered junk.** `session_pitch` filters on registration quality and
  fit quality before summarising, rather than taking a median over every pair that parsed.

The pitch is a spatial period in rows. Converting it to a frequency needs the sensor readout
time, which we have **not** measured -- `pitch_to_hz` takes it as an explicit argument and
does not default, so the assumption stays visible at the call site.

**Choosing the pairs.** Registering every nearby-in-time pair and discarding what fails is a
weak filter -- on 260814 it rejected 79 of 88 candidates and every survivor came from the one
hover, because 5 s of translation moves the scene out from under the method. When the flight
log is available, select candidates by **pose** instead: two stills taken from nearly the same
position and heading should show nearly the same scene, whenever they were taken.

That inversion also turns the rejects into a signal. A pair that is *pose-close* and still will
not register is interesting on its own -- the geometry says the frames should match, so
something non-geometric differs: blur, exposure, focus, wind in the scene, or the banding
itself. `session_pitch` reports those separately rather than dropping them.

Backs `analysis/vio-quality-experiments.md` E34.
"""

import glob
import json
import os

import numpy as np
from scipy.ndimage import gaussian_filter, uniform_filter1d

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is an analysis-only dependency
    Image = None

ROW_BLOCK = 8          # rows averaged per sample of the profile
MIN_PHASE_CORR = 0.10  # registration quality floor
MIN_R2 = 0.60          # sinusoid fit quality floor


def load_gray(path):
    if Image is None:
        raise RuntimeError("Pillow is required to read stills")
    return np.asarray(Image.open(path).convert("L"), dtype=np.float32)


def register(a, b, downsample=8):
    """Phase-correlate two frames. Returns (dy, dx, peak); peak is the quality measure."""
    A, B = a[::downsample, ::downsample], b[::downsample, ::downsample]
    A, B = A - A.mean(), B - B.mean()
    w = np.outer(np.hanning(A.shape[0]), np.hanning(A.shape[1]))
    R = np.fft.fft2(A * w) * np.conj(np.fft.fft2(B * w))
    R /= np.abs(R) + 1e-9
    c = np.fft.fftshift(np.real(np.fft.ifft2(R)))
    p = np.unravel_index(np.argmax(c), c.shape)
    return (p[0] - A.shape[0] // 2) * downsample, (p[1] - A.shape[1] // 2) * downsample, float(c.max())


def row_energy(img, block=ROW_BLOCK):
    """Mean squared gradient magnitude per block of rows -- a per-row sharpness profile."""
    g = gaussian_filter(img, 1.0)
    gy, gx = np.gradient(g)
    e = gx * gx + gy * gy
    n = (e.shape[0] // block) * block
    return e[:n].reshape(-1, block, e.shape[1]).mean(axis=(1, 2))


def band_pitch(a, b, window=(80, 2960, 700, 3300), pitch_range=(300, 2600), n_trials=900,
               block=ROW_BLOCK):
    """Scene-cancelled band pitch between two frames of the same scene.

    Returns (trial_pitches, variance_explained, info) or None if the pair will not register
    or the shifted window falls outside the frame. `variance_explained` is the periodogram:
    inspect it rather than trusting the argmax.
    """
    dy, dx, peak = register(a, b)
    if peak < MIN_PHASE_CORR:
        return None
    y0, y1, x0, x1 = window
    if not (0 <= y0 + dy and y1 + dy <= a.shape[0] and 0 <= x0 + dx and x1 + dx <= a.shape[1]):
        return None
    A = a[y0:y1, x0:x1]
    B = b[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
    r = np.log(row_energy(B, block) / row_energy(A, block))
    r = r - uniform_filter1d(r, max(3, int(2500 / block) | 1))  # drop the slow trend
    rows = (np.arange(len(r)) * block + y0).astype(float)
    r = r - r.mean()
    tss = (r ** 2).sum()
    ps = np.linspace(*pitch_range, n_trials)
    ve = np.empty_like(ps)
    for i, p in enumerate(ps):
        w = 2 * np.pi / p
        X = np.column_stack([np.cos(w * rows), np.sin(w * rows), np.ones_like(rows)])
        c, *_ = np.linalg.lstsq(X, r, rcond=None)
        ve[i] = 1 - ((r - X @ c) ** 2).sum() / tss
    return ps, ve, {"dy": dy, "dx": dx, "phase_corr": peak}


def pitch_from_periodogram(ps, ve, edge_frac=0.02):
    """Peak pitch, or None if it sits in the end bins (the fit is railing, not resolving)."""
    i = int(np.argmax(ve))
    n = len(ps)
    if i < n * edge_frac or i > n * (1 - edge_frac):
        return None, float(ve[i]), "peak_at_bound"
    return float(ps[i]), float(ve[i]), "ok"


def pitch_to_hz(pitch_rows, readout_s, n_rows=3040):
    """Spatial pitch -> temporal frequency. `readout_s` is NOT measured; pass it explicitly."""
    return (n_rows / readout_s) / pitch_rows


def pose_pairs(times, positions, yaw_deg, max_dist_m=1.0, max_yaw_deg=10.0,
               min_dt_s=2.0, max_pairs=400):
    """Still pairs taken from nearly the same pose, whenever they were taken.

    `positions` is (n, 3) in any consistent frame; `yaw_deg` is (n,). `min_dt_s` keeps a still
    from pairing with its immediate neighbour when the vehicle is stationary and every frame
    qualifies -- without it a hover produces only adjacent pairs and no revisits. Returns
    (i, j, dist_m, dyaw_deg, dt_s) sorted by separation, closest first.
    """
    t = np.asarray(times, dtype=float)
    P = np.asarray(positions, dtype=float)
    y = np.asarray(yaw_deg, dtype=float)
    out = []
    for i in range(len(t)):
        for j in range(i + 1, len(t)):
            if abs(t[j] - t[i]) < min_dt_s:
                continue
            d = float(np.linalg.norm(P[j] - P[i]))
            if d > max_dist_m:
                continue
            dy = abs((y[j] - y[i] + 180) % 360 - 180)
            if dy > max_yaw_deg:
                continue
            out.append((i, j, d, float(dy), float(t[j] - t[i])))
    out.sort(key=lambda r: (r[2], r[3]))
    return out[:max_pairs]


def session_pitch(paths, max_lag=2, min_r2=MIN_R2, pairs=None, **kw):
    """Quality-filtered band pitch over a session's stills.

    `pairs` is an optional explicit candidate list -- (i, j, ...) tuples, as produced by
    `pose_pairs`. Without it, each still is paired with the next `max_lag` stills, which only
    finds revisits that happen to be adjacent in time.

    Keeps pairs that register and whose sinusoid fit clears `min_r2`, and reports the median and
    IQR of what survives, plus the per-pair values so a wide IQR is visible rather than hidden
    behind a median. Candidates that fail registration are returned in `unregistered_pairs`
    with whatever metadata the candidate carried: when candidates came from `pose_pairs`, a pair
    that is pose-close and will not register is a finding, not noise.
    """
    kept, rejected = [], {"unregistered": 0, "low_r2": 0, "at_bound": 0}
    unregistered_pairs = []
    cache = {}

    def gray(i):
        if i not in cache:
            try:
                cache[i] = load_gray(paths[i])
            except Exception:
                cache[i] = None
            if len(cache) > 6:
                cache.pop(next(iter(cache)))
        return cache[i]

    if pairs is None:
        candidates = [(i, j) for i in range(len(paths) - 1)
                      for j in range(i + 1, min(i + 1 + max_lag, len(paths)))]
    else:
        candidates = list(pairs)

    for cand in candidates:
        i, j = int(cand[0]), int(cand[1])
        meta = {"i": i, "j": j}
        if len(cand) >= 5:
            meta.update({"dist_m": round(cand[2], 3), "dyaw_deg": round(cand[3], 2),
                         "dt_s": round(cand[4], 2)})
        a, b = gray(i), gray(j)
        if a is None or b is None or a.shape != b.shape:
            rejected["unregistered"] += 1
            unregistered_pairs.append({**meta, "why": "unreadable_or_mismatched"})
            continue
        out = band_pitch(a, b, **kw)
        if out is None:
            rejected["unregistered"] += 1
            _dy, _dx, _pk = register(a, b)
            unregistered_pairs.append({**meta, "why": "phase_corr_below_floor",
                                       "phase_corr": round(_pk, 3)})
            continue
        ps, ve, info = out
        pitch, r2, status = pitch_from_periodogram(ps, ve)
        if status != "ok":
            rejected["at_bound"] += 1
        elif r2 < min_r2:
            rejected["low_r2"] += 1
        else:
            kept.append({**meta, "pitch_rows": pitch, "r2": r2, **info})
    if not kept:
        return {"n": 0, "rejected": rejected, "unregistered_pairs": unregistered_pairs}
    v = np.array([k["pitch_rows"] for k in kept])
    return {
        "n": len(kept),
        "pitch_rows_median": round(float(np.median(v)), 1),
        "pitch_rows_iqr": [round(float(np.percentile(v, 25)), 1), round(float(np.percentile(v, 75)), 1)],
        "r2_median": round(float(np.median([k["r2"] for k in kept])), 3),
        "rejected": rejected,
        "pairs": kept,
        "unregistered_pairs": unregistered_pairs,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session", help="capture session directory containing *.jpg stills")
    ap.add_argument("--min-r2", type=float, default=MIN_R2)
    ap.add_argument("--readout-ms", type=float, default=None,
                    help="if given, also report Hz -- this value is an assumption, not a measurement")
    a = ap.parse_args()
    paths = sorted(glob.glob(os.path.join(a.session, "*.jpg")))
    out = session_pitch(paths, min_r2=a.min_r2)
    out["session"] = a.session
    out["n_stills"] = len(paths)
    if a.readout_ms and out.get("n"):
        out["hz_if_readout_is_%gms" % a.readout_ms] = round(
            pitch_to_hz(out["pitch_rows_median"], a.readout_ms / 1000.0), 1)
    out.pop("pairs", None)
    print(json.dumps(out, indent=2))
