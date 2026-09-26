# Experiments: image quality

*The deliverable is stills sharp enough to feed ODM. This ledger tracks why they are not, at
explicit confidence, in the form of `fables/Datasets/experiments-house-model.md`: findings graded,
**dead theories kept visible with what killed them**, and the discriminating probe named for each
live one. A topic closes when the confirmed set explains the observed behaviour.*

**This is not about VIO.** Sharpness, banding, focus and blur are the subject. Where a finding also
bears on pose estimation that is incidental, and the VIO ledger
([vio-quality-experiments.md](vio-quality-experiments.md)) keeps its own theories.

---

## Two cameras, and results do not transfer between them

This is the first thing to fix about how these questions have been asked. There are two still
cameras and they differ in every respect that matters to a banding or blur argument:

| | OAK-D RGB | campod |
|---|---|---|
| sensor | IMX378, 12 MP | IMX708, 4608x2592 |
| readout | 33 ms, 3040 rows | **68.1 ms**, 2592 rows |
| mount | **hub, bobbin-isolated** | **bolted to the rear-left arm** |
| focus | VCM autofocus, or a pinned lens position | fixed, hyperfocal at 1.25 m |
| vibration field it sits in | **rev-dominated** (E40) | **blade-pass-dominated** (E40) |

A result from one is **evidence about that camera**, not about the airframe's imagery in general.
Whether a finding transfers is itself an interesting question and usually an unasked one: the two
disagree about which rotor order is loudest where they sit, so a banding mechanism that explains one
predicts something *different* for the other rather than the same thing.

---

## Evidence

| ID | Evidence | Source / provenance |
|----|----------|---------------------|
| IQ1 | **The vibration field is location-dependent, and blade-pass transmits only locally.** blade-pass/rev PSD ratio is **1.68** for the sensors' own motor, **0.25-0.83** for the other three motors at that same sensor, and **0.02-0.71** at the FC (hub, bobbin-mounted). Higher frequency attenuates faster through the structure, and the bobbins roll off what is left | `analysis/vibration-spectrogram.ipynb` on 260923 |
| IQ2 | **On the arm, the largest line measured anywhere on the airframe is that arm's own motor's blade-pass** -- motor2 at 261.80 Hz, PSD 7.187, against 4.286 for its own rev line | same |
| IQ3 | **The props are three-blade**, so blade-pass is order 3: 261.80 / 281.90 / 348.14 / 358.76 Hz, confirmed to ~1 Hz on four independent instruments. The analysis had been computing order 2 and calling it blade-pass | same; `docs/rekon10/props.md` |
| IQ4 | **A structural mode at 152.9 Hz on the rear-left arm**, two sensors 127 mm apart agreeing to 160 ppm. Nothing drives it at current hover RPM -- nearest line 21.4 Hz above. Base was hand-held, so it is the restrained assembly's mode | `analysis/bump-test-ringdown.ipynb` on 260926; graded in `docs/rekon10/vibration-testing.md` sec. 7 |
| IQ7 | **[carried: E18, 260712]** **In-flight colour stills unusable; vibration the top correlate.** var(Laplacian) median collapses **~43x** (4827 at rest -> 113 in flight); **0 of 29** in-flight frames reach at-rest sharpness; exposure 1.2 -> 6.1 ms. Correlates: **VIBE -0.81**, exposure -0.66, EKF-velocity -0.53, gyro -0.42. Streak/arc morphology is *directional*, not uniform focus-softness | `vio-quality-experiments.md` E18. **OAK-D only. Not re-examined this session** |
| IQ8 | **[carried: E24, 260730]** **Eye-visible still banding at ~motor 1st-order rev, n=1.** Operator annotated **4 horizontal blur bands** on a hover still (~2.5 m, motors ~6400 rpm) -> ~760-row pitch -> **~120 Hz** via the IMX378 33 ms readout, matching the fast-pair rev line and **not** the three-blade blade-pass at ~290-360 Hz | `vio-quality-experiments.md` E24. **OAK-D only; operator visual count; n=1** |
| IQ9 | **[carried: E33, 260814]** **The still exposure cap is live and binding.** All 45 stills came in at exactly **4996 us** with ISO carrying the range, 162 to a pinned 1600 -- the first hardware data on the exposure lever. Banding **survives** the cap (IQ10): at ~100 Hz a half-cycle is ~5 ms, so a 5 ms exposure still integrates close to a full peak-to-peak excursion | `vio-quality-experiments.md` E33. **OAK-D only** |
| IQ10 | **[carried: E34]** **Scene-cancelled band-pitch measurement.** Two stills of the same scene registered by phase correlation; the **ratio** of per-row gradient energy divides the scene out; a sinusoid fit to that ratio gives the pitch. Within 260814 it is tight (**895 rows**, IQR 880-901 -> ~103 Hz); on 260730 it is internally unstable. Implemented as `still_banding.py` | `vio-quality-experiments.md` E34. **Method, not yet reliable flight-to-flight** |
| IQ5 | **campod focus is fixed at 1.25 m** (Wide module hyperfocal), costing ~1.16 px of defocus blur at 3 m subject distance | campod module spec + sidecars |
| IQ6 | **`blur = rate x exposure` is false above `f*t_exp ~= 0.45`** -- about 90 Hz at 5 ms. It overstates by 1.48x at 120 Hz, 2.22x at 200 Hz, 8.89x at 800 Hz. Every rotor-band blur figure computed that way is inflated, worse the higher the band | simulated sinusoid against the closed form |

