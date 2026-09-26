# Vibration and structural testing

Sibling to [flight-platform.md](flight-platform.md), which describes what the airframe **is**.
This one is about measuring what it **does** -- its dynamic behaviour -- and, first, about
establishing that the instrument measuring it can be trusted.

Everything below dated 2026-09-15 was measured on `campod-se` with two ADXL345s on a
breadboard balanced on the face of a small 5-blade USB fan. That is a deliberately unserious
test article: the point was not the fan, it was to find out whether the measurement chain
reports real frequencies. Raw captures are in `~/datasets/campod-accel/` with a README.

## 1. Validating the chain before believing it

An accelerometer reader can produce a spectrum full of confident peaks that are artifacts of
its own acquisition. Ours did, for a while: every capture carried a line at exactly the poll
rate. So the first job is to separate what the world is doing from what the instrument is
doing, and the honest way is with controls that an artifact cannot pass.

**Three orthogonal controls.** Each one is something a real mechanical signal does and an
acquisition artifact cannot.

| Control | A real line | An artifact |
|---|---|---|
| **Change the sample rate** (ODR 3200 / 1600 / 800) | stays at the same absolute Hz | moves with the rate, or with a fixed fraction of it |
| **Compare two sensors** whose clocks differ | agrees on absolute Hz once each is scaled by its own measured rate | disagrees, or agrees only in bin-fraction terms |
| **Change the source speed** (fan high / low) | moves in exact proportion | does not move |

Measured results, fan high, camera sensor, three sample rates:

| ODR 3200 | ODR 1600 | ODR 800 |
|---|---|---|
| 109.38 Hz | 108.59 | 108.59 |
| 181.25 Hz | 181.25 | 181.25 |
| 434.38 Hz | 435.16 | *(above Nyquist)* |
| 579.69 Hz | 580.47 | *(above Nyquist)* |

181.25 Hz is identical to 0.01 Hz across a 4x change in sample rate.

Two sensors, each scaled by its own measured rate (3242.6 and 3155.7 Hz -- the parts' clocks
differ by **2.75%**), eight peaks match:

```
camera      arm       delta
 36.81     36.60     -0.22 Hz
 73.62     73.58     -0.05
110.44    110.17     -0.26
146.85    147.15     +0.30
183.66    183.75     +0.08
440.95    441.07     +0.12
514.57    514.65     +0.07
588.20    588.22     +0.03
```

And across the two fan speeds, every line scaled by the same factor:

| | high | low | ratio |
|---|---|---|---|
| fundamental | 36.81 Hz | 31.33 Hz | 0.8510 |
| 12th harmonic | 441.0 | 375.1 | 0.8506 |
| 16th harmonic | 588.2 | 500.5 | 0.8509 |

**Zero peaks stayed put.** Both sensors agree on the ratio to 0.4%.

No acquisition artifact survives all three. That is the basis on which any later claim about
the airframe rests, and it should be re-established -- at least the rate control -- after any
change to the reader.

## 2. Use each sensor's own measured rate, never the nominal one

The ADXL345 datasheet specifies **no tolerance at all** on the part's internal clock, and the
two parts on this pod differ by **2.75%**. At 588 Hz that is 16 Hz of error -- forty times the
0.39 Hz bin width. So:

- Fit the rate as **samples delivered / wall-clock span**, per sensor, per capture.
- That is only valid when the capture is **gap-free**. If the FIFO overflowed, samples are
  missing at unknown positions and the ratio is a lower bound, not a rate.
- A batch with `n < 32` and `ovr` false lost nothing, because the FIFO never filled. That is
  the check to use. The cumulative sample index **cannot** detect loss -- it counts what was
  read, so it advances smoothly no matter how much the part discarded.

That last point cost a day. An earlier analysis argued the stream was "gap-free, therefore
nothing was lost, therefore the part is clocking at half its configured rate." The gap-free
observation was true by construction and the conclusion was wrong: the reader was stalled for
about half the run and the part was fine.

## 3. What a running motor can and cannot tell you

Everything the fan produced was **forcing** -- harmonics of the shaft, nothing else. Across
two speeds, every single peak moved in proportion and none stayed put, which means no
structural resonance was excited enough to see.

