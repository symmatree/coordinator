"""vio_ekf_compare.py -- compare a regenerated VINS pose trajectory to the FC EKF.

Given a VINS pose CSV (from replaying a vio-ipc-record fixture through the real
vins_fusion offline -- see docs/vio-offline-replay.md) and the matching ArduPilot
dataflash .bin, this:

  1. time-aligns VINS to the FC log by angular-speed cross-correlation (both observe
     the same body rotation; wall-clocks are unusable -- the FC .bin is 1980-dated),
  2. finds the valid window: motion onset (leaving the idle-on-the-ground hold) up to
     where VINS diverges (velocity runs non-physical) or the FC sees an aggressive
     maneuver (the hand barrel-roll / hard yaw that breaks tracking),
  3. Umeyama-aligns the VINS position to the EKF NED position over that window and
     reports the trajectory error (ATE) and the recovered scale,
  4. draws the story: idle (VINS flat vs GPS settling), takeoff, and the divergence.

Used by analysis/vio-ekf-comparison.ipynb. Frame note: VINS world != FC NED; the
Umeyama fit absorbs the rotation/translation (and reports scale), so no hand-coded
axis convention is needed. See coordinator #42.
"""

import json
import warnings

import numpy as np
import pandas as pd

from ardupilot_log import parse_log

# Finite-difference speed smoothing (samples) at the VINS pose rate (~11 Hz), ~0.4 s.
SPEED_SMOOTH = 5


# ---------------------------------------------------------------- loaders
def load_vio_pose(pose_csv, replay_speed=1.0):
    """VINS pose CSV -> DataFrame with elapsed capture time `te`, quat, pos, vel, and
    body angular speed `w`. `replay_speed` rescales the tap's replay clock back to
    capture time (e.g. 0.9 if replayed at --speed 0.9)."""
    v = pd.read_csv(pose_csv)
    # `t_mono` = the live replay-tap clock; `t` = the offline harness's sensor timestamp
    # (estimator Headers). Both are elapsed-capture-time sources; prefer t_mono if present.
    tcol = "t_mono" if "t_mono" in v.columns else "t"
    v["te"] = (v[tcol] - v[tcol].iloc[0]) * replay_speed
    q = v[["qw", "qx", "qy", "qz"]].values
    dot = np.abs(np.sum(q[:-1] * q[1:], axis=1)).clip(-1, 1)
    dt = np.diff(v["te"].values)
    dt[dt <= 0] = np.nan
    v["w"] = np.nan_to_num(np.concatenate([[0.0], 2 * np.arccos(dot) / dt]))
    # Speed is derived from the POSITION track, never from vx/vy/vz. The offline
    # vins_fusion_offline writes zero velocity; trusting that column makes `speed` zero,
    # which defeats the motion/divergence windowing and yields plausible-but-wrong ATE
    # (#101). Position is always populated and is the ground truth for motion here.
    dp = np.linalg.norm(np.diff(v[["px", "py", "pz"]].values, axis=0), axis=1)
    raw = np.nan_to_num(np.concatenate([[0.0], dp / dt]))  # dt already has <=0 -> nan
    v["speed"] = np.convolve(raw, np.ones(SPEED_SMOOTH) / SPEED_SMOOTH, mode="same")
    # If velocity is ever non-zero, do NOT silently use it -- warn so it is evaluated.
    if {"vx", "vy", "vz"}.issubset(v.columns) and np.abs(v[["vx", "vy", "vz"]].values).max() > 0:
        warnings.warn(
            "VINS pose CSV has a non-zero velocity column; it is NOT used for windowing "
            "(speed is derived from position). Evaluate the velocity explicitly before "
            "trusting it (#101).",
            stacklevel=2,
        )
    return v


def load_fc(fc_bin):
    """Parse the FC .bin -> dict with IMU0 (|gyro| for alignment), XKF1 (EKF NED
    trajectory), and VIBE/RCOU/ATT/GPS for context."""
    F, dur, parms = parse_log(fc_bin, ["IMU", "XKF1", "GPS", "VIBE", "ATT", "RCOU"])
    imu0 = F["IMU"][F["IMU"]["I"] == 0].reset_index(drop=True)
    imu0["w"] = np.linalg.norm(imu0[["GyrX", "GyrY", "GyrZ"]].values, axis=1)
    xkf = F["XKF1"]
    if "C" in xkf:  # core 0 only (lane 0 is the primary EKF)
        xkf = xkf[xkf["C"] == 0]
    xkf = xkf.reset_index(drop=True)
    return dict(F=F, dur=dur, parms=parms, imu0=imu0, xkf=xkf)


# ---------------------------------------------------------------- alignment
def _resample(t, x, dt, t0, t1):
    grid = np.arange(t0, t1, dt)
    return grid, np.interp(grid, t, x)