---

## Banding

**Confirmed:**

- IQ1: the two cameras sit in *different* vibration fields, so "which order writes the bands" has
  a different answer per camera and must be asked per camera.
- IQ10: the banding is not a focus state -- it alternates within one frame and its phase moves
  between frames 10 s apart.
- IQ9: it **survives** the 5 ms exposure cap, which at ~100 Hz still integrates close to a full
  peak-to-peak excursion.
- The rolling-shutter mapping itself: a band pitch of *R* rows is `f = rows/readout / R`. At the
  campod's 68.1 ms readout, motor2's rev writes **5.9 bands** and its blade-pass **17.8**; at the
  OAK-D's 33 ms the same two are **2.9** and **8.6**.

**Current theories:**

- **[carried: T11, *suggestive, not established*]** **The eye-visible bands on the OAK-D are the
  motor rev line.** For: IQ8, one hover still, an operator visual count of 4 bands. Against: an
  automated var-Laplacian detector **over-counts** (6-10 bands, and fires on a ground frame), so the
  count is not independently confirmed; metadata-to-pixel timing is ~5 s off, so the per-frame RPM
  tie is coarse; n=1 frame, 1 flight. IQ10's scene-cancelled pitch of 895 rows gives ~103 Hz, also a
  rev-order figure, but is not yet reliable flight-to-flight.
- **A hub-mounted camera should band at rev, an arm-mounted one at blade-pass**, following IQ1 --
  which is the first thing that makes T11's rev reading *expected* rather than merely observed, and
  removes the objection that the loudest line (blade-pass, IQ2) should have written the bands.
  This is the prediction the arm mount was built to test and it has not been run. The discriminating
  probe is `still_banding.py` on the 300 campod stills from 260923 at the 68.1 ms readout: rev at
  5.9 bands against blade-pass at 17.8 is a factor of three, where the OAK-D's 2.9-vs-8.6 was
  crowded enough that an eyeball count and an automated detector disagreed.
  **A null -- campod stills banding at rev anyway -- would be the stronger result**, because it
  would say banding is not set by the local vibration amplitude.

**Weakened:**

- ~~The visible banding is set by whichever rotor line is loudest.~~ The loudest line on the arm is
  blade-pass (IQ2), and the loudest at the hub is rev (IQ1) -- so "loudest" is not one thing, and any
  claim of the form "the bands are at X because X is the biggest peak" has to say *where* the peak
  was measured. Not disproven, because per-camera it may still hold; retired as a global statement.

**Disproven:**

- ~~Bands at 174-239 Hz would be blade-pass.~~ Those are order 2 on a three-blade aircraft (IQ3).
  Any band-count prediction made against them was against the wrong frequency.
- ~~`blur = rate x exposure` in the rotor band.~~ IQ6. It is only valid below ~90 Hz at 5 ms, which
  excludes every rotor line on this airframe.

**Next steps:**

- [ ] **`still_banding.py` on the 300 campod stills from 260923** at 68.1 ms. It will **not**
  separate the 152.9 Hz mode (10.4 bands) from motor2's order 2 (11.9) -- 14% in pitch against the
  method's 2.4% within-flight spread, so possible but not assured. It will separate either from rev.