That is the general limitation: **a spectrum of a running motor shows you the forcing
frequencies, and reveals a resonance only if a harmonic happens to land on one.** It cannot
map the structure. Any peak you find is confounded with the drive.

Worth noting what the harmonics looked like, because it is a reminder not to assume blade-pass
dominates. The fan has 5 blades, so blade-pass is the 5th harmonic -- present (183.7 Hz high,
156.6 Hz low) but **not** the strongest line. The dominant peaks were the 8th, 12th and 16th
harmonics, which are not blade multiples and are more likely motor commutation. The blades are
heavily scimitared, which plausibly suppresses blade-pass relative to motor noise. So
"strongest peak" and "blade-pass" are not the same thing, and an unexplained harmonic index is
not evidence of a resonance.

## 4. Bump testing -- first result, 260926

Done once, on the SW arm, and it produced one number worth carrying: **a mode at 152.9 Hz.**

**Where the pod is: `campod-sw` is on the REAR-LEFT arm**, which carries **motor2**
(`SERVO4_FUNCTION=34`). Operator-confirmed 2026-09-26, and recorded here because nothing else in
the repo stated it and every per-motor amplitude below is read against it -- the strongest lines
turn out to be the sensors' own motor, which is only interpretable once the mounting is known.
`campod-se` is the mirrored rear-right arm.

| | camera-colocated sensor | arm-end sensor |
|---|---|---|
| frequency | 152.92 +/- 0.04 Hz | 152.94 +/- 0.02 Hz |
| strikes fitted | 3 of 16 | 5 of 16 |
| r2 | 0.983 | 0.987 |
| zeta / Q | 0.0357 / 14.0 | 0.0124 / 40.2 |

Two sensors 127 mm apart on one arm, fitted independently, 160 ppm apart. Notebook:
`analysis/bump-test-ringdown.ipynb`, run against `260926-sixpose-and-bump`. The arm sensor also
holds modes at 37.2 and 51.0 Hz on 3 and 5 strikes which the camera sensor does not see at all;
the vehicle was hand-held and moving under the strikes, so those are candidates for whole-vehicle
motion in a grip rather than anything structural.

**The damping is not usable, which was supposed to be the point of the exercise.** Q 14 against
Q 40 at the same frequency is not a geometry effect: mode shape sets how much each point moves --
a free end swings and a root does not -- but the decay RATE belongs to the mode and is the same
wherever it is observed. So a 2.9x spread means either the band holds more than one mode that the
two positions weight differently, or a sensor mount has dynamics of its own. Neither is tested.

**The fitter refuses a record that is not impulses-in-silence, and that guard is not
hypothetical.** Run against the fan captures in section 1 -- a steady source with no taps at all
-- it returned five confident "modes" at 37.3 / 110.1 / 147.1 / 183.3 / 441.3 Hz, which are that
fan's own shaft harmonics from the table above, each with a plausible damping ratio attached. Kill
the motors before striking anything, and check the record is quiet between strikes.

Corrections to the method notes above, from doing it:

- **Tape is questionable after all.** The note says tape, wax or glue are all fine. The taped
  sensor is the one reporting 2.9x more damping, which is what a lossy mounting looks like. Not
  established -- it is also what a second mode in the band looks like -- but it is the first thing
  to remove next time by mounting both sensors the same way.
- **The base has to be genuinely clamped.** Held down by hand is not a boundary condition: the
  vehicle moved under every strike, so 152.9 Hz is a mode of the hand-restrained assembly and the
  arm-root condition on a flying vehicle is different.
- **+/-16 g is not enough range for a metal hook.** Peaks reached 26.4 g (camera) and 27.7 g
  (arm), with 71 and 143 railed samples inside the strike window. +/-16 g is the ADXL345's
  maximum full scale, so the fix is a softer impactor, not a range setting.
- **Only one arm was done.** The SE-vs-SW comparison the notes ask for -- each arm as the other's
  control -- is still open, and it is the check that says whether 152.9 Hz is a design property or
  an asymmetry in one arm.

## 5. Where 152.9 Hz sits relative to what actually drives the airframe

