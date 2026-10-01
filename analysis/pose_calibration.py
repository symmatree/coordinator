"""pose_calibration.py -- scale, bias and relative orientation from held poses.

A sensor held still measures gravity, whose magnitude is known. Hold it in several orientations
and the constraint |a| = 1 g in every one of them is enough to separate the per-axis scale from
the per-axis bias, which a single orientation cannot do: one orientation is one equation and
there are two unknowns per axis.

With two sensors on one body there is a second question -- their orientation relative to each
other -- and the same poses answer it, because both see the same gravity vector expressed in
their own frames.

WHAT THE SELECTION HAS TO RESPECT, which is most of the difficulty:

* **One contiguous span per pose, not the whole session.** A hand-held session is a few usable
  spans separated by positioning, hand shake and the operator settling into a hold. Fitting
  everything lets the transients dominate, which is backwards when the point is to calibrate
  from the quiet parts.
* **Take the span from the CENTRE of a held period, not the first window that qualifies.** A
  first-satisfying-window search lands on the leading edge of the criterion, where the transient
  is still decaying and the signal only just qualified -- collecting exactly the samples the
  criterion was meant to exclude. This is a property of any threshold-crossing selector.
* **A pose is a held ORIENTATION, not a quiet one.** Selecting on the windowed standard deviation
  of |a| was tried and is wrong here in a way worth recording: on 260926 the vehicle sitting on a
  table read a HIGHER standard deviation at the camera-colocated sensor (0.037-0.044 g) than the
  same sensor did while hand-held (0.022-0.028 g), so a quietness threshold rejected the longest
  held pose in the session. It is also the wrong quantity in principle: zero-mean vibration
  averages out of a mean, and at 3.2 kHz over 8 s the standard error on a direction is ~0.02
  degrees whether the standard deviation is 0.02 g or 0.04 g. What corrupts a pose is the
  direction DRIFTING during the window, so that is what is tested.

Sampling-rate drift across a session does not matter much here, which is worth stating because it
matters elsewhere: the measurement is the direction and magnitude of gravity while stationary,
not a frequency response, so a slowly wrong time axis does not move a static mean.
"""

import numpy as np
from scipy.optimize import least_squares


def unit_directions_per_second(capture):
    """Mean gravity direction over each whole second of a capture, as unit vectors (3, n)."""
    n = int(round(capture.rate_hz))
    k = len(capture.time_s) // n
    v = np.stack([c[:k * n].reshape(k, n).mean(axis=1) for c in (capture.x, capture.y, capture.z)])
    return v / np.linalg.norm(v, axis=0)


def held_spans(capture, tol_deg=2.0, min_s=10):
    """Spans (in whole seconds) over which the gravity direction stays within tol of its own mean.

    Greedy: extend a span while every second in it remains within `tol_deg` of the span's running
    mean direction. A pose ends where the operator moved the vehicle, which is a large change, so
    the tolerance only has to be smaller than the smallest deliberate pose change.
    """
    u = unit_directions_per_second(capture)
    k = u.shape[1]
    out, start = [], 0
    while start < k:
        end = start + 1
        while end < k:
            mean = u[:, start:end + 1].mean(axis=1)
            mean /= np.linalg.norm(mean)
            ang = np.degrees(np.arccos(np.clip(u[:, start:end + 1].T @ mean, -1, 1)))
            if ang.max() > tol_deg:
                break
            end += 1
        if end - start >= min_s:
            out.append((start, end - 1))
        start = end if end > start else start + 1
    return out


