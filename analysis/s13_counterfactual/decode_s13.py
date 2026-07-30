#!/usr/bin/env python3
"""S13: decode the VIO-primary counterfactual Replay vs the GPS-driven truth.

Replay re-ran 260728's EKF3 with EK3_SRC1_POSXY/VELXY forced to ExtNav (VIO), on
the real logged IMU + real logged VIO. Its XKF1 is the "what if VIO drove position"
solution. Compare to the ORIGINAL log's XKF1 (GPS-driven = where the vehicle
actually was). Uses the logged (VISO_TYPE=1) UNALIGNED VIO -- expect it to fail.
"""
import os
import sys

import numpy as np

REPLAY = "/home/jovyan/wt-s13/analysis/s13_counterfactual/run/logs/00000001.BIN"
ORIG = "/home/jovyan/datasets/flights/rekon10/260728-sunny-baseline/1980-01-08 17-07-39.bin"
ARM, DISARM = 355.0, 598.0

from pymavlink import mavutil  # noqa: E402


def xkf1(path, core=0):
    m = mavutil.mavlink_connection(path)
    t, pn, pe = [], [], []
    while True:
        msg = m.recv_match(type="XKF1", blocking=False)
        if msg is None:
            break
        if getattr(msg, "C", 0) == core:
            t.append(msg.TimeUS / 1e6); pn.append(msg.PN); pe.append(msg.PE)
    return np.array(t), np.array(pn), np.array(pe)


print("decoding VIO-driven (replay)...", flush=True)
tv, vn, ve = xkf1(REPLAY, 0)
print(f"  {len(tv)} XKF1; PN range [{vn.min():.0f},{vn.max():.0f}] PE [{ve.min():.0f},{ve.max():.0f}]", flush=True)
print("decoding GPS truth (original)...", flush=True)
tg, gn, ge = xkf1(ORIG, 0)
print(f"  {len(tg)} XKF1; PN range [{gn.min():.1f},{gn.max():.1f}] PE [{ge.min():.1f},{ge.max():.1f}]", flush=True)

# common armed window, interpolate VIO onto GPS timestamps
mask = (tg >= ARM) & (tg <= DISARM)
tt = tg[mask]
gN, gE = gn[mask], ge[mask]
vN = np.interp(tt, tv, vn); vE = np.interp(tt, tv, ve)
dev = np.hypot(vN - gN, vE - gE)
print(f"\nArmed window {ARM:.0f}-{DISARM:.0f}s:", flush=True)
print(f"  GPS truth extent: N {gN.max()-gN.min():.1f} m, E {gE.max()-gE.min():.1f} m", flush=True)
print(f"  VIO-driven extent: N {vN.max()-vN.min():.1f} m, E {vE.max()-vE.min():.1f} m", flush=True)
print(f"  VIO-vs-GPS deviation: median {np.median(dev):.1f} m, p95 {np.percentile(dev,95):.1f} m, MAX {dev.max():.1f} m", flush=True)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(13, 5.5))
    ax[0].plot(gE, gN, color="#2e8b57", lw=1.5, label="GPS-driven EKF (truth)")
    ax[0].plot(vE, vN, color="#d1495b", lw=1.0, alpha=0.8, label="VIO-driven EKF (counterfactual)")
    ax[0].set_xlabel("East (m)"); ax[0].set_ylabel("North (m)"); ax[0].set_aspect("equal", "datalim")
    ax[0].legend(fontsize=8); ax[0].set_title("Track: VIO-driven vs GPS truth (260728, armed)")
    ax[1].plot(tt, dev, color="#d1495b", lw=1)
    ax[1].set_xlabel("time (s)"); ax[1].set_ylabel("VIO-vs-GPS deviation (m)")
    ax[1].set_title("Position error if VIO had driven")
    fig.tight_layout()
    out = "/home/jovyan/wt-s13/analysis/s13_counterfactual/run/s13_counterfactual.png"
    fig.savefig(out, dpi=120); print(f"wrote {out}", flush=True)
except Exception as e:
    print("plot skipped:", e, flush=True)
