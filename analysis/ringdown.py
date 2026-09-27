"""ringdown.py -- frequency AND damping from a bump test, one tap at a time.

A spectrum of a running motor shows the forcing frequencies and reveals a resonance only
where a harmonic happens to land on one, so it cannot map a structure
(`docs/rekon10/vibration-testing.md` sec. 3). An impulse with the motors off can: excite the
structure, watch it ring down, and read the mode frequency off the oscillation and the damping
off the decay envelope.

**Damping is the number this exists for.** A lightly-damped mode near a rotor harmonic is a
problem; the same frequency heavily damped is not, and nothing measurable in flight
distinguishes them. A running-motor spectrum gives frequency at best; only a ringdown gives
zeta.

Three method rules from that doc, enforced here rather than left to the caller:

* **Each tap is fitted separately.** Welch-averaging across taps smears the transients together
  and throws away the fact that every tap is an independent estimate of both quantities. The
  scatter across taps is the error bar, and there is no other honest source of one.
* **A tap with a poor fit is reported, not dropped.** `r2` and the fitted window are returned
  per tap so a caller can see what was rejected and why.
* **The two arms are each other's control.** They should be identical but mirrored, so a mode
  on one and not the other is an asymmetry -- a loose fastener, a cracked layer, a different
  cable route -- rather than a property of the design. `compare_arms` does that pairing.

Method: band-pass around a candidate mode, take the analytic-signal envelope, and fit
log|envelope| against time over the decay. The slope is -zeta*omega_n, which with the measured
damped frequency gives zeta directly. Frequency comes from the band-passed segment's own
spectrum, not from the band centre, so the band only has to bracket the mode.

Units: the input is acceleration in any consistent unit -- the results are a frequency and a
dimensionless ratio, so the scale factor cancels and an uncalibrated sensor is fine here.
This is the one measurement on this vehicle that does NOT need the 10.4% inter-part scale
discrepancy resolved first.
"""

import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt, welch

# A mode is worth fitting only over the part of the decay that is above the noise. Fitting into
# the floor bends the log-envelope flat and biases zeta low -- towards "lightly damped", which
# is the direction that would wrongly raise an alarm.
DEFAULT_DECAY_DB = 20.0
# A heavily damped mode rings for few cycles BY DEFINITION -- that is the measurement, not a
# defect -- so this cannot be set high enough to make a light mode comfortable. At zeta = 0.08
# a 20 dB decay is only 4.6 cycles, and a 5-cycle floor silently rejected exactly the case this
# module exists to distinguish from a lightly damped one at the same frequency.
MIN_CYCLES = 3.0
# An empty band is not harmless: the band-pass filter's own transient response to the impulse
# decays smoothly, so fitting a band that contains no mode yields a confident exponential out
# of the filter rather than out of the structure. The band has to be shown to contain something.
MIN_PROMINENCE_DB = 6.0
# A bump test is silence punctuated by impulses; a running motor is not. Without this check the
# tap finder fires on the amplitude fluctuations of a STEADY source and every fit then reports a
# forcing line as a mode, with a plausible zeta and Q attached. Measured on the 2026-09-15 bench
# captures: a steady fan gives peak/median 11.8, while synthetic 5-tap records give 375 (zeta
# 0.02) and 647 (zeta 0.08). Run on the fan capture without it, this module confidently returned
# "modes" at 37.3 / 110.1 / 147.1 / 183.3 / 441.3 Hz -- which are exactly that fan's shaft
# harmonics (36.81 / 110.44 / 146.85 / 183.66 / 440.95, vibration-testing.md sec. 1).
MIN_PEAK_OVER_MEDIAN = 50.0