def common_held_spans(captures, tol_deg=2.0, min_s=10, after_s=None, before_s=None):
    """Spans where EVERY capture holds one orientation -- a pose is a property of the vehicle.

    These are HELD SPANS, not poses, and the difference is about NOISE CONTROL rather than
    validity. On 260926 the vehicle sat on a table for the first 283 s while the operator set up,
    and again for 88 s after a bump test. Those intervals are usable -- the vehicle was where it
    was put and its direction is as stable as any deliberate pose -- but nobody was damping it,
    and the operator had his feet on the table and was typing, so expect interfering noise from
    being kicked rather than a quiet record. The bump-test interval is different in kind: the
    vehicle was held down and struck, and the strikes moved it, so the orientation before and
    after is not the same one.

    Taking them for deliberate poses cost two errors. The 0.46 deg between the two upright spans
    was read as evidence that a sensor mount had shifted, when the vehicle had simply been struck
    between them. And the two extra rows let a 6-parameter fit report a 0.0006 g residual that
    looked like precision and was slack.

    `after_s` and `before_s` restrict the search to the part of the session where the procedure
    was actually being performed. WHICH SPANS ARE POSES IS OPERATOR KNOWLEDGE and is not
    recoverable from the signal: an orientation held because someone is holding it and one held
    because the vehicle is sitting on a bench look identical here.
    """
    per = [held_spans(c, tol_deg, min_s) for c in captures]
    if after_s is not None or before_s is not None:
        lo = after_s if after_s is not None else -np.inf
        hi = before_s if before_s is not None else np.inf
        # CLIP, do not discard. A held span usually starts before the procedure does -- the
        # vehicle was already sitting where it was put -- so dropping any span that begins
        # before the cutoff throws away the pose instead of trimming the setup off its front.
        clipped = []
        for spans in per:
            keep = []
            for a, b in spans:
                a2, b2 = int(np.ceil(max(a, lo))), int(np.floor(min(b, hi)))
                if b2 - a2 + 1 >= min_s:
                    keep.append((a2, b2))
            clipped.append(keep)
        per = clipped
    k = min(len(unit_directions_per_second(c)[0]) for c in captures)
    held = np.ones(k, bool)
    for spans in per:
        m = np.zeros(k, bool)
        for a, b in spans:
            m[a:b + 1] = True
        held &= m
    out, start = [], None
    for i, q in enumerate(list(held) + [False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if i - start >= min_s:
                out.append((start, i - 1))
            start = None
    return out


def centre_windows(spans, window_s):
    """The requested duration taken from the CENTRE of each span. See the module docstring."""
    out = []
    for a, b in spans:
        mid = (a + b) / 2.0
        if b - a + 1 < window_s:
            continue
        out.append((mid - window_s / 2.0, mid + window_s / 2.0))
    return out


def pose_vectors(capture, windows):
    """Mean (x, y, z) over each window, plus the standard error of each mean."""
    t = capture.time_s - capture.time_s[0]
    means, errs = [], []
    for w0, w1 in windows:
        m = (t >= w0) & (t <= w1)
        v = np.array([capture.x[m], capture.y[m], capture.z[m]])
        means.append(v.mean(axis=1))
        errs.append(v.std(axis=1) / np.sqrt(m.sum()))
    return np.array(means), np.array(errs)


def fit_scale_bias(vectors, g=1.0):
    """Per-axis scale and bias such that |scale * (raw - bias)| == g in every pose.

    Six unknowns. Six orientations determine them exactly, which means the fit's own residual is
    NOT evidence that the model is right -- with no spare degrees of freedom it can only report
    how inconsistent any near-duplicate poses were. Redundancy needs a seventh INDEPENDENT
    orientation, and two poses a fraction of a degree apart do not count as two.
    """
    v = np.asarray(vectors, float)
    ind = pose_independence(v)
    if ind["n_independent"] < 6:
        # Refuse rather than return something. Six parameters cannot be recovered from fewer than
        # six independent orientations, and a solver asked anyway returns whichever member of the
        # solution family it walked into -- a number with no error bar and no way to notice.
        return dict(scale=None, bias=None, residual_g=None, rms_g=None,
                    n_poses=len(v), n_parameters=6,
                    n_independent_poses=ind["n_independent"],
                    min_separation_deg=ind["min_separation_deg"],
                    exactly_determined=False, underdetermined=True,
                    short_by=6 - ind["n_independent"])

    def residual(p):
        return np.linalg.norm((v - p[3:]) * p[:3], axis=1) - g

    r = least_squares(residual, [1, 1, 1, 0, 0, 0], method="lm")
    scale, bias = r.x[:3], r.x[3:]
    return dict(scale=scale, bias=bias, residual_g=residual(r.x), underdetermined=False,
                rms_g=float(np.sqrt((residual(r.x) ** 2).mean())),
                n_poses=len(v), n_parameters=6,
                n_independent_poses=ind["n_independent"],
                min_separation_deg=ind["min_separation_deg"],
                # Counting poses is not counting constraints. Two poses a fraction of a degree
                # apart are one observation made twice, and a fit with no spare degrees of
                # freedom cannot use its own residual as evidence that the model is right.
                exactly_determined=bool(ind["n_independent"] <= 6))


def pose_independence(vectors, min_deg=5.0):
    """How many of these orientations are actually distinct, and by how little.

    On 260926 the unrestricted search returns seven spans, two of which are within 0.5 degrees of
    each other -- the table before and after a bump test. Those two add a row each and almost no
    constraint. The five deliberate poses, by contrast, are at least 82 degrees apart, and five is
    one short of what a 6-parameter fit needs.
    """
    v = np.asarray(vectors, float)
    u = v / np.linalg.norm(v, axis=1, keepdims=True)
    cos = np.clip(u @ u.T, -1, 1)
    ang = np.degrees(np.arccos(cos))
    off = ang[~np.eye(len(u), dtype=bool)]
    kept = []
    for i in range(len(u)):
        if all(np.degrees(np.arccos(np.clip(u[i] @ u[j], -1, 1))) >= min_deg for j in kept):
            kept.append(i)
    return dict(n_independent=len(kept), independent_indices=kept,
                min_separation_deg=float(off.min()), pairwise_deg=ang)


MODEL_LADDER = {
    "bias_only": dict(n=3, x0=[0, 0, 0],
                      resid=lambda v, p: np.linalg.norm(v - p[:3], axis=1) - 1.0,
                      report=lambda p: dict(scale=[1.0, 1.0, 1.0], bias=list(p[:3]))),
    "global_scale_only": dict(n=1, x0=[1.0],
                              resid=lambda v, p: np.linalg.norm(v * p[0], axis=1) - 1.0,
                              report=lambda p: dict(scale=[float(p[0])] * 3, bias=[0.0] * 3)),
    "global_scale_and_bias": dict(n=4, x0=[1, 0, 0, 0],
                                  resid=lambda v, p: np.linalg.norm((v - p[1:4]) * p[0], axis=1) - 1.0,
                                  report=lambda p: dict(scale=[float(p[0])] * 3, bias=list(p[1:4]))),
    "axis_scale_and_bias": dict(n=6, x0=[1, 1, 1, 0, 0, 0],
                                resid=lambda v, p: np.linalg.norm((v - p[3:]) * p[:3], axis=1) - 1.0,
                                report=lambda p: dict(scale=list(p[:3]), bias=list(p[3:]))),
}


def model_ladder(vectors, se_g=None):
    """Fit every model from 1 to 6 parameters and report each residual against its spare dof.

    This exists because "six poses, six unknowns, so the residual tells you nothing" is true and
    is not the end of the question. Smaller models are over-observed by the same six poses and CAN
    be tested, so the ladder says which terms the data actually requires rather than leaving the
    6-parameter fit unfalsifiable.

    On 260926 it decides something: a global scale with per-axis bias -- 4 parameters, 2 spare
    degrees of freedom -- leaves 0.0100 g rms on one channel and 0.0114 g on the other, against
    per-pose standard errors of 0.00034 g and 0.00021 g. Thirty to fifty times the noise, so the
    per-axis scale terms are necessary and the 6-parameter fit is not merely absorbing noise. The
    same ladder shows the two parts differ in kind: the arm's global scale fits at 0.9994 and
    buys nothing over bias alone, while the camera's is 0.9824 and halves the residual.

    Pass `se_g` (the per-pose standard error of |a|) to get the ratio that makes a residual
    interpretable; a residual is only large or small relative to the measurement.
    """
    v = np.asarray(vectors, float)
    out = {}
    for name, spec in MODEL_LADDER.items():
        r = least_squares(lambda p: spec["resid"](v, p), spec["x0"], method="lm")
        resid = spec["resid"](v, r.x)
        rms = float(np.sqrt((resid ** 2).mean()))
        out[name] = dict(n_parameters=spec["n"], dof=len(v) - spec["n"], rms_g=rms,
                         max_g=float(np.abs(resid).max()),
                         rms_over_noise=(rms / se_g) if se_g else None, **spec["report"](r.x))
    return out


def apply_calibration(vectors, scale, bias):
    return (np.asarray(vectors, float) - bias) * scale


def relative_rotation(a_vectors, b_vectors):
    """Single rotation R with R @ a ~= b, fitted over all poses (Kabsch), plus its residual.

    The residual is the test of whether one rigid rotation explains the pair. Note what is NOT a
    test: the angle between a sensor's gravity vector and the other's is not rotation-invariant --
    it depends on where gravity lies relative to the rotation axis -- so that angle varying across
    poses says nothing. On 260926 it ranged over 80 degrees for a pair whose rigid-rotation
    residual is ~1 degree.
    """
    a = np.asarray(a_vectors, float); a = a / np.linalg.norm(a, axis=1, keepdims=True)
    b = np.asarray(b_vectors, float); b = b / np.linalg.norm(b, axis=1, keepdims=True)
    h = a.T @ b
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    r = vt.T @ np.diag([1, 1, d]) @ u.T
    per_pose = np.degrees(np.arccos(np.clip(np.sum((a @ r.T) * b, axis=1), -1, 1)))
    angle = float(np.degrees(np.arccos(np.clip((np.trace(r) - 1) / 2, -1, 1))))
    axis = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]])
    norm = np.linalg.norm(axis)
    return dict(R=r, angle_deg=angle, axis=(axis / norm) if norm > 1e-12 else np.array([0., 0., 1.]),
                residual_deg=per_pose, rms_deg=float(np.sqrt((per_pose ** 2).mean())),
                max_deg=float(per_pose.max()))