def align_time(vio_t, vio_w, fc_t, fc_w, dt=0.05):
    """Slide the VINS angular-speed envelope over the FC |gyro| envelope; return
    (lag, peak_ncc) mapping VINS elapsed time to FC t_s:  fc_t ~= vio_t + lag."""
    _, a = _resample(vio_t - vio_t[0], vio_w, dt, 0, vio_t[-1] - vio_t[0])
    _, b = _resample(fc_t - fc_t[0], fc_w, dt, 0, fc_t[-1] - fc_t[0])
    a = (a - a.mean()) / (a.std() + 1e-9)
    b = (b - b.mean()) / (b.std() + 1e-9)
    corr = np.correlate(b, a, mode="valid")
    win = len(a)
    b2 = np.concatenate([[0.0], np.cumsum(b * b)])
    energy = b2[win:win + len(corr)] - b2[:len(corr)]
    ncc = corr / (np.sqrt(np.maximum(energy, 1e-9)) * np.sqrt(a @ a) + 1e-9)
    k = int(np.argmax(ncc))
    return (fc_t[0] + k * dt) - vio_t[0], float(ncc[k])


# ---------------------------------------------------------------- window
def find_window(v, fc, lag, vel_onset=0.3, vel_diverge=6.0, roll_rate_deg=200.0):
    """Return (t0_fc, t_div_fc, reason) for the valid comparison window in FC t_s.

    t0    = motion onset: VINS speed first sustained above `vel_onset`.
    t_div = the earlier of: VINS speed exceeding `vel_diverge` (runaway), or the FC
            attitude rate exceeding `roll_rate_deg` deg/s (aggressive maneuver).
    """
    tfc = v["te"].values + lag
    onset_i = np.argmax(v["speed"].values > vel_onset)
    t0 = tfc[onset_i] if v["speed"].values.max() > vel_onset else tfc[0]

    div_i = np.argmax(v["speed"].values > vel_diverge) if (v["speed"].values > vel_diverge).any() else len(v) - 1
    t_div_vio = tfc[div_i]

    att = fc["F"].get("ATT")
    t_roll = np.inf
    if att is not None and len(att) > 2:
        rr = np.abs(np.diff(att["Roll"].values)) / np.maximum(np.diff(att["t_s"].values), 1e-3)
        hot = att["t_s"].values[1:][rr > roll_rate_deg]
        hot = hot[hot > t0]  # only maneuvers after we're airborne/moving
        if len(hot):
            t_roll = hot[0]

    if t_roll < t_div_vio:
        return t0, t_roll, "fc_aggressive_attitude"
    return t0, t_div_vio, "vio_velocity_runaway"


