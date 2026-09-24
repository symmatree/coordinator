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

## 4. Bump testing (planned)

To get structural properties **uncontaminated by the drive**, excite the structure with an
impulse and watch it ring down with the motors off. Not yet done; this section is the intent
and the method, and should be replaced with results.

Method notes, for whoever does it first:

- **Attach the accelerometer rigidly to the arm** -- tape, wax, or glue. A breadboard has its
  own modes and they will dominate anything the arm does. Measuring a loose sensor measures
  the mounting.
- **Tap several times and analyse each ringdown separately.** Welch-averaging across taps
  smears the transients; each individual decay is an independent estimate of both frequency
  *and* damping.
- **Damping is the useful number.** A lightly-damped resonance near a rotor harmonic is a
  problem; the same frequency heavily damped is not. That is the quantity a spectrum of a
  running motor cannot give you.
- **Do both the SE and SW arms.** They should be identical but mirrored, so they are each
  other's control: a mode that appears on one and not the other is an asymmetry -- a loose
  fastener, a cracked layer, a different cable route -- rather than a property of the design.
  That comparison is worth more than either measurement alone.

## 5. A blur accounting was attempted on 260923, and this is what is wrong with it

An attempt was made to decompose the blur in the 260923 stills into named terms in pixels --
defocus, rotor vibration, low-frequency attitude, translation -- and to rank them. **That
accounting is not established and should not be quoted.** It is written down here rather than
deleted because the failure modes are the useful part, and because a tidy table of attributed
costs is exactly the kind of thing that gets believed on sight.

Two properties of how it was produced should colour everything below. It was assembled by
running analyses and reporting each result as it came, so the conclusion moved repeatedly --
at one point the rotor term was 0.95 px, later 2.8 px, then ~2 px, each stated to two or three
significant figures. **A number whose value reverses while its precision does not is a
conclusion wearing the costume of a measurement.** And the only end-to-end test of the whole
chain -- does predicted blur actually predict still sharpness -- came back weak, and the chain
was carried on being quoted anyway.

### 5.1 `blur = angular rate x exposure` is wrong above ~90 Hz

This is the load-bearing formula and it is false in exactly the band the rotor lines occupy.
It holds only while the rate is roughly constant across the exposure. Above `f * t_exp ~ 0.45`
the motion reverses *within* the exposure, the smear saturates at the angular amplitude, and
the formula keeps growing linearly with rate. Measured against a simulated sinusoid at a 5 ms
exposure:

| f | 60 Hz | 120 | 200 | 300 | 500 | 800 |
|---|---|---|---|---|---|---|
| formula overstates by | 1.19x | 1.48x | 2.22x | 3.33x | 5.55x | 8.89x |

Every rotor-band figure produced this way is inflated, worse the higher the band. The
low-frequency attitude terms (1-6 Hz) are affected by only ~10%.

### 5.2 Quadrature summation assumes things not in evidence

Terms were combined as `sqrt(sum of squares)`. That assumes they are independent and have
comparable point-spread shapes. Defocus is a disc; motion blur is a line; rolling-shutter
banding is neither. Nothing was done to justify combining them this way.

### 5.3 The pod's angular rate is a chain of four assumptions

Angular rate at the pod was derived as `alpha = (a_arm - R a_cam) / |r|`, then integrated to
rate. In order:

- **Perpendicularity.** `alpha = diff/|r|` recovers only the component perpendicular to the
  separation vector. Which sensor axis points outboard along the arm is **not recorded
  anywhere** -- not in `campod-electrical.md`, not in the sidecars -- so the decomposition
  cannot be done and the result is an upper bound on one component.
- **The centripetal term `omega x (omega x r)` was dropped** without estimating it.
- **A 30 Hz validity threshold was asserted, not derived.** Below it the differential is
  dominated by calibration residual: at 1-6 Hz the method reported 0.268 g of differential
  between two points 127 mm apart, which would require ~1186 deg/s^2 and is plainly not real.
  The frequency at which leakage stops dominating was eyeballed from that failure, not
  computed from the calibration error.