A mode matters only if something excites it. The forcing is the rotors: each motor puts a line at
its rev frequency and at multiples of it. **The props are three-blade** (HQ MacroQuad 10x4.8x3,
[props.md](props.md)), so **blade-pass is the 3rd harmonic**, and the 2nd harmonic is a rotational
harmonic with no blade-rate meaning. Getting that wrong moves a named line by a third of its
frequency; it was wrong here until 2026-09-26.

Measured in the 260923 hover window, output-to-motor map from `SERVOn_FUNCTION`. `|H|` is
single-degree-of-freedom amplification at the mode, `1/sqrt((1-r^2)^2 + (2*zeta*r)^2)`,
`r = f/152.93`. PSD is on the arm-end sensor, which with both sensors on the **rear-left** arm
makes motor2 the sensor's own motor:

| line | motor position | Hz | arm PSD | r | \|H\| Q=40 | \|H\| Q=14 |
|---|---|---|---|---|---|---|
| motor2 rev | rear-left (own arm) | 87.27 | 4.286 | 0.571 | 1.48 | 1.48 |
| motor4 rev | rear-right | 93.97 | 4.212 | 0.614 | 1.61 | 1.61 |
| motor1 rev | front-right | 116.05 | 1.169 | 0.759 | 2.35 | 2.34 |
| motor3 rev | front-left | 119.59 | 2.922 | 0.782 | 2.57 | 2.54 |
| motor2 2x | rear-left (own arm) | 174.53 | 0.738 | 1.141 | 3.30 | 3.20 |
| motor4 2x | rear-right | 187.93 | 1.058 | 1.229 | 1.96 | 1.93 |
| **motor2 blade-pass 3x** | rear-left (own arm) | **261.80** | **7.187** | 1.712 | 0.52 | 0.52 |
| motor4 blade-pass 3x | rear-right | 281.90 | 1.067 | 1.843 | 0.42 | 0.42 |
| motor1 2x | front-right | 232.10 | 0.532 | 1.518 | 0.77 | 0.77 |
| motor3 2x | front-left | 239.18 | 1.635 | 1.564 | 0.69 | 0.69 |
| motor1 blade-pass 3x | front-right | 348.14 | 0.976 | 2.276 | 0.24 | 0.24 |
| motor3 blade-pass 3x | front-left | 358.76 | 1.601 | 2.346 | 0.22 | 0.22 |

**The largest line measured anywhere on this airframe is motor2's blade-pass at 261.80 Hz, PSD
7.187** -- larger than its own rev line, larger than everything else, and on the arm the sensors
are bolted to. It was invisible until 2026-09-26 because the analysis computed order 2 and called
it blade-pass, never computing order 3 at all.

**The mode is not what makes any of it loud.** At 261.80 Hz the mode's amplification is 0.52 -- it
attenuates. The strongest line and the measured mode are not interacting. Nothing sits on 152.9 Hz:
the nearest line is motor2's 2nd harmonic at 174.53 Hz, 21.4 Hz above, picking up about 3.3x, and
that line's PSD is 0.738, a tenth of the blade-pass line 87 Hz further up.

**The unusable damping does not block this question.** Off resonance the response is set by the
stiffness term `(1-r^2)`, which is why the Q=40 and Q=14 columns are effectively identical.
Damping decides the answer only when a line is ON the mode -- so the missing number is exactly
what is needed to say how bad landing on the mode would be, and is not needed to say nothing is on
it today.

**Prop-change arithmetic**, for the two-blade set that is on hand and unflown:

- **Blade count moves blade-pass without moving rev.** Going from three blades to two takes
  blade-pass from order 3 to order 2: on the rear-left motor at its current speed that is 261.80 Hz
  down to 174.53 Hz. Fewer blades need more RPM for the same thrust, which pushes it back up, so
  the landing point is somewhere above 174.5 Hz. Either way it moves the **dominant** line from 71%
  above the mode to roughly 14-30% above it.
- **Diameter moves every rev line together.** The front pair at 116-120 Hz reaches 152.9 Hz on a
  +28 to +33% RPM rise; the rear pair needs +63 to +75%.
- So the two-blade test walks the largest line toward the mode without reaching it, and a diameter
  reduction walks a rev line onto it from below. Neither is a recommendation; both are the
  arithmetic that was missing.

**All of it bounded by the base not having been clamped.** These are distances to a mode frequency
measured on a hand-held vehicle.

## 6. The 1x rev amplitudes, measured