def _main(argv=None):
    """CLI so a job can produce a calibration, rather than numbers being copied from a session."""
    import argparse
    import json
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ringdown import load_accel_jsonl

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("capture_dir", help="directory holding accel-*.jsonl for one pod session")
    ap.add_argument("--window-s", type=float, default=8.0,
                    help="duration taken from the CENTRE of each held span (default 8)")
    ap.add_argument("--tol-deg", type=float, default=2.0,
                    help="how far the direction may wander inside one held span (default 2)")
    ap.add_argument("--min-hold-s", type=int, default=10, help="shortest usable span (default 10)")
    ap.add_argument("--after-s", type=float, default=None,
                    help="ignore held spans before this time. A capture normally contains the "
                         "setup period, which holds an orientation without being a pose.")
    ap.add_argument("--before-s", type=float, default=None, help="ignore held spans after this time")
    ap.add_argument("--json", metavar="PATH", help="write the result here")
    args = ap.parse_args(argv)

    import glob
    paths = sorted(glob.glob(os.path.join(args.capture_dir, "accel-*.jsonl")))
    if not paths:
        raise SystemExit(f"no accel-*.jsonl under {args.capture_dir}")
    caps = {os.path.basename(p)[len("accel-"):-len(".jsonl")]: load_accel_jsonl(p) for p in paths}

    spans = common_held_spans(list(caps.values()), args.tol_deg, args.min_hold_s,
                              args.after_s, args.before_s)
    if args.after_s is None and args.before_s is None:
        print("NOTE: --after-s/--before-s not given, so every held span is treated as a pose.\n"
              "      A capture usually contains setup and handling time that holds an orientation\n"
              "      without anyone intending it to. Those spans are indistinguishable from poses\n"
              "      in the signal, and including them puts slack into the fit.")
    windows = centre_windows(spans, args.window_s)
    print(f"{len(spans)} span(s) where every channel holds one orientation for "
          f"{args.min_hold_s}+ s -> {len(windows)} usable")
    for (a, b), (w0, w1) in zip(spans, windows):
        print(f"   held {a:7.1f}-{b:7.1f} s  ->  centred {w0:7.1f}-{w1:7.1f} s")

    out = dict(capture=args.capture_dir, window_s=args.window_s,
               held_spans_s=[[float(a), float(b)] for a, b in spans],
               windows_s=[[float(a), float(b)] for a, b in windows], channels={})
    vectors = {}
    for label, cap in caps.items():
        v, se = pose_vectors(cap, windows)
        vectors[label] = v
        fit = fit_scale_bias(v)
        if fit.get("underdetermined"):
            print(f"\n{label}: NOT FITTED. {fit['n_independent_poses']} independent orientation(s), "
                  f"6 parameters -- short by {fit['short_by']}.")
            print(f"   Scale and bias are not separable from this many orientations. Another "
                  f"{fit['short_by']} well-separated held pose(s) would do it.")
            out["channels"][label] = dict(
                underdetermined=True, short_by=fit["short_by"],
                n_poses=fit["n_poses"], n_independent_poses=fit["n_independent_poses"],
                pose_vectors_g=[[float(x) for x in row] for row in v],
                pose_direction_se_deg=float(np.degrees(se.max())))
            continue
        se_mag = float(np.max([np.linalg.norm(e) for e in se]))
        ladder = model_ladder(v, se_g=se_mag)
        print(f"\n{label}: model ladder over {len(v)} poses "
              f"(|a| standard error {se_mag:.5f} g per pose)")
        print(f"   {'model':24s} {'par':>3s} {'dof':>4s} {'rms (g)':>9s} {'x noise':>8s}")
        for name, d in ladder.items():
            print(f"   {name:24s} {d['n_parameters']:3d} {d['dof']:4d} {d['rms_g']:9.5f}"
                  f" {d['rms_over_noise']:8.0f}")
        print("   A model with spare degrees of freedom CAN be wrong, so these are the ones that")
        print("   carry information. The 6-parameter fit's own residual is arithmetic.")
        out["channels"][label] = dict(out["channels"].get(label, {}), model_ladder={
            k: {kk: vv for kk, vv in d.items()} for k, d in ladder.items()})
        print(f"\n{label}: scale {np.round(fit['scale'], 4)}  bias {np.round(fit['bias'], 4)} g")
        print(f"   {fit['n_poses']} poses, {fit['n_independent_poses']} independent "
              f"(closest pair {fit['min_separation_deg']:.2f} deg apart)")
        if fit["exactly_determined"]:
            print("   EXACTLY DETERMINED: 6 parameters, 6 independent orientations. The residual "
                  "below is not evidence the model is right -- there are no spare degrees of "
                  "freedom for it to be wrong in. A seventh well-separated pose would give one.")
        print(f"   rms |a|-1 after correction {fit['rms_g']:.5f} g")
        out["channels"][label] = dict(
            scale=[float(x) for x in fit["scale"]], bias=[float(x) for x in fit["bias"]],
            rms_g=fit["rms_g"], n_poses=fit["n_poses"],
            n_independent_poses=fit["n_independent_poses"],
            min_separation_deg=fit["min_separation_deg"],
            exactly_determined=fit["exactly_determined"],
            pose_vectors_g=[[float(x) for x in row] for row in v],
            pose_direction_se_deg=float(np.degrees(se.max())),
        )

    labels = sorted(caps)
    if len(labels) == 2:
        a, b = labels
        fa, fb = fit_scale_bias(vectors[a]), fit_scale_bias(vectors[b])
        if fa.get("underdetermined") or fb.get("underdetermined"):
            print(f"\n{a} -> {b}: from RAW directions, because scale and bias were not solved. "
                  "A rotation needs only directions, but uncorrected directions carry the scale "
                  "and bias error, so the residual below is an upper bound and not a measure of "
                  "how rigid the pair is.")
            rot = relative_rotation(vectors[a], vectors[b])
        else:
            rot = relative_rotation(apply_calibration(vectors[a], fa["scale"], fa["bias"]),
                                    apply_calibration(vectors[b], fb["scale"], fb["bias"]))
        print(f"\n{a} -> {b}: {rot['angle_deg']:.2f} deg about "
              f"({rot['axis'][0]:+.3f},{rot['axis'][1]:+.3f},{rot['axis'][2]:+.3f})")
        print(f"   residual rms {rot['rms_deg']:.3f} deg, max {rot['max_deg']:.3f} deg, "
              f"against a per-pose direction standard error near 0.02 deg")
        print("   A residual far above that standard error means one rigid rotation plus per-axis "
              "scale and bias does not describe this pair -- candidates are cross-axis "
              "sensitivity, which this model has no term for, and the pair not being rigid.")
        out["relative_rotation"] = dict(
            from_=a, to=b, angle_deg=rot["angle_deg"],
            axis=[float(x) for x in rot["axis"]], R=[[float(x) for x in r] for r in rot["R"]],
            residual_deg=[float(x) for x in rot["residual_deg"]],
            rms_deg=rot["rms_deg"], max_deg=rot["max_deg"])

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=1)
        print(f"\nwrote {args.json}")
    return out


