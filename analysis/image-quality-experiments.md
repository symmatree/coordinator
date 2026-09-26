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
| IQ5 | **campod focus is fixed at 1.25 m** (Wide module hyperfocal), costing ~1.16 px of defocus blur at 3 m subject distance | campod module spec + sidecars |
| IQ6 | **`blur = rate x exposure` is false above `f*t_exp ~= 0.45`** -- about 90 Hz at 5 ms. It overstates by 1.48x at 120 Hz, 2.22x at 200 Hz, 8.89x at 800 Hz. Every rotor-band blur figure computed that way is inflated, worse the higher the band | simulated sinusoid against the closed form |

---

## Banding

**Confirmed:**

- IQ1: the two cameras sit in *different* vibration fields, so "which order writes the bands" has
  a different answer per camera and must be asked per camera.
- The rolling-shutter mapping itself: a band pitch of *R* rows is `f = rows/readout / R`. At the
  campod's 68.1 ms readout, motor2's rev writes **5.9 bands** and its blade-pass **17.8**; at the
  OAK-D's 33 ms the same two are **2.9** and **8.6**.

**Current theories:**

- **A hub-mounted camera should band at rev, an arm-mounted one at blade-pass**, following IQ1.
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

## Sharpness and focus

**Confirmed:**

- IQ5: campod focus is fixed at 1.25 m, so anything at flight distance is beyond hyperfocal and
  carries a defocus term that no exposure change touches.

**Current theories:**

- (none stated here yet -- the OAK-D focus theories live in `vio-quality-experiments.md` T10 and
  have not been re-derived for the campod, which has a different module and a fixed lens.)

**Next steps:**

- [ ] **Check the 260926 bench stills for focus.** 507 of them, vehicle static and deliberately left
  tilted so a range of distances is in frame -- which makes them a focus-calibration set with no
  flight confound.

---

## What has not been migrated, and why

The imagery theories in [vio-quality-experiments.md](vio-quality-experiments.md) (**T9** motion
blur, **T10** autofocus, **T11** the rev-line banding, with evidence **E18 / E24 / E33 / E34**) are
about image quality and arguably belong here. They have not been moved, because moving them means
restating claims whose evidence this session has not re-examined, and a restatement is where a
hedge quietly becomes a fact. They stay where they were argued until each is either re-checked or
retired.

The one exception is the correction they need now: **T11's comparison against a three-blade
blade-pass at ~290-360 Hz was right** (that *is* the correct range), but the 260923 spectrogram it
would have been checked against was labelling order 2 as blade-pass (IQ3).
