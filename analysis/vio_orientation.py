#!/usr/bin/env python3
"""Determine the VINS->NED horizontal rotation from flight logs, and test whether
it is a FIXED mount offset (-> a static VISO_ORIENT can fix it) or the arbitrary
per-flight VINS-init heading (-> only GPS yaw-anchoring can fix it).

Stereo-only VINS has no global heading reference, so its world-frame yaw is set by
whatever the vehicle heading was at VINS init. If the measured VISP->NED rotation
equals the compass heading at init, the rotation is that arbitrary datum, not a
mount offset. We fit the rotation in the HORIZONTAL plane only (NE) -- a tame
low-altitude flight has near-zero vertical signal, so a full 3D Umeyama's
out-of-plane axis is unconstrained (garbage).

    python3 vio_orientation.py <fc.bin> [name]
"""
import sys

import numpy as np

sys.path.insert(0, "/home/jovyan/wt-vio-orient/analysis")
from vio_ekf_compare import find_window, load_fc, load_visp_pose, parse_log  # noqa: E402


def umeyama2d(src, dst):
    """Horizontal similarity src(Nx2)->dst(Nx2); return (angle_deg, scale)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s, d = src - mu_s, dst - mu_d
    U, D, Vt = np.linalg.svd((d.T @ s) / len(src))
    W = np.eye(2)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        W[1, 1] = -1
    R = U @ W @ Vt
    c = np.trace(np.diag(D) @ W) / ((s ** 2).sum() / len(src))
    return np.degrees(np.arctan2(R[1, 0], R[0, 0])), c


def orient(bin_path, name):
    v = load_visp_pose(bin_path)
    if v is None:
        print(f"[{name}] no VISP"); return
    fc = load_fc(bin_path)
    v = v.copy(); v["tfc"] = v["te"]
    t0, t_div, reason = find_window(v, fc, lag=0.0)
    xkf = fc["xkf"]
    win = v[(v["tfc"] >= t0) & (v["tfc"] <= t_div)].copy()
    gtNE = np.c_[np.interp(win["tfc"], xkf["t_s"], xkf["PN"]),
                 np.interp(win["tfc"], xkf["t_s"], xkf["PE"])]
    vioNE = win[["px", "py"]].values
    ang, c = umeyama2d(vioNE, gtNE)                     # NED-north(VISP) -> NED-north(EKF)

    # Compass heading (ATT.Yaw) near the fit window start = vehicle heading ~ when
    # the VISP frame's yaw was effectively pinned.
    att = parse_log(bin_path, ["ATT"])[0].get("ATT")
    hdg = None
    if att is not None:
        at = att["TimeUS"].values / 1e6
        i = int(np.argmin(np.abs(at - t0)))
        hdg = float(att["Yaw"].values[i])

    print(f"[{name}] window {t0:.0f}-{t_div:.0f}s  n={len(win)}  scale={c:.3f}")
    print(f"  horizontal VISP->NED rotation = {ang:+.1f} deg")
    if hdg is not None:
        print(f"  compass heading at window start = {hdg:.1f} deg")
        print(f"  rotation - heading = {((ang - hdg + 180) % 360) - 180:+.1f} deg  "
              f"(near 0 => rotation IS the arbitrary init heading, not a mount offset)")
    nearest = min([0, 90, 180, -90], key=lambda a: abs(((ang - a + 180) % 360) - 180))
    print(f"  nearest cardinal: {nearest} deg (off by {((ang - nearest + 180) % 360) - 180:+.1f})")


if __name__ == "__main__":
    orient(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "run")
