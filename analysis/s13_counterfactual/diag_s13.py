#!/usr/bin/env python3
"""Diagnose the S13 0.0 result: did VIO actually drive, or is the logged ExtNav
already GPS-anchored (align_inactive_sources ran all flight, GPS-primary)?"""
import numpy as np
from pymavlink import mavutil

REPLAY = "/home/jovyan/wt-s13/analysis/s13_counterfactual/run/logs/00000001.BIN"
ORIG = "/home/jovyan/datasets/flights/rekon10/260728-sunny-baseline/1980-01-08 17-07-39.bin"
ARM, DISARM = 355.0, 598.0


def pull(path, typ, fields, core=None):
    m = mavutil.mavlink_connection(path)
    out = {f: [] for f in fields}
    out["t"] = []
    while True:
        msg = m.recv_match(type=typ, blocking=False)
        if msg is None:
            break
        if core is not None and getattr(msg, "C", 0) != core:
            continue
        out["t"].append(msg.TimeUS / 1e6)
        for f in fields:
            out[f].append(getattr(msg, f))
    return {k: np.array(v) for k, v in out.items()}


rep = pull(REPLAY, "XKF1", ["PN", "PE"], core=0)   # VIO-driven re-run
org = pull(ORIG, "XKF1", ["PN", "PE"], core=0)     # GPS-driven truth
vis = pull(ORIG, "VISP", ["PX", "PY"])             # raw logged VIO input

# exact diff replay vs original XKF1 (same core), matched by nearest time
def dev(a_t, a_n, a_e, b_t, b_n, b_e, lo, hi):
    mask = (b_t >= lo) & (b_t <= hi)
    bt, bn, be = b_t[mask], b_n[mask], b_e[mask]
    an = np.interp(bt, a_t, a_n); ae = np.interp(bt, a_t, a_e)
    return np.hypot(an - bn, ae - be)

d_rep_org = dev(rep["t"], rep["PN"], rep["PE"], org["t"], org["PN"], org["PE"], ARM, DISARM)
print(f"replay-XKF1 vs original-XKF1 (both core0): median {np.median(d_rep_org):.3f} m, MAX {d_rep_org.max():.3f} m")
print(f"  -> {'IDENTICAL (VIO did not redirect, or input==GPS)' if d_rep_org.max()<0.5 else 'DIFFERENT (VIO drove differently)'}")

# raw VISP vs original XKF1: is the raw logged VIO input itself already GPS-like?
d_vis_org = dev(vis["t"], vis["PX"], vis["PY"], org["t"], org["PN"], org["PE"], ARM, DISARM)
print(f"raw VISP vs original-XKF1(GPS): median {np.median(d_vis_org):.2f} m, MAX {d_vis_org.max():.2f} m")
print(f"  VISP extent N {vis['PX'][(vis['t']>=ARM)&(vis['t']<=DISARM)].ptp():.1f} PE {vis['PY'][(vis['t']>=ARM)&(vis['t']<=DISARM)].ptp():.1f}")
print(f"  GPS  extent N {org['PN'][(org['t']>=ARM)&(org['t']<=DISARM)].ptp():.1f} PE {org['PE'][(org['t']>=ARM)&(org['t']<=DISARM)].ptp():.1f}")