Rev-line PSD on the arm-end sensor, 260923 hover. **Both sensors are on the rear-left arm**, which
is motor2's, so motor2 is the sensors' own motor:

| motor | position | rpm | rev Hz | PSD on arm sensor |
|---|---|---|---|---|
| motor2 | **rear-left -- the sensors' own arm** | 5230 | 87.27 | 4.286 |
| motor4 | rear-right | 5608 | 93.97 | 4.212 |
| motor3 | front-left | 7173 | 119.59 | 2.922 |
| motor1 | front-right | 6903 | 116.05 | 1.169 |

The slowest motors give the strongest 1x lines, by up to 3.7x. What that does and does not imply --
and why prop imbalance is no longer the leading explanation for the 1x band -- is graded in section
7 rather than argued here.

## 7. Open issues, graded

Following the form of `fables/Datasets/experiments-house-model.md`: findings held at explicit
confidence, and **dead theories kept visible with what killed them** rather than deleted. Several
of the entries below were stated to me as results before they were withdrawn, which is exactly why
they are recorded here instead of in a commit message nobody reads twice. **This topic closes when
the confirmed set explains the observed behaviour.**

### Confirmed

- **A mode at 152.9 Hz on the SW arm.** Two sensors 127 mm apart fitted independently: 152.92 and
  152.94 Hz, 160 ppm apart, on 3 and 5 of 16 strikes, r2 0.983 and 0.987. Survives every band-count
  cap tried, and no mode in the sweep is non-monotone in the cap. *(260926, hand-held base.)*
- **Motor2's blade-pass at 261.80 Hz is the largest line measured anywhere on this airframe.** PSD
  7.187 on the arm-end sensor, against 4.286 for that motor's own rev line. Motor2 is the rear-left
  motor and both sensors are on the rear-left arm. *(260923.)*
- **The forcing lines are three-blade.** Predicted 3rd-harmonic lines land within ~1 Hz on four
  independent instruments -- motor1 predicted 348.14 Hz, measured 347.23-349.37. *(260923.)*
- **Nothing drives 152.9 Hz at current hover RPM.** Nearest line is motor2's 2nd harmonic at
  174.53 Hz, 21.4 Hz above, amplifying about 3.3x against a 40x or 14x peak -- and that line's PSD
  is 0.738, a tenth of the blade-pass line above it.
- **The 1x rev amplitudes are rear-leading**: 4.286 / 4.212 / 2.922 / 1.169 at 5230 / 5608 / 7173 /
  6903 rpm. The slowest motors give the strongest lines.
- **campod-sw is on the rear-left arm**, motor2's. Operator-confirmed 2026-09-26.

### Current theories

- **The 1x ordering is set by the structural path from each motor to the sensor** -- own arm
  loudest, the other rear arm next, the far front arm last. Consistent with every number above, and
  **not tested.** The discriminating probe is the SE arm: it makes a different motor the "own" one,
  so the ordering should follow the sensor rather than the motor.
- **A sensor mount contributes damping of its own**, which would account for Q 14 against Q 40 at
  one frequency. Equally consistent with two modes inside one band that the two positions weight
  differently. Neither is tested, so **the damping from the bump test is not usable** and only the
  frequency is.

### Weakened

- ~~The 1x rev band is prop imbalance.~~ Both mechanisms that would make a 1x line strong predict
  **front**-leading -- imbalance forcing grows as speed squared, so the front pair at 1.29x speed
  makes 1.67x the force for identical eccentricity, and proximity to 152.9 Hz favours the front
  lines at 2.3-2.6x over the rear at 1.5-1.6x -- and the measurement is rear-leading. The line also
  survived replacing all four props (set A to set B, [props.md](props.md)), which four independent
  new props would not do if it were their balance state. **Not disproven:** a motor-side
  eccentricity is untouched by a prop change. But prop imbalance is no longer the leading
  explanation, and no prop-side story is needed to explain the pattern.

### Disproven

- ~~A mode at 242.5 Hz, sitting 1.4% from the 239.18 Hz forcing line.~~ Reported and withdrawn the
  same day, 2026-09-26. It appeared only under an arbitrary 120 Hz band floor at `n=6`, which freed
  a slot in the band list; deriving the floor from the data removed it. It was the most striking
  number in that run. Both the floor and the count are now non-arbitrary and asserted non-binding.