if __name__ == "__main__":
    _main()


def fc_pose_vectors(bin_path, windows, instance=0, g=9.80665):
    """Gravity per pose from an ArduPilot dataflash `IMU` stream, in g.

    The third instrument. Its accel is already calibrated by ArduPilot, so a scale-and-bias fit
    over the same poses should come out near unity and near zero -- which makes it a usable
    reference rather than another unknown, and a check on this module at the same time.

    `windows` are in the dataflash's own TimeUS seconds from the first IMU record, not in the
    campod's capture time. The two do not share a clock: see fc_absolute_time.
    """
    from pymavlink import mavutil
    conn = mavutil.mavlink_connection(str(bin_path), robust_parsing=True)
    rows = []
    while True:
        msg = conn.recv_match(type="IMU", blocking=False)
        if msg is None:
            break
        if msg.I == instance:
            rows.append((msg.TimeUS / 1e6, msg.AccX, msg.AccY, msg.AccZ))
    a = np.array(rows)
    if not len(a):
        raise ValueError(f"{bin_path}: no IMU records for instance {instance}")
    t = a[:, 0] - a[0, 0]
    means, errs = [], []
    for w0, w1 in windows:
        m = (t >= w0) & (t <= w1)
        v = a[m, 1:].T / g
        means.append(v.mean(axis=1))
        errs.append((v.std(axis=1) / np.sqrt(m.sum())).max())
    return np.array(means), np.array(errs)


