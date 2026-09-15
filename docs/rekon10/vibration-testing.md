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

## 5. Cross-checking against the flight controller

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