- **`R` carries 12 degrees of unexplained misalignment** from the nearest exact axis
  permutation. Two boards on a rigid mount should sit within a few degrees. The camera-side
  sensor is taped against an oval with no flat, which is a plausible cause, but it is a
  hypothesis and not a measurement.

### 5.4 The calibration behind `R` is weaker than it looks

`R` and the per-axis scale/bias came from a bench tumble on 2026-09-24 (`campod-sw`, session
`8d6f79ec`), **not from flight data**. Three problems:

- The intended method was a six-position calibration from *static* holds. The procedure
  actually requested was "slow continuous tumble", so the static intervals all landed in
  essentially one orientation -- direction-covariance eigenvalues `[0.986, 0.0135, 0.0006]`,
  rank 1. That was an error in the instruction, not in the tumbling.
- Falling back to low-passed *moving* data recovers the coverage (`[0.862, 0.074, 0.064]`) but
  contaminates the sphere fit with hand-motion linear acceleration. The post-fit `|g|` spread
  of 0.0097 g **is** that contamination and is not a precision figure.
- An unbounded fit of the same model ran away to a 0.011 scale and a 92 g bias, which also
  satisfies `|S(x-b)| = 1`. Bounds were then imposed to stop it. A model that needs bounds to
  avoid absurdity is under-constrained by the data it was given.

### 5.5 The comparison against the FC gyro conflates two things

The pod was reported as seeing 4.5x the FC's rotational rate above 30 Hz. The FC is on
isolation bobbins **and** behind the IMU's own anti-alias filtering, and nothing here separates
those. The ratio is not a mount transmissibility and should not be read as one. Separately,
the FC gyro was used as a proxy for the OAK-D on the grounds that both are bobbin-mounted --
but they are different bobbins carrying different masses.

### 5.6 The optical constants are published specifications, not measurements

The IFOV figures (509 urad/px campod, 327 urad/px OAK-D) come from vendor focal lengths and
pixel pitches. Every blur figure in pixels scales linearly with them. The defocus figures
additionally assume the commanded lens position (0.8 dioptres) is *achieved*, which has never
been checked against a target at a known distance.

### 5.7 The images do not confirm any of it

This is the part that should have stopped the accounting being quoted. Testing predicted blur
against measured sharpness over 140 campod stills from 260923, trimmed 10 s inside each end of
the armed window:

- `corr(predicted blur, log sharpness)` = **-0.28**
- across terciles, predicted blur spans **7x** while median sharpness moves **13%**

The tercile ordering is monotonic, so motion blur is doing *something*. But a model that
predicts a 7x change and produces a 13% response has not been validated by that test; it has
been weakly survived by it. The reading offered at the time -- that a large constant term is
swamping a variable one -- is consistent with the data and is also exactly what one would say
to keep a model alive.

`var_laplacian` is also not scene-normalised, and across frames of changing ground it tracks
scene content. At n=140 over a moving scene that is plenty to hide the effect, which means
**the test as run cannot distinguish "the model is wrong" from "the test is too weak"**. That
is a reason to build a better test, not a reason to keep the model.

### 5.8 What would actually settle it

Each of these attacks one step rather than producing another table:

- **Re-derive the blur kernel** as the integral of angular displacement over the exposure,
  per band, instead of `rate x exposure`. Cheap, no new data, and it is the largest single
  correction identified so far.
- **Six-position calibration with held poses**, which is a different procedure from a
  continuous tumble and needs to be asked for as such.
- **A bench shot of a flat textured target at known distances**, which settles the achieved
  focus, the real depth of field against a photogrammetry criterion rather than a 2 px circle
  of confusion, and whether the frame has a spatial defect -- with no motion and no scene
  change to confound it.
- **Which sensor axis points outboard**, which is a fact about the hardware and unblocks the
  perpendicular decomposition.
- **A sharpness metric that survives a changing scene**, without which no in-flight test of
  any of this has the power to confirm or refute it.

## 6. Cross-checking against the flight controller

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