def find_taps(t, a, n_expected=None, min_peak_frac=0.25, min_sep_s=0.2, pre_s=0.01):
    """Locate impulses in a record. Returns a list of (i_start, i_peak), earliest first.

    Peak-picking with suppression: take the largest excursion, blank +/-`min_sep_s` around it,
    repeat while anything remains above `min_peak_frac` of the largest. `min_peak_frac` is a
    FRACTION OF THIS RECORD'S OWN LARGEST PEAK -- not a count of standard deviations -- so it
    carries no units and does not change meaning when the sensor's scale does.

    This replaced a threshold-above-the-noise-floor detector, which was wrong for the case it
    exists to handle. That version marked every sample above a multiple of the median absolute
    deviation and split the marked samples on quiet gaps, so a record whose DECAY never falls
    back below the threshold has consecutive impulses merge into one group and only the loudest
    of each merged run gets fitted. Measured on 260926: for the same fifteen physical strikes it
    returned 2 impulses on one channel and 22 on the other, purely because one channel's decay
    stayed hot between blows. Peak-picking is insensitive to that, because it looks for maxima
    rather than for regions.

    `pre_s` backs the returned start up before the peak so a caller sees the rise, and
    `n_expected`, if given, keeps only that many of the largest.
    """
    t = np.asarray(t, float)
    a = np.asarray(a, float)
    x = np.abs(a - np.median(a))
    if not len(x) or x.max() <= 0:
        return []
    floor = min_peak_frac * x.max()
    work = x.copy()
    peaks = []
    while True:
        i = int(np.argmax(work))
        if work[i] < floor:
            break
        peaks.append(i)
        blank = (t >= t[i] - min_sep_s) & (t <= t[i] + min_sep_s)
        work[blank] = -np.inf
        if n_expected is not None and len(peaks) >= n_expected:
            break
    taps = []
    for i_peak in sorted(peaks):
        i_start = int(np.searchsorted(t, t[i_peak] - pre_s))
        taps.append((max(0, i_start), int(i_peak)))
    return taps


def looks_like_a_bump_test(a, min_ratio=MIN_PEAK_OVER_MEDIAN):
    """Is this record impulses-in-silence, or a running source? Returns a dict with the number.

    The discriminator is peak over MEDIAN absolute level, not crest factor: crest barely
    separates the two cases (5.9 vs 7.2 on real data) because a steady source has plenty of
    instantaneous peaks, while the median level differs by more than an order of magnitude
    because a bump test is mostly quiet and a running motor never is.
    """
    a = np.asarray(a, float)
    a = a - np.median(a)
    med = float(np.median(np.abs(a)))
    pk = float(np.percentile(np.abs(a), 99.99))
    rms = float(np.sqrt(np.mean(a ** 2))) or 1.0
    ratio = pk / med if med > 0 else float("inf")
    return dict(peak_over_median=ratio, crest=pk / rms, ok=bool(ratio >= min_ratio),
                threshold=min_ratio)


