"""capture_align.py -- put coordinator-side capture data on the FC clock.

Three clocks are involved and they are not interchangeable:

* `t_mono` / `monotonic_ns` -- the coordinator's CLOCK_MONOTONIC. Never steps.
* `t_unix` / `wall_clock_unix` -- the coordinator's wall clock. **Steps**, once, when NTP
  first disciplines it after boot. On 260814 that was +184.288 s at monotonic 69.3 s, which
  makes the first frames of a session look 184 s early and reads as a hole in the capture
  cadence where there is none (E30).
* the FC's `TimeUS` -- monotonic from FC boot, unrelated to either.

So **join on monotonic, never on wall clock**. The bridge to FC time is free: the FC logs
`VISP.RTimeUS`, the coordinator's own wall-clock timestamp for each pose it received, next to
its own `TimeUS`. Regressing one on the other gives coordinator-wall -> FC-time directly, with
no GPS week/ms arithmetic and no assumption about NTP quality. Datagrams in a `.feat` carry
both `t_mono` and `t_unix`, so the same fit maps a fixture onto the flight.

Backs `analysis/vio-quality-experiments.md` E30.
"""

import numpy as np

STEP_THRESHOLD_S = 0.5


def clock_step(monotonic_s, wall_unix):
    """Find the NTP step in a coordinator wall clock. Returns a dict, or None if there is none.

    Works on the offset series `wall - monotonic`, which is flat except at a step.
    """
    mono = np.asarray(monotonic_s, dtype=float)
    wall = np.asarray(wall_unix, dtype=float)
    order = np.argsort(mono)
    mono, wall = mono[order], wall[order]
    off = wall - mono
    d = np.diff(off)
    idx = np.where(np.abs(d) > STEP_THRESHOLD_S)[0]
    if not len(idx):
        return None
    i = int(idx[-1])
    after = off[i + 1:]
    return {
        "step_s": float(d[i]),
        "at_monotonic_s": float(mono[i + 1]),
        "n_before": i + 1,
        "offset_after_s": float(np.median(after)),
        "offset_after_sd_s": float(np.std(after)),
    }


def fc_time_from_visp(visp):
    """Fit coordinator wall clock -> FC `t_s` from the `VISP` records.

    `visp` needs `t_s` and `RTimeUS` (microseconds, coordinator wall clock). Rows before the
    coordinator's own NTP step are dropped, since their timestamps are on the pre-step clock.
    Returns (fn, info) where `fn(wall_unix) -> fc_t_s`.
    """
    t_fc = np.asarray(visp["t_s"], dtype=float)
    t_co = np.asarray(visp["RTimeUS"], dtype=float) / 1e6
    off = t_fc - t_co
    d = np.diff(off)
    idx = np.where(np.abs(d) > STEP_THRESHOLD_S)[0]
    start = int(idx[-1]) + 1 if len(idx) else 0
    t_fc, t_co = t_fc[start:], t_co[start:]
    slope, intercept = np.polyfit(t_co, t_fc, 1)
    resid = t_fc - (intercept + slope * t_co)
    info = {
        "n": int(len(t_fc)),
        "dropped_pre_step": start,
        "slope": float(slope),
        "residual_rms_s": float(np.sqrt((resid ** 2).mean())),
        "residual_max_s": float(np.abs(resid).max()),
    }
    return (lambda w: intercept + slope * np.asarray(w, dtype=float)), info


def repair_wall_clock(monotonic_s, wall_unix):
    """Undo the NTP step so a whole session shares one wall clock.

    Timestamps recorded before the step are on the pre-step clock and are wrong by the step
    size. Rebuild them from monotonic plus the post-step offset. Returns the repaired array;
    without this the first frames of a session land in the wrong place (E30) -- which reads as
    a gap in the capture cadence rather than as a clock artefact.
    """
    mono = np.asarray(monotonic_s, dtype=float)
    wall = np.asarray(wall_unix, dtype=float).copy()
    step = clock_step(mono, wall)
    if step is None:
        return wall
    pre = mono < step["at_monotonic_s"]
    wall[pre] = mono[pre] + step["offset_after_s"]
    return wall


def airborne_window(fc, climb_m=0.30):
    """Liftoff and touchdown in FC `t_s`, derived physically.

    Liftoff is the first EKF altitude `climb_m` above the pre-arm ground level; touchdown is
    the last time throttle is above zero. Do **not** substitute an absolute altitude threshold
    for touchdown -- the vehicle can land below the home datum (260814 landed ~0.8 m below it,
    downslope of the launch point) and the threshold then never re-triggers.
    """
    xk = fc["XKF1"]
    xk = xk[xk["C"] == 0].sort_values("t_s")
    ct = fc["CTUN"].sort_values("t_s")
    t = ct["t_s"].values
    alt = np.interp(t, xk["t_s"], -xk["PD"])
    arm = fc.get("ARM")
    t_arm = float(arm[arm["ArmState"] == 1]["t_s"].min()) if arm is not None and (arm["ArmState"] == 1).any() else None
    ground = float(np.median(alt[t < (t_arm if t_arm else 60)]))
    up = np.where(alt > ground + climb_m)[0]
    if not len(up):
        return None
    on = np.where((t > (t_arm or 0)) & (ct["ThO"].values > 0))[0]
    return float(t[up[0]]), float(t[on[-1]] if len(on) else t[up[-1]])
