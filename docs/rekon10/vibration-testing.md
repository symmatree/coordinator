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

A mode matters only if something excites it. The forcing on this airframe is the rotors: each
motor puts a line at its rev frequency and at multiples of it. Props are **Master Airscrew MR
10x4.5 2-blade**, so blade-pass is the 2nd harmonic.

Measured in the 260923 hover window (FC 245-354 s), with the output-to-motor map read from
`SERVOn_FUNCTION`. `|H|` is single-degree-of-freedom amplification at the mode,
`1/sqrt((1-r^2)^2 + (2*zeta*r)^2)` with `r = f/152.93`:

| forcing line | position | Hz | r | \|H\| at Q=40 | \|H\| at Q=14 | RPM shift to land on the mode |
|---|---|---|---|---|---|---|
| motor2 rev | rear-left, 5230 rpm | 87.17 | 0.570 | 1.48 | 1.48 | +75.4% |
| motor4 rev | rear-right, 5608 rpm | 93.47 | 0.611 | 1.60 | 1.59 | +63.6% |
| motor1 rev | front-right, 6903 rpm | 115.05 | 0.752 | 2.30 | 2.29 | +32.9% |
| motor3 rev | front-left, 7173 rpm | 119.54 | 0.782 | 2.57 | 2.54 | +27.9% |
| motor2 blade-pass | rear-left | 174.33 | 1.140 | 3.32 | 3.22 | -12.3% |
| motor4 blade-pass | rear-right | 186.93 | 1.222 | 2.02 | 1.99 | -18.2% |
| motor1 blade-pass | front-right | 230.10 | 1.505 | 0.79 | 0.79 | -33.5% |
| motor3 blade-pass | front-left | 239.08 | 1.563 | 0.69 | 0.69 | -36.0% |

**Nothing is on it now.** The closest line is motor2's blade-pass at 174.33 Hz, 21.4 Hz above,
which is 5.6 half-power bandwidths away at Q=40 and 2.0 at Q=14. It still picks up about 3.3x
amplification over static, against a 40x or 14x peak.

**The unusable damping does not block this question.** Off resonance the response is set by the
stiffness term `(1-r^2)`, not by damping, which is why the Q=40 and Q=14 columns above are
effectively identical. Damping only decides the answer when a line is ON the mode -- so the
number that is missing is exactly the number needed to say how bad landing on it would be, and
not needed to say that nothing is on it today.

**Which direction each prop change moves things**, as arithmetic:

- **Smaller diameter** raises hover RPM, walking every rev line up. The front pair at 115-120 Hz
  reaches 152.9 Hz on a +28 to +33% RPM increase; the rear pair needs +64 to +75%.
- **Blade count changes where blade-pass sits without moving rev.** At 2 blades the current
  blade-pass lines are 174-239 Hz, straddling the mode from above. At 3 blades and the same RPM
  they move to 261-359 Hz, away from it -- while the rev lines stay where they are.
- So the two changes move different lines in different directions, and a diameter change is the
  one that walks a line toward 152.9 Hz from below.

**All of this is bounded by the base not having been clamped.** The shift percentages are
distances to a mode frequency measured on a hand-held vehicle. A clamped measurement is what
would make them decision-grade.

## 6. The 1x rev band does not need a prop-imbalance explanation

A strong line at 1x rotation is the textbook imbalance signature, and imbalance is the reading it
invites. On this airframe that reading is not supported by the amplitude pattern, and it was never
written down here -- this section exists so it does not get re-invented.

Rev-line PSD on the arm-end sensor, 260923 hover, from
`derived/vibration-spectrogram.json`:

| motor | position | rpm | rev Hz | PSD on arm sensor |
|---|---|---|---|---|
| motor2 | rear-left | 5230 | 87.27 | 4.286 |
| motor4 | rear-right | 5608 | 93.97 | 4.212 |
| motor3 | front-left | 7173 | 119.59 | 2.922 |
| motor1 | front-right | 6903 | 116.05 | 1.169 |

**The slowest motors produce the strongest 1x lines, by up to 3.7x.** Two mechanisms would make a
1x line strong and both predict the opposite ordering:

- **Imbalance forcing grows as the square of speed.** For the same residual mass eccentricity on
  every prop, the front pair at 1.29x the rear speed produces 1.67x the force. Front should lead.
- **Proximity to 152.9 Hz.** The front rev lines at 115-120 Hz sit closer to the mode than the
  rear at 87-94 Hz, and amplify 2.3-2.6x against 1.5-1.6x. Front should lead again.

Measured ordering is rear-leading. So neither RPM-squared forcing nor the structural mode is what
sets the 1x amplitudes, and a difference in imbalance between props is not needed to explain the
pattern -- which is consistent with the pattern surviving a change to four new props, freshly
mounted, where four independent new props sharing one imbalance is not a plausible story.

What the ordering is consistent with is the structural path from each motor to the sensor: both
sensors are on one arm, and the two motors nearest it in the structure dominate. **Confirming that
requires knowing which arm the pod is on, and this repo does not record it** -- the SE/SW naming
plus the mirrored-arm framing in section 4 suggests the two rear arms, but that is an inference.
It should be written down, because it determines how every per-motor amplitude in this document is
read.

## 7. Cross-checking against the flight controller

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