def fit_ringdown(t, a, f_lo, f_hi, decay_db=DEFAULT_DECAY_DB, min_cycles=MIN_CYCLES,
                 min_prominence_db=MIN_PROMINENCE_DB):
    """Fit one tap's decay inside [f_lo, f_hi]. Returns a dict, or None if it cannot be fitted.

    `zeta` is the damping ratio, `q` the quality factor, `f_hz` the damped natural frequency
    measured from the segment itself. `r2` is the fit of log-envelope against time -- a real
    single-mode ringdown is close to 1, and a value well below it means the band holds more
    than one mode or the tap never rang.
    """
    t = np.asarray(t, float)
    a = np.asarray(a, float)
    if len(t) < 64:
        return None
    fs = (len(t) - 1) / (t[-1] - t[0])
    nyq = fs / 2.0
    if not (0 < f_lo < f_hi < nyq):
        return None
    # Is there anything in this band at all? Checked on the UNFILTERED segment, because after
    # band-passing there is always something -- the filter's own ringing.
    nper = min(len(a), 4096)
    fr, pr = welch(a - a.mean(), fs=fs, nperseg=nper, noverlap=nper // 2)
    inb_raw = (fr >= f_lo) & (fr <= f_hi)
    if not inb_raw.any():
        return None
    db = 10 * np.log10(np.maximum(pr, 1e-30))
    prominence = float(db[inb_raw].max() - np.median(db[fr >= f_lo * 0.25]))
    if prominence < min_prominence_db:
        return None

    sos = butter(4, [f_lo / nyq, f_hi / nyq], btype="bandpass", output="sos")
    y = sosfiltfilt(sos, a - a.mean())

    # frequency from the segment's own spectrum, so the band only has to bracket the mode
    f, p = welch(y, fs=fs, nperseg=nper, noverlap=nper // 2)
    inb = (f >= f_lo) & (f <= f_hi)
    if not inb.any():
        return None
    f_hz = float(f[inb][np.argmax(p[inb])])

    env = np.abs(hilbert(y))
    i0 = int(np.argmax(env))
    env, t2 = env[i0:], t[i0:]
    if len(env) < 32:
        return None
    peak = env[0]
    if peak <= 0:
        return None
    # fit only while the envelope is still above the floor by `decay_db`
    lim = peak * 10 ** (-decay_db / 20.0)
    below = np.flatnonzero(env < lim)
    i1 = int(below[0]) if len(below) else len(env)
    if (t2[min(i1, len(t2) - 1)] - t2[0]) * f_hz < min_cycles:
        return None
    env, t2 = env[:i1], t2[:i1]
    good = env > 0
    if good.sum() < 16:
        return None
    lg = np.log(env[good])
    tt = t2[good] - t2[0]
    slope, icept = np.polyfit(tt, lg, 1)
    if slope >= 0:
        return None
    resid = lg - (icept + slope * tt)
    ss = float(np.sum((lg - lg.mean()) ** 2))
    r2 = float(1.0 - np.sum(resid ** 2) / ss) if ss > 0 else 0.0

    # slope = -zeta*omega_n and f_hz = f_n*sqrt(1-zeta^2); solve without assuming zeta is small
    sigma = -float(slope)
    wd = 2 * np.pi * f_hz
    wn = float(np.hypot(sigma, wd))
    zeta = sigma / wn
    return dict(f_hz=f_hz, f_n_hz=wn / (2 * np.pi), zeta=float(zeta),
                q=float(1.0 / (2 * zeta)) if zeta > 0 else float("inf"),
                decay_s=float(1.0 / sigma), r2=r2, n=int(good.sum()),
                prominence_db=prominence,
                fit_span_s=float(tt[-1]), cycles=float(tt[-1] * f_hz), peak=float(peak))


def candidate_bands(t, a, fmin=20.0, fmax=None, n=None, rel_width=0.06, prominence_db=6.0):
    """Pick bands to fit, from the peaks of the record's spectrum.

    Returns a list of (f_lo, f_hi) bracketing each candidate, in frequency order. `rel_width` is
    a fraction of the peak frequency, so a band scales with the mode rather than being a fixed
    number of Hz.

    **`n` defaults to no limit.** It was a hard default of 6, and that count was deciding which
    modes existed: a peak was dropped because five others were more prominent, which says nothing
    about the peak. Measured on 260926, a peak near 153 Hz fitted on multiple impulses on both
    channels sat outside the top six and so was never fitted at all. Prominence and the
    non-overlap rule decide; a caller passing `n` gets a safety valve, and should check whether it
    bound.

    **Prominence is measured against the median of the searched range**, which assumes the noise
    floor is roughly flat across it. On 260926 it is (-44 to -14 dB with a median of -37). A
    rolling LOCAL baseline was tried instead, to remove the dependence on `fmax`, and it fails on
    this data: the modes here are broad humps rather than sharp lines, so a window narrow enough
    to be local still sits on the peak's own shoulder. The 153 Hz peak reads +12.6 dB against the
    global median, +5.8 dB against a 99 Hz-wide local one and +4.7 dB against a 25 Hz one -- so a
    local baseline found no peaks at all at any width under ~400 Hz. Kept global, with the
    limitation stated: widening `fmax` into a noisier region raises the median and can drop a
    peak that has not changed.
    """
    t = np.asarray(t, float)
    a = np.asarray(a, float)
    fs = (len(t) - 1) / (t[-1] - t[0])
    fmax = fmax or 0.45 * fs
    nper = min(len(a), 8192)
    f, p = welch(a - a.mean(), fs=fs, nperseg=nper, noverlap=nper // 2)
    m = (f >= fmin) & (f <= fmax)
    f, p = f[m], p[m]
    if len(p) < 8:
        return []
    db = 10 * np.log10(np.maximum(p, 1e-30))
    prominence = db - np.median(db)
    idx = [i for i in range(2, len(db) - 2)
           if db[i] == max(db[i - 2:i + 3]) and prominence[i] > prominence_db]
    idx.sort(key=lambda i: -prominence[i])
    out = []
    for i in idx:
        fc = float(f[i])
        # Bands must not overlap. A damped mode has a BROAD spectral peak, so the picker finds
        # several bins on the same hump; overlapping bands then each fit a skirt of that one
        # mode and it is reported two or three times at slightly wrong frequencies, with a worse
        # r2 each time. Require a full band-width of separation, not half of one.
        if any(abs(fc - c) < 2.2 * rel_width * max(fc, c) for c, _ in out):
            continue
        out.append((fc, float(prominence[i])))
        if n is not None and len(out) >= n:
            break
    return [(c * (1 - rel_width), c * (1 + rel_width)) for c, _ in sorted(out)]


def analyze(t, a, bands=None, n_expected=None, post_s=0.5, tap_kw=None, **kw):
    """Full pipeline for one channel: find taps, fit every candidate band in each.

    Returns dict(taps=[{t_s, modes=[fit, ...]}, ...], bands=[...]). Every tap appears, including
    ones where nothing fitted, so a caller can see the attempt rate rather than only successes.

    `tap_kw` goes to find_taps and `**kw` goes to fit_ringdown. They are separated because the
    detection threshold is not a fit parameter and a caller has to be able to set it: on a real
    capture the default floor merged consecutive strikes on one sensor (2 groups) while splitting
    them on the other (21), so the two sensors fitted different impulse sets and nothing could be
    compared between them. Reaching find_taps through **kw was impossible, which hid that.
    """
    t = np.asarray(t, float)
    a = np.asarray(a, float)
    taps = find_taps(t, a, n_expected=n_expected, **(tap_kw or {}))
    if bands is None:
        bands = candidate_bands(t, a)
    out = []
    for i_start, i_peak in taps:
        i_end = int(np.searchsorted(t, t[i_peak] + post_s))
        seg_t, seg_a = t[i_start:i_end], a[i_start:i_end]
        modes = []
        for f_lo, f_hi in bands:
            r = fit_ringdown(seg_t, seg_a, f_lo, f_hi, **kw)
            if r is not None:
                r["band"] = [f_lo, f_hi]
                modes.append(r)
        out.append(dict(t_s=float(t[i_peak]), modes=modes))
    return dict(taps=out, bands=[list(b) for b in bands], n_taps=len(taps))


# A real single-mode ringdown fits log-linear to r2 > 0.99. Fits that catch the SKIRT of a mode
# from a neighbouring band land at 0.85-0.90 and look respectable, so the threshold has to sit
# between those, not below both. Measured on synthetic 6-tap records: true mode 0.994, skirts
# 0.856-0.877.
R2_MIN = 0.95


def summarize(result, r2_min=R2_MIN, tol_rel=0.03):
    """Group the per-tap fits into modes and report the scatter ACROSS taps.

    The scatter is the error bar: each tap is an independent estimate, so a mode seen once is
    not a mode. Fits below `r2_min` are counted and excluded, never silently dropped.
    """
    fits = [m for tap in result["taps"] for m in tap["modes"]]
    kept = [m for m in fits if m["r2"] >= r2_min]
    rejected = len(fits) - len(kept)
    groups = []
    for m in sorted(kept, key=lambda m: m["f_hz"]):
        for g in groups:
            if abs(m["f_hz"] - np.mean([x["f_hz"] for x in g])) < tol_rel * m["f_hz"]:
                g.append(m)
                break
        else:
            groups.append([m])
    modes = []
    for g in groups:
        f = np.array([x["f_hz"] for x in g])
        z = np.array([x["zeta"] for x in g])
        modes.append(dict(
            n_taps=len(g),
            f_hz=float(f.mean()), f_sd_hz=float(f.std(ddof=1)) if len(f) > 1 else float("nan"),
            zeta=float(z.mean()), zeta_sd=float(z.std(ddof=1)) if len(z) > 1 else float("nan"),
            q=float(1.0 / (2 * z.mean())) if z.mean() > 0 else float("inf"),
            decay_s=float(np.mean([x["decay_s"] for x in g])),
            r2_median=float(np.median([x["r2"] for x in g]))))
    modes.sort(key=lambda m: -m["n_taps"])
    return dict(modes=modes, n_fits=len(fits), n_kept=len(kept), n_rejected_low_r2=rejected,
                r2_min=r2_min)


def compare_arms(summary_a, summary_b, tol_rel=0.03):
    """Pair modes across two nominally identical arms.

    They should be mirror images, so a mode on one and not the other is an asymmetry rather
    than a design property -- and that comparison is worth more than either measurement alone.
    Returns matched pairs with their frequency and damping differences, plus the unmatched.
    """
    a = list(summary_a["modes"])
    b = list(summary_b["modes"])
    matched, used = [], set()
    for ma in a:
        best, bi = None, None
        for j, mb in enumerate(b):
            if j in used:
                continue
            d = abs(ma["f_hz"] - mb["f_hz"])
            if d < tol_rel * ma["f_hz"] and (best is None or d < best):
                best, bi = d, j
        if bi is not None:
            used.add(bi)
            mb = b[bi]
            matched.append(dict(f_a=ma["f_hz"], f_b=mb["f_hz"],
                                df_hz=mb["f_hz"] - ma["f_hz"],
                                df_pct=100.0 * (mb["f_hz"] - ma["f_hz"]) / ma["f_hz"],
                                zeta_a=ma["zeta"], zeta_b=mb["zeta"],
                                dzeta=mb["zeta"] - ma["zeta"]))
    return dict(matched=matched,
                only_a=[m for m in a if not any(abs(m["f_hz"] - x["f_a"]) < 1e-9 for x in matched)],
                only_b=[b[j] for j in range(len(b)) if j not in used])


# ---------------------------------------------------------------- capture loading + CLI

# Record types in a campod-accel jsonl, named rather than sniffed at the call site. The writer
# emits the discriminator as "t" (`containers/campod-camera/accel/main.go`); older captures used
# "type", so both are read.
RECORD_TYPE_KEYS = ("t", "type")
RECORD_HEADER = "header"
RECORD_BATCH = "b"
RECORD_CLOCK = "clk"

# Full scale in FULL_RES mode is 13-bit signed, so a sample at +/-4095 LSB is clipped and its true
# value is unknown. Detected in LSB, at parse time, before any scaling: converting to g and
# comparing against 4095 is a test that can never fire, and comparing against a scaled rail
# accumulates avoidable float error.
RAIL_LSB = 4095

FIFO_DEPTH = 32


class AccelCapture:
    """One `campod-accel` jsonl, decoded.

    Attributes, all numpy arrays over samples unless noted:

        time_s      sample time on the pod's CLOCK_BOOTTIME
        x, y, z     acceleration in g
        saturated   per sample: any axis was at the +/-4095 LSB rail, so that sample is clipped
        header      the file's header record, plus rate_fitted_hz / n_samples / truncated_lines
        batch_*     per FIFO drain rather than per sample -- see read_gaps()

    Sample times come from each batch's own `boot_ns`, placed backwards at the fitted rate. That
    re-anchors the time axis to a kernel stamp every 2-3 samples, so a rate error cannot
    accumulate along the record; it only spreads samples slightly within one batch.

    The rate is FITTED per capture. `odr_hz_nominal` is what was asked for over SPI, not what
    happened, and the two parts differ from each other by ~2.8%. The fit is emitted samples per
    second of the pod's own clock: any sample the reader failed to emit lowers it, and there is
    no absolute time reference on the pod, so it is that Pi's clock and carries that oscillator's
    accuracy as its error bar.
    """

    def __init__(self, time_s, x, y, z, saturated, header,
                 batch_time_s, batch_first_index, batch_count, batch_read_gap_s, batch_overrun):
        self.time_s = time_s
        self.x, self.y, self.z = x, y, z
        self.saturated = saturated
        self.header = header
        self.batch_time_s = batch_time_s
        self.batch_first_index = batch_first_index
        self.batch_count = batch_count
        self.batch_read_gap_s = batch_read_gap_s
        self.batch_overrun = batch_overrun

    @property
    def rate_hz(self):
        return self.header["rate_fitted_hz"]

    @property
    def magnitude(self):
        return np.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)

    def window(self, t_start=None, t_end=None, relative=True):
        """A new AccelCapture over a time range, samples and batches both.

        `relative` measures from the first sample rather than from pod boot, because a caller
        knows where in a capture an event was, not what CLOCK_BOOTTIME read at the time.
        """
        origin = self.time_s[0] if relative else 0.0
        lo = origin + t_start if t_start is not None else -np.inf
        hi = origin + t_end if t_end is not None else np.inf
        m = (self.time_s >= lo) & (self.time_s <= hi)
        bm = (self.batch_time_s >= lo) & (self.batch_time_s <= hi)
        if not m.any():
            raise ValueError(f"no samples in {t_start}..{t_end} s "
                             f"(capture spans 0..{self.time_s[-1] - origin:.1f} s)")
        return AccelCapture(self.time_s[m], self.x[m], self.y[m], self.z[m], self.saturated[m],
                            dict(self.header, windowed_from_s=t_start, windowed_to_s=t_end),
                            self.batch_time_s[bm], self.batch_first_index[bm],
                            self.batch_count[bm], self.batch_read_gap_s[bm],
                            self.batch_overrun[bm])

    def read_gaps(self):
        """Stretches of elapsed time the reader emitted no samples for.

        The reader takes whatever the FIFO holds and moves on, so a short read is the ordinary
        case and says nothing; `ovr` is only sampled when the FIFO came back full, so it is
        false by construction on a short read and cannot classify one either. What bounds a hole
        is the gap since this device's previous drain, against the time the FIFO takes to fill
        (FIFO_DEPTH / the fitted rate). Anything beyond that is time the FIFO could not hold.

        Returns dict(fill_s, n_stalls, stall_time_s, stall_at_s, worst_gap_s).
        """
        fill = FIFO_DEPTH / self.rate_hz
        stalled = self.batch_read_gap_s > 2 * fill
        origin = self.time_s[0]
        return dict(
            fill_s=float(fill),
            n_stalls=int(stalled.sum()),
            stall_time_s=float(np.clip(self.batch_read_gap_s[stalled] - fill, 0, None).sum()),
            stall_at_s=[float(v) for v in (self.batch_time_s[stalled] - origin)],
            worst_gap_s=float(self.batch_read_gap_s.max()) if len(self.batch_read_gap_s) else 0.0,
        )

    def fit_rate_over(self, segment_s):
        """Emitted-samples-per-second, fitted independently over each segment of the window.

        Spread across segments bounds how much a single whole-window rate can misplace a
        spectral line. It does not separate the part's oscillator from the Pi's: nothing here is
        referred to an absolute clock.
        """
        t, i = self.batch_time_s, self.batch_first_index
        out = []
        for edge in np.arange(t[0], t[-1] - segment_s, segment_s):
            m = (t >= edge) & (t < edge + segment_s)
            if m.sum() >= 8:
                out.append(float(np.polyfit(t[m], i[m], 1)[0]))
        return np.array(out)


def load_accel_jsonl(path):
    """Decode a `campod-accel` jsonl into an AccelCapture.

    Tolerates a truncated final record: the writer buffers behind a writeback kick rather than
    fsyncing, so a power cut leaves the last record unparseable. Bad lines are counted into the
    header as `truncated_lines`, never silently skipped.
    """
    import json
    hdr, bad = None, 0
    bt, bi, bn, bgap, bovr = [], [], [], [], []
    xs, ys, zs = [], [], []
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            kind = next((r[k] for k in RECORD_TYPE_KEYS if k in r), None)
            if kind == RECORD_HEADER:
                hdr = dict(r)
            elif kind == RECORD_BATCH:
                bt.append(r["boot_ns"]); bi.append(r["i"]); bn.append(r["n"])
                bgap.append(r.get("gap_ns", 0)); bovr.append(bool(r.get("ovr", False)))
                xs.append(r["x"]); ys.append(r["y"]); zs.append(r["z"])
    if hdr is None or not bn:
        raise ValueError(f"{path}: no header or no sample batches")

    cat = lambda L: np.concatenate([np.asarray(v, float) for v in L])
    x_lsb, y_lsb, z_lsb = cat(xs), cat(ys), cat(zs)
    saturated = ((np.abs(x_lsb) >= RAIL_LSB) | (np.abs(y_lsb) >= RAIL_LSB)
                 | (np.abs(z_lsb) >= RAIL_LSB))

    batch_time = np.asarray(bt, float) / 1e9
    batch_index = np.asarray(bi, float)
    batch_count = np.asarray(bn, int)
    rate = float(np.polyfit(batch_time, batch_index, 1)[0])
    time_s = np.concatenate([batch_time[j] - (batch_count[j] - 1 - np.arange(batch_count[j])) / rate
                             for j in range(len(batch_count))])

    g_per_lsb = hdr["scale_mg_per_lsb"] / 1000.0
    hdr = dict(hdr, rate_fitted_hz=rate, truncated_lines=bad, n_samples=int(len(x_lsb)),
               saturated_frac=float(saturated.mean()))
    return AccelCapture(time_s, x_lsb * g_per_lsb, y_lsb * g_per_lsb, z_lsb * g_per_lsb,
                        saturated, hdr, batch_time, batch_index, batch_count,
                        np.asarray(bgap, float) / 1e9, np.asarray(bovr, bool))


def _main(argv):
    import argparse
    import os
    import json as _json
    ap = argparse.ArgumentParser(description="Fit bump-test ringdowns from a campod-accel jsonl.")
    ap.add_argument("files", nargs="+", help="accel-*.jsonl; give two to compare arms")
    ap.add_argument("--axis", default="z", choices=["x", "y", "z", "mag"])
    ap.add_argument("--taps", type=int, default=None, help="expected number of taps")
    ap.add_argument("--fmin", type=float, default=20.0)
    ap.add_argument("--fmax", type=float, default=None)
    ap.add_argument("--post", type=float, default=0.5, help="seconds of decay to fit per tap")
    ap.add_argument("--r2", type=float, default=R2_MIN,
                    help=f"minimum log-envelope fit quality (default {R2_MIN}); a real single-mode "
                         "ringdown reaches 0.99, band skirts land near 0.87")
    ap.add_argument("--tol", type=float, default=0.05,
                    help="fractional frequency window for calling two arms' modes the same "
                         "(default 0.05). Too tight and a real cross-arm difference is reported "
                         "as two unmatched modes instead of one matched pair with a delta.")
    ap.add_argument("--window", nargs=2, type=float, metavar=("START_S", "END_S"),
                    help="seconds from the first sample; restricts everything to this range. A "
                         "capture usually contains more than the strikes -- startup, handling, "
                         "poses -- and all of it is impulses this would otherwise try to fit.")
    ap.add_argument("--json", metavar="PATH", help="write the full result here")
    args = ap.parse_args(argv)

    summaries, out = {}, {}
    for path in args.files:
        cap = load_accel_jsonl(path)
        if args.window:
            cap = cap.window(*args.window)
        t, hdr = cap.time_s, cap.header
        sig = dict(x=cap.x, y=cap.y, z=cap.z, mag=cap.magnitude)[args.axis]
        label = f"{hdr.get('node', '?')}/{hdr.get('label', '?')}"
        if label in summaries or label in out:
            label = f"{label} [{os.path.basename(path)}]"   # two captures can share node+label
        print(f"\n=== {label}  ({path}) ===")
        print(f"  {hdr['n_samples']:,} samples, fitted rate {hdr['rate_fitted_hz']:.2f} Hz "
              f"(nominal {hdr.get('odr_hz_nominal')}), Nyquist {hdr['rate_fitted_hz']/2:.0f} Hz")
        if hdr["truncated_lines"]:
            print(f"  {hdr['truncated_lines']} truncated trailing record(s) -- capture ended unclean")
        if hdr["saturated_frac"] > 1e-4:
            print(f"  WARNING {hdr['saturated_frac']*100:.3f}% of samples clipped at the "
                  f"+/-{hdr.get('range_g')} g rail, which is this part's maximum full scale. "
                  "Clipping flattens a peak, so zeta reads HIGH and the mode reads weak. Strike "
                  "more gently -- there is no range setting that fixes it.")
        gaps = cap.read_gaps()
        if gaps["n_stalls"]:
            print(f"  {gaps['n_stalls']} read stall(s) in this window, "
                  f"{gaps['stall_time_s']*1e3:.0f} ms with no samples, at "
                  f"{', '.join(f'{v:.1f}s' for v in gaps['stall_at_s'][:8])}")
        st = hdr.get("self_test", {})
        if st and not st.get("all_pass", True):
            print("  WARNING self-test did not pass on every axis -- suspect the joint before the structure")
        verdict = looks_like_a_bump_test(sig)
        print(f"  impulse check: peak/median {verdict['peak_over_median']:.1f} "
              f"(needs >= {verdict['threshold']:.0f}), crest {verdict['crest']:.2f}")
        if not verdict["ok"]:
            print("  REFUSING to fit. This record is not impulses-in-silence -- something was")
            print("  running. Fitting it anyway turns the forcing lines into a table of modes")
            print("  with plausible damping attached, which is worse than no answer. Kill the")
            print("  motors, re-tap, and check that the record is quiet between taps.")
            out[label] = dict(header={k: v for k, v in hdr.items() if k != "note"},
                              refused=verdict)
            continue
        res = analyze(t, sig, n_expected=args.taps,
                      bands=candidate_bands(t, sig, fmin=args.fmin, fmax=args.fmax),
                      post_s=args.post)
        print(f"  {res['n_taps']} tap(s) found; candidate bands: "
              + ", ".join(f"{lo:.0f}-{hi:.0f}" for lo, hi in res["bands"]))
        s = summarize(res, r2_min=args.r2)
        summaries[label] = s
        out[label] = dict(header={k: v for k, v in hdr.items() if k != "note"}, result=res, summary=s)
        if not s["modes"]:
            print("  no mode survived the fit. That is a result, not a failure: report it.")
        for m in s["modes"]:
            sd_f = "" if np.isnan(m["f_sd_hz"]) else f" +/- {m['f_sd_hz']:.2f}"
            sd_z = "" if np.isnan(m["zeta_sd"]) else f" +/- {m['zeta_sd']:.4f}"
            print(f"    {m['f_hz']:7.2f}{sd_f} Hz   zeta {m['zeta']:.4f}{sd_z}   Q {m['q']:6.1f}"
                  f"   decay {m['decay_s']*1000:6.1f} ms   {m['n_taps']} tap(s)   r2 {m['r2_median']:.3f}")
        print(f"  ({s['n_kept']}/{s['n_fits']} fits kept at r2 >= {s['r2_min']}; "
              f"{s['n_rejected_low_r2']} rejected)")
        if len(s["modes"]) == 1 or any(m["n_taps"] == 1 for m in s["modes"]):
            print("  NOTE a mode seen on one tap is not a mode. The scatter across taps is the "
                  "only honest error bar there is.")

    if len(summaries) == 2:
        (la, sa), (lb, sb) = summaries.items()
        cmp_ = compare_arms(sa, sb, tol_rel=args.tol)
        out["comparison"] = dict(a=la, b=lb, **cmp_)
        print(f"\n=== {la} vs {lb} ===")
        print("  These should be identical but mirrored, so a difference is an asymmetry -- a")
        print("  loose fastener, a cracked layer, a different cable route -- not a design property.")
        for m in cmp_["matched"]:
            print(f"    {m['f_a']:7.2f} vs {m['f_b']:7.2f} Hz  ({m['df_pct']:+5.2f}%)   "
                  f"zeta {m['zeta_a']:.4f} vs {m['zeta_b']:.4f}  ({m['dzeta']:+.4f})")
        for m in cmp_["only_a"]:
            print(f"    {m['f_hz']:7.2f} Hz  on {la} ONLY  <- asymmetry")
        for m in cmp_["only_b"]:
            print(f"    {m['f_hz']:7.2f} Hz  on {lb} ONLY  <- asymmetry")

    if args.json:
        with open(args.json, "w") as fh:
            _json.dump(out, fh, indent=2, default=float)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