- [ ] **Score campod still sharpness** against the per-still sidecar exposure and the interpolated
  airframe motion, as was done for the OAK-D -- but scored as its own camera, not by carrying the
  OAK-D's conclusion across.

## Motion blur

*Carried from `vio-quality-experiments.md` T9 at its stated confidence -- **leading, confounded with
vibration** -- with the VIO framing dropped. There, sharpness was also treated as a proxy for
feature-trackability; here it is the deliverable itself, which is a narrower and easier claim.*

**Confirmed:**

- IQ7: in-flight sharpness collapses ~43x and **no** in-flight frame reaches at-rest sharpness. The
  morphology is directional streaks and arcs, not uniform softness, so something moves during the
  exposure.
- IQ9: the 5 ms exposure cap is live and binding, with ISO carrying the range.

**Current theories:**

- **Blur-pixels = airframe motion x exposure / GSD is the governing driver.** Carried as *leading*.
  **Not isolated:** VIBE is the strongest correlate (-0.81, against exposure -0.66) and everything
  covaries at takeoff, because there are no motors-on-ground frames in that set. A short exposure
  freezes motion blur *and* vibration jello, so the exposure lever cannot discriminate them.
- **A loaded hover is the discriminator** -- it holds airframe translation near zero while the rotors
  still run, separating the two. Not flown.

**Disproven:**

- ~~`blur = rate x exposure` in the rotor band.~~ IQ6. Valid only below `f*t_exp ~= 0.45`, about
  90 Hz at 5 ms, which excludes every rotor line on this airframe. It overstates by 1.48x at 120 Hz
  and 8.89x at 800 Hz, so every rotor-band blur figure computed that way was inflated.

**Next steps:**

- [ ] **A loaded hover.** The one probe that separates motion blur from vibration jello.
- [ ] **Blur budget from physics** rather than the 5 ms guess: the exposure that holds blur under N px
  at flight speed and GSD -- and computed with the correct closed form, not the linear one.
- [ ] **Re-run the driver comparison for the campod**, which has a different sensor, a fixed lens and
  a far longer readout. Carrying the OAK-D's answer across is the thing this ledger exists to stop.

## Sharpness and focus

**Confirmed:**

- IQ5: campod focus is fixed at 1.25 m, so anything at flight distance is beyond hyperfocal and
  carries a defocus term that no exposure change touches.

**Current theories:**

- **[carried: T10, *proposed*]** **Autofocus is the wrong mode for the OAK-D; a calibrated fixed
  focus is better.** The VCM can hunt or refocus silently, and autofocus locks the
  highest-frequency signal in frame -- canopy twigs -- leaving the useful subject soft. A fixed
  position is repeatable across flights. **Caveat carried intact: a *wrong* fixed value is worse
  than autofocus,** so the knob ships defaulting to `auto` and the value is uncalibrated. Nothing
  here re-derives this for the campod, which has a different module and an already-fixed lens.
- **Banding is not a focus state.** It alternates within a single frame and its phase moves between
  two frames 10 s apart (IQ10), so whatever it is, it is not the lens being wrong.

**Next steps:**

- [ ] **Check the 260926 bench stills for focus.** 507 of them, vehicle static and deliberately left
  tilted so a range of distances is in frame -- which makes them a focus-calibration set with no
  flight confound.

---

## What stays in the VIO ledger, and why

The imagery theories and evidence are carried above, marked `[carried: ...]` with their original ID
and their original status, so the provenance stays traceable and a carried claim is not mistaken for
one this session re-examined. **None of IQ7-IQ10 was re-derived here.**

What is deliberately left behind is everything VIO-specific -- estimator behaviour, IMU authority,
solver budgets, stereo baseline, feature supply for tracking (T1-T8, T12 and their evidence). Those
are a different question with different failure modes, and dragging them along is how an image-quality
question turns into a pose-estimation argument.

Two framing changes were made in carrying T9 and T11 across, and both narrow the claim rather than
widen it:

- T9's metric was sharpness *as a proxy for mono feature-trackability*. Here sharpness is the
  deliverable itself, so the proxy step is dropped -- a narrower claim needing less to support it.
- T11 is now stated **per camera**. Its evidence is OAK-D evidence, and IQ1 says the campod sits in a
  different field, so the theory predicts something *different* there rather than the same thing.