def fc_absolute_time(bin_path):
    """When the dataflash's TimeUS zero was, in UTC, and how fast its clock runs.

    Fitted over every 3D-fix `GPS` record rather than read off the first one: the fit averages
    the receiver's own jitter and, more usefully, its SLOPE is the FC clock's rate error against
    GPS, which is the only absolute time reference anywhere on this vehicle.

    The log's FILENAME is not this. The FC has no RTC, so a file created before GPS lock is named
    from a 1980 epoch; the GPS week only appears in the records once locked. On 260926 the
    filename says 1980-01-12 and the records say week 2437, day 6 -- Saturday 2026-09-26.

    Returns dict(t0_utc, rate_error_ppm, residual_ms, n_records).
    """
    import datetime
    from pymavlink import mavutil
    conn = mavutil.mavlink_connection(str(bin_path), robust_parsing=True)
    rows = []
    while True:
        msg = conn.recv_match(type="GPS", blocking=False)
        if msg is None:
            break
        if getattr(msg, "Status", 0) >= 3 and msg.GWk > 0:
            rows.append((msg.TimeUS / 1e6, msg.GWk, msg.GMS / 1000.0))
    a = np.array(rows)
    if len(a) < 10:
        raise ValueError(f"{bin_path}: too few locked GPS records to fit a time base")
    t, week, sow = a[:, 0], a[:, 1], a[:, 2]
    slope, intercept = np.polyfit(t, sow, 1)
    residual = sow - (slope * t + intercept)
    epoch = datetime.datetime(1980, 1, 6, tzinfo=datetime.timezone.utc)
    t0 = (epoch + datetime.timedelta(weeks=float(week[0]), seconds=float(intercept))
          - datetime.timedelta(seconds=18))      # GPS-UTC leap seconds as of 2026
    return dict(t0_utc=t0, rate_error_ppm=float((slope - 1.0) * 1e6),
                residual_ms=float(residual.std() * 1000), n_records=int(len(a)))
