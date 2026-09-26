#!/usr/bin/env python3
"""Synthetic tests for ringdown.py -- no hardware, known answers.

Each test pins a way the fit can be wrong rather than only the happy path. The one that
matters most is `test_zeta_is_not_biased_by_the_noise_floor`: fitting a log-envelope into the
noise bends it flat, which biases damping LOW -- towards "lightly damped", the direction that
would wrongly raise an alarm about a mode near a rotor harmonic.

Run: python3 analysis/test_ringdown.py     (exit 0 = pass; no pytest needed)
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import ringdown


def ringdown_signal(f_n, zeta, fs=3200.0, dur=1.0, t0=0.05, amp=1.0, noise=0.0, seed=0):
    """One tap: a damped sinusoid starting at t0, with a quiet lead-in."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, dur, 1.0 / fs)
    wn = 2 * np.pi * f_n
    wd = wn * np.sqrt(1 - zeta ** 2)
    a = np.zeros_like(t)
    m = t >= t0
    tt = t[m] - t0
    a[m] = amp * np.exp(-zeta * wn * tt) * np.cos(wd * tt)
    if noise:
        a = a + rng.normal(0, noise, len(t))
    return t, a


def multi_tap(f_n, zeta, n=5, fs=3200.0, gap=0.4, **kw):
    t_all, a_all = [], []
    for k in range(n):
        t, a = ringdown_signal(f_n, zeta, fs=fs, dur=gap, seed=k, **kw)
        t_all.append(t + k * gap)
        a_all.append(a)
    return np.concatenate(t_all), np.concatenate(a_all)


# ------------------------------------------------------------------ tests

def test_recovers_a_known_frequency_and_damping():
    t, a = ringdown_signal(260.0, 0.01)
    r = ringdown.fit_ringdown(t, a, 230, 290)
    assert r is not None, "a clean ringdown must fit"
    assert abs(r["f_hz"] - 260.0) < 2.0, r["f_hz"]
    assert abs(r["zeta"] - 0.01) < 0.002, r["zeta"]
    assert r["r2"] > 0.99, r["r2"]


def test_recovers_heavy_damping_too():
    # the whole point is telling a lightly-damped mode from a heavily-damped one at the SAME
    # frequency, so the fit must not be tuned to the light case
    t, a = ringdown_signal(260.0, 0.08, dur=0.4)
    r = ringdown.fit_ringdown(t, a, 200, 320)
    assert r is not None
    assert abs(r["zeta"] - 0.08) < 0.012, r["zeta"]


def test_zeta_is_not_biased_by_the_noise_floor():
    """Fitting into the floor flattens the log-envelope and under-reports damping."""
    t, a = ringdown_signal(260.0, 0.02, dur=1.5, noise=0.01)
    good = ringdown.fit_ringdown(t, a, 230, 290, decay_db=18.0)
    into_floor = ringdown.fit_ringdown(t, a, 230, 290, decay_db=80.0)
    assert good is not None and into_floor is not None
    assert abs(good["zeta"] - 0.02) < 0.004, good["zeta"]
    # the over-long fit must be the WORSE one, and biased low
    assert into_floor["zeta"] < good["zeta"], (into_floor["zeta"], good["zeta"])
    assert into_floor["r2"] < good["r2"]


def test_a_band_with_no_mode_does_not_invent_one():
    t, a = ringdown_signal(260.0, 0.01)
    r = ringdown.fit_ringdown(t, a, 600, 700)
    assert r is None or r["r2"] < 0.85, "an empty band must not yield a confident fit"


def test_a_pure_tone_is_rejected_not_reported_as_undamped():
    """A steady tone has no decay. It must not come back as zeta ~ 0 with a good r2."""
    t = np.arange(0, 1.0, 1 / 3200.0)
    a = np.cos(2 * np.pi * 260 * t)
    r = ringdown.fit_ringdown(t, a, 230, 290)
    assert r is None or r["r2"] < 0.85, r


def test_finds_every_tap():
    t, a = multi_tap(260.0, 0.02, n=5)
    taps = ringdown.find_taps(t, a)
    assert len(taps) == 5, len(taps)


def test_each_tap_is_fitted_separately_and_the_scatter_is_the_error_bar():
    t, a = multi_tap(260.0, 0.02, n=6, noise=0.003)
    res = ringdown.analyze(t, a, bands=[(230, 290)], post_s=0.35)
    assert res["n_taps"] == 6
    s = ringdown.summarize(res)
    assert len(s["modes"]) == 1, s["modes"]
    m = s["modes"][0]
    assert m["n_taps"] >= 5, m
    assert abs(m["f_hz"] - 260.0) < 2.0
    assert abs(m["zeta"] - 0.02) < 0.005
    assert m["f_sd_hz"] >= 0.0 and not np.isnan(m["zeta_sd"]), "scatter across taps is the error bar"


