#!/usr/bin/env python3
"""Check which motor is which, from one flight's DataFlash log.

Three tables over an airborne window, every series 2 s high-passed:
  1. each FC output against the rate controller's roll / pitch / yaw outputs (RATE.ROut/POut/YOut):
     which outputs the mixer treats as front, left, and the CCW pair;
  2. each axis's controller output against the measured angular acceleration (RATE.R/P/Y
     differentiated), at the best lag up to 100 ms: a positive sign on all three means the
     airframe answers the mixer the right way round;
  3. each ESC telemetry index's RPM against each FC output: which output an index reports.

Usage: PYTHONPATH=analysis motor_mapping_check.py <log.bin> <t0_s> <t1_s>
Times are seconds from the log's first message, as ardupilot_log.parse_log gives t_s.
Docs: docs/rekon10/ardupilot.md, "Which motor is which".
"""
import sys

import numpy as np
import pandas as pd

from ardupilot_log import parse_log


def highpass(x, n):
    return x - pd.Series(x).rolling(n, center=True, min_periods=1).mean().to_numpy()


def corr(a, b):
    return float(np.corrcoef(a, b)[0, 1])


def main(path, t0, t1):
    F, _, p = parse_log(path, ["RATE", "RCOU", "ESC", "PARM"])
    print("SERVO1..4_FUNCTION:", [p.get(f"SERVO{i}_FUNCTION") for i in range(1, 5)])
    rate = F["RATE"].sort_values("t_s")
    rcou = F["RCOU"].sort_values("t_s")
    esc = F["ESC"]

    fs = 10.0
    grid = np.arange(t0, t1, 1 / fs)
    n = int(2 * fs)
    out = {j: highpass(np.interp(grid, rcou.t_s, rcou[f"C{j}"]), n) for j in range(1, 5)}

    print("\n1. output vs controller output       out1   out2   out3   out4")
    for axis, col in (("roll", "ROut"), ("pitch", "POut"), ("yaw", "YOut")):
        u = highpass(np.interp(grid, rate.t_s, rate[col]), n)
        print(f"   {axis:5s}                            " + "  ".join(f"{corr(u, out[j]):+.2f}" for j in range(1, 5)))

    fs2 = 400.0
    g2 = np.arange(t0 + 0.5, t1 - 0.5, 1 / fs2)
    n2 = int(0.5 * fs2)
    print("\n2. controller output vs angular acceleration")
    for axis, col_out, col_rate in (("roll", "ROut", "R"), ("pitch", "POut", "P"), ("yaw", "YOut", "Y")):
        u = highpass(np.interp(g2, rate.t_s, rate[col_out]), n2)
        w = pd.Series(np.interp(g2, rate.t_s, rate[col_rate])).rolling(8, center=True, min_periods=1).mean().to_numpy()
        acc = highpass(np.gradient(w, 1 / fs2), n2)
        best = max(((corr(u[: len(u) - k], acc[k:]), k) for k in range(0, 40, 2)), key=lambda c: abs(c[0]))
        print(f"   {axis:5s} {best[0]:+.2f} at {best[1] / fs2 * 1000:.0f} ms")

    print("\n3. ESC index RPM vs output          out1   out2   out3   out4")
    for i in sorted(esc.Instance.unique()):
        e = esc[esc.Instance == i].sort_values("t_s")
        r = highpass(np.interp(grid, e.t_s, e.RPM), n)
        print(f"   index {int(i)}                          " + "  ".join(f"{corr(r, out[j]):+.2f}" for j in range(1, 5)))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]))