- ~~"Blade-pass" at 174-239 Hz.~~ Those are 2x rev. The analysis computed order 2 and labelled it
  blade-pass on a three-blade aircraft, which is where the real blade-pass at 261-359 Hz -- and the
  largest line on the airframe -- was hiding.
- ~~The camera sensor being taped on is why it reports more damping.~~ Asserted without a
  measurement. Decay rate belongs to the mode and does not depend on where it is observed, so the
  Q spread does need an explanation; this was a story, not one of them.
- ~~Set A may be unbalanced after the crash, so a cross-set vibration comparison is confounded.~~
  No measurement behind it, and the inference ran backwards: the set change is a natural experiment
  that already ran, and its result is the Weakened entry above.
- ~~The pod can be placed on the FC clock by fitting motion cross-correlation.~~ The timestamp chain
  and the fit agree to one envelope bin (+0.00 s camera, +0.10 s arm, NCC 0.675 and 0.759), so the
  fit was reproducing what the timestamps already said, one bin coarser -- and that bin was then
  applied as a correction, injecting 25 cycles of phase at 250 Hz.
- ~~The rest-magnitude departure is a scale error.~~ |a| must read 1.000 g on a bench and camera
  reads 1.0879 against arm 0.9874. A perfect scale with a bias along gravity reads identically: one
  orientation is one equation with two unknowns.

### Next steps

- [ ] **Clamp the base and repeat.** Every frequency above is of a hand-restrained assembly. This is
  the one that makes 152.9 Hz decision-grade rather than indicative.
- [ ] **Do the SE arm.** Both the control the method notes ask for, and the discriminating probe for
  the structural-path theory.
- [ ] **Softer impactor.** +/-16 g is the ADXL345's maximum and the hook exceeded it -- 71 and 143
  railed samples inside the strike window.
- [ ] **Mount both sensors the same way**, to remove the mounting from the Q question.
- [ ] **Fit the six held poses** in the 260926 capture, which separates scale from bias and makes
  cross-sensor amplitude comparison mean something.
- [ ] **Run `still_banding.py` on the 300 campod stills from 260923** at the 68.1 ms readout. The
  camera is on the arm carrying the loudest line, and at that readout rev writes 5.9 bands against
  11.9 for the 2nd harmonic -- a factor of two, where the OAK-D's 33 ms crowded 4 against 5 and left
  T11 resting on an eyeball count. It will not separate 152.9 Hz (10.4 bands) from the 2nd harmonic
  (11.9), which is a 14% pitch difference.
- [ ] **Fly the two-blade set.** It moves blade-pass without moving rev, which is the only clean
  separation of blade-rate from rotational-harmonic effects -- and it walks the dominant line from
  71% above the mode to roughly 14-30% above it.

## 8. Cross-checking against the flight controller

The FC logs raw IMU, so once it is powered on the bench alongside the pods there is a third
independent instrument looking at the same structure. Agreement on a common line -- with three
different clocks, three different sample rates and three different mounting points -- would
close the loop on the whole chain.

Not done yet. Recorded here so it is not re-derived.

## Appendix: reader properties that matter to analysis

From `containers/campod-camera/accel` (see its
[README](../../containers/campod-camera/README.md)):

- **There is no fixed poll rate and no fill threshold.** The reader alternates between devices
  and takes whatever each has. Batch spacing and size vary by design, so there is no cadence
  for an artifact to sit on -- the old 200 Hz line came from a fixed 200 Hz poll.
- **SPI runs at 3 MHz.** At 1.5 MHz the bus could not sustain two devices at ODR 3200: 84% of
  samples delivered and 2516 overruns in 2530 batches. At 3 MHz it is 100% and 3 overruns.
- **`drain_ns` and `gap_ns` are recorded per batch.** Use them to bound what the FIFO could
  have discarded, rather than inferring it from the interval between the two sensors' stamps.
- **The first batch of a run is normally flagged overrun.** The FIFO fills between
  `configure()` enabling measurement and the first read. Expect exactly one, at index 0.
- **Quantization is 3.9 mg/LSB** at every range in FULL_RES. A "peak" below that is not a
  measurement. The bench floor with nothing running was around 1 mg.