# ---------------------------------------------------------------- trajectory fit
def umeyama(src, dst, with_scale=True):
    """Least-squares similarity src->dst: returns (R, c, t) with dst ~= c*R@src + t."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s, d = src - mu_s, dst - mu_d
    U, D, Vt = np.linalg.svd((d.T @ s) / len(src))
    W = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        W[2, 2] = -1
    R = U @ W @ Vt
    var = (s ** 2).sum() / len(src)
    c = np.trace(np.diag(D) @ W) / var if with_scale else 1.0
    return R, c, mu_d - c * R @ mu_s


def compare(pose_csv, fc_bin, run_name="run", replay_speed=1.0,
            vel_diverge=6.0, fit_seconds=None, with_scale=True, make_plot=True):
    """Full comparison. Returns a metrics dict; optionally draws the figure.

    fit_seconds: if set, Umeyama-fit only the first `fit_seconds` of the moving window
    (the rest is shown as error growth). Default None = fit the whole valid window.
    """
    v = load_vio_pose(pose_csv, replay_speed)
    fc = load_fc(fc_bin)
    # Align on the PRE-divergence part only: once VINS runs away its angular speed goes
    # wild and wrecks the cross-correlation. Divergence is detectable in VINS time alone
    # (velocity threshold), before we know the FC offset.
    dv = np.argmax(v["speed"].values > vel_diverge) if (v["speed"].values > vel_diverge).any() else len(v) - 1
    pre = v.iloc[:dv + 1] if dv >= 10 else v
    lag, ncc = align_time(pre["te"].values, pre["w"].values, fc["imu0"]["t_s"].values, fc["imu0"]["w"].values)
    v["tfc"] = v["te"] + lag
    t0, t_div, reason = find_window(v, fc, lag, vel_diverge=vel_diverge)

    xkf = fc["xkf"]
    win = v[(v["tfc"] >= t0) & (v["tfc"] <= t_div)].copy()
    gt = np.c_[np.interp(win["tfc"], xkf["t_s"], xkf["PN"]),
               np.interp(win["tfc"], xkf["t_s"], xkf["PE"]),
               np.interp(win["tfc"], xkf["t_s"], xkf["PD"])]
    vio = win[["px", "py", "pz"]].values

    fit_mask = np.ones(len(win), bool) if fit_seconds is None else (win["tfc"].values <= t0 + fit_seconds)
    if fit_mask.sum() >= 3:
        R, c, t = umeyama(vio[fit_mask], gt[fit_mask], with_scale)
    else:
        R, c, t = np.eye(3), 1.0, np.zeros(3)
    vio_al = (c * (R @ vio.T).T + t)
    err = np.linalg.norm(vio_al - gt, axis=1)

    # seconds VINS stayed within 1 m of EKF after onset -- a "usable tracking" figure
    within = win["tfc"].values[err <= 1.0]
    track_s = float(within.max() - t0) if len(within) else 0.0

    metrics = dict(
        run=run_name, align_lag_s=round(lag, 2), align_ncc=round(ncc, 3),
        window_fc_s=[round(t0, 1), round(float(t_div), 1)], window_reason=reason,
        n_samples=int(len(win)), umeyama_scale=round(float(c), 3),
        ate_rmse_m=round(float(np.sqrt((err ** 2).mean())), 3) if len(err) else None,
        ate_median_m=round(float(np.median(err)), 3) if len(err) else None,
        ate_max_m=round(float(err.max()), 3) if len(err) else None,
        usable_track_s=round(track_s, 1),
    )

    if make_plot:
        _plot(v, fc, xkf, win, gt, vio_al, err, t0, t_div, run_name, metrics)
    return metrics


def _plot(v, fc, xkf, win, gt, vio_al, err, t0, t_div, run_name, metrics):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle(f"{run_name}: VINS vs FC EKF  (align NCC {metrics['align_ncc']}, "
                 f"scale {metrics['umeyama_scale']}, ATE {metrics['ate_rmse_m']} m, "
                 f"usable {metrics['usable_track_s']}s)", fontweight="bold")

    # (0,0) altitude vs time: EKF -PD vs VINS-aligned, motors, window
    a = ax[0, 0]
    a.plot(xkf["t_s"], -xkf["PD"], label="EKF alt (-PD)", color="crimson", lw=1)
    a.plot(win["tfc"], -vio_al[:, 2], label="VINS alt (aligned)", color="steelblue", lw=1)
    rc = fc["F"].get("RCOU")
    if rc is not None:
        a2 = a.twinx(); a2.plot(rc["t_s"], rc["C1"], color="gray", lw=0.6, alpha=0.5); a2.set_ylabel("motor C1 (us)")
    a.axvspan(t0, t_div, color="green", alpha=0.08)
    a.set_xlim(t0 - 30, t_div + 30); a.set_xlabel("FC t_s (s)"); a.set_ylabel("alt (m)")
    a.legend(fontsize=8); a.grid(alpha=0.3); a.set_title("altitude & takeoff")

    # (0,1) top-down horizontal trajectory over window
    a = ax[0, 1]
    a.plot(gt[:, 1], gt[:, 0], label="EKF (PE,PN)", color="crimson", lw=1.5)
    a.plot(vio_al[:, 1], vio_al[:, 0], label="VINS (aligned)", color="steelblue", lw=1.5)
    a.scatter([gt[0, 1]], [gt[0, 0]], c="k", s=20, zorder=5, label="start")
    a.set_aspect("equal", "datalim"); a.set_xlabel("East (m)"); a.set_ylabel("North (m)")
    a.legend(fontsize=8); a.grid(alpha=0.3); a.set_title("horizontal path (valid window)")

    # (1,0) position error vs time
    a = ax[1, 0]
    a.plot(win["tfc"], err, color="purple", lw=1)
    a.axhline(1.0, color="orange", ls="--", lw=0.8, label="1 m")
    a.axvline(t_div, color="red", ls="--", lw=1, label=f"cut ({metrics['window_reason']})")
    a.set_xlabel("FC t_s (s)"); a.set_ylabel("|VINS-EKF| (m)")
    a.legend(fontsize=8); a.grid(alpha=0.3); a.set_title("position error over valid window")

    # (1,1) idle period: VINS flat vs EKF/GPS settling (before onset). VINS emits no
    # pose while dead-stationary (can't initialize), so idle can be empty -- guard it.
    a = ax[1, 1]
    idle = v[v["tfc"] < t0]
    if len(idle):
        a.plot(idle["tfc"], idle["px"] - idle["px"].iloc[0], label="VINS pN", lw=0.8)
        a.plot(idle["tfc"], idle["py"] - idle["py"].iloc[0], label="VINS pE", lw=0.8)
    xki = xkf[xkf["t_s"] < t0]
    if len(xki):
        a.plot(xki["t_s"], xki["PN"] - xki["PN"].iloc[0], label="EKF PN", color="crimson", lw=0.8, alpha=0.7)
        a.plot(xki["t_s"], xki["PE"] - xki["PE"].iloc[0], label="EKF PE", color="darkred", lw=0.8, alpha=0.7)
    if not len(idle):
        a.text(0.5, 0.5, "no idle VINS pose\n(no init while stationary)", ha="center",
               va="center", transform=a.transAxes, fontsize=9, color="gray")
    a.set_xlabel("FC t_s (s)"); a.set_ylabel("pos - start (m)")
    a.legend(fontsize=8); a.grid(alpha=0.3); a.set_title("idle: VINS still vs GPS/EKF settling")

    fig.tight_layout()
    return fig


if __name__ == "__main__":
    import sys
    m = compare(sys.argv[1], sys.argv[2], run_name=sys.argv[3] if len(sys.argv) > 3 else "run",
                replay_speed=float(sys.argv[4]) if len(sys.argv) > 4 else 1.0, make_plot=False)
    print(json.dumps(m, indent=2))