def test_two_modes_are_separated():
    t1, a1 = ringdown_signal(150.0, 0.01, dur=1.0)
    _, a2 = ringdown_signal(390.0, 0.03, dur=1.0)
    t, a = t1, a1 + 0.7 * a2
    bands = ringdown.candidate_bands(t, a, fmin=50, n=4)
    got = []
    for lo, hi in bands:
        r = ringdown.fit_ringdown(t, a, lo, hi)
        if r and r["r2"] > 0.9:
            got.append(r["f_hz"])
    assert any(abs(g - 150) < 4 for g in got), got
    assert any(abs(g - 390) < 6 for g in got), got


def test_candidate_bands_scale_with_frequency():
    t, a = ringdown_signal(400.0, 0.01)
    bands = ringdown.candidate_bands(t, a, fmin=50, n=2)
    assert bands, "a clear peak must produce a band"
    lo, hi = bands[0]
    assert lo < 400 < hi, bands[0]
    assert (hi - lo) / 400 < 0.2


def test_candidate_bands_do_not_overlap():
    """Overlapping bands re-fit the skirts of one broad damped peak as extra phantom modes."""
    t, a = ringdown_signal(262.0, 0.015, dur=1.0)
    bands = ringdown.candidate_bands(t, a, fmin=50, n=6)
    for (lo1, hi1), (lo2, hi2) in zip(bands, bands[1:]):
        assert lo2 >= hi1, f"bands overlap: {(lo1, hi1)} and {(lo2, hi2)}"


def test_one_broad_mode_is_reported_once():
    t, a = multi_tap(262.0, 0.015, n=6, gap=0.5, noise=0.0015)
    res = ringdown.analyze(t, a, post_s=0.4)
    s = ringdown.summarize(res, r2_min=0.95)
    assert len(s["modes"]) == 1, [round(m["f_hz"], 1) for m in s["modes"]]
    assert abs(s["modes"][0]["f_hz"] - 262.0) < 3.0


def test_compare_arms_flags_an_asymmetry():
    """A mode on one arm and not the other is the finding; the pairing must surface it."""
    t, a = multi_tap(260.0, 0.02, n=5)
    se = ringdown.summarize(ringdown.analyze(t, a, bands=[(230, 290)], post_s=0.35))
    t2, a2 = multi_tap(268.0, 0.02, n=5)          # 3% higher: a loose joint, say
    sw = ringdown.summarize(ringdown.analyze(t2, a2, bands=[(230, 300)], post_s=0.35))
    cmp_ = ringdown.compare_arms(se, sw, tol_rel=0.05)
    assert len(cmp_["matched"]) == 1, cmp_
    assert abs(cmp_["matched"][0]["df_pct"] - 3.0) < 1.5, cmp_["matched"][0]


def test_compare_arms_reports_a_mode_present_on_only_one():
    t, a = multi_tap(260.0, 0.02, n=5)
    se = ringdown.summarize(ringdown.analyze(t, a, bands=[(230, 290)], post_s=0.35))
    empty = dict(modes=[])
    cmp_ = ringdown.compare_arms(se, empty)
    assert not cmp_["matched"] and len(cmp_["only_a"]) == 1, cmp_


def test_a_steady_source_is_refused_not_fitted():
    """The failure this guard exists for: fitting a running motor yields a table of 'modes'
    that are really its shaft harmonics, with plausible zeta and Q attached."""
    fs = 3200.0
    t = np.arange(0, 20.0, 1 / fs)
    rng = np.random.default_rng(3)
    a = sum(np.cos(2 * np.pi * 36.8 * k * t + rng.uniform(0, 7)) / k for k in (1, 3, 4, 5, 12))
    a = a * (1 + 0.3 * np.sin(2 * np.pi * 0.7 * t)) + rng.normal(0, 0.02, len(t))
    v = ringdown.looks_like_a_bump_test(a)
    assert not v["ok"], v
    t2, a2 = multi_tap(260.0, 0.02, n=5, noise=0.002)
    assert ringdown.looks_like_a_bump_test(a2)["ok"]


def test_units_cancel():
    """Frequency and zeta are scale-free, so an uncalibrated sensor is fine for a bump test."""
    t, a = ringdown_signal(260.0, 0.02)
    r1 = ringdown.fit_ringdown(t, a, 230, 290)
    r2 = ringdown.fit_ringdown(t, a * 1.104, 230, 290)     # the measured 10.4% part-to-part error
    assert abs(r1["zeta"] - r2["zeta"]) < 1e-9
    assert abs(r1["f_hz"] - r2["f_hz"]) < 1e-9


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as e:
            bad += 1
            print(f"  FAIL {name}: {e}")
        except Exception as e:
            bad += 1
            print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"RESULT: {'PASS' if not bad else 'FAIL'} ({len(tests)-bad}/{len(tests)})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
