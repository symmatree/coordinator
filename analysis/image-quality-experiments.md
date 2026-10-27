# Experiments: image quality

*Why the stills are not usable. Open topics, each with what has actually been observed and what
would decide it. Form follows `fables/Datasets/experiments-house-model.md`.*

**Nothing here is marked confirmed.** There are two powered flights with instrumented arms (260923
and 2026-10-25) and two bench runs (260926, bench-20261023-simflight-21). A thing that happened once in one flight is
an observation; a claim about how the airframe behaves is something else, and it earns that status by
surviving attempts to falsify it across a range of conditions -- not by being written down.

**Sharpness is the feature-tracking problem, not a separate one.** ODM has to reconstruct poses from
matched features across the collection run before densification matters at all. So "are the stills
sharp" and "can they be tracked" are the same question asked twice, and the second is the one that
gates a map.

## The two cameras

Both are wanted. **Good stills off either would be welcome, and off the Pi Zero camera much more so**
-- there is one OAK-D and four Pi Zero + camera sets.

| | OAK-D RGB | campod (Pi camera module) |
|---|---|---|
| sensor | IMX378 | IMX708 |
| readout | 33 ms over 3040 rows | 68.1 ms over 2592 rows |
| focus, through 2026-10-25 | VCM, pinned to a fixed value in software | VCM, pinned to a fixed value in software (`infinity` from #419) |
| focus, from 2026-10-26 | unchanged | Arducam B0310: IMX708 behind a fixed M12 lens, 120 deg H; no VCM, no `LensPosition` |
| mount, 260923 | hub, on bobbins | **VHB to the left-rear arm** |
| mount, 2026-10-25 | hub, on bobbins | clamshell at the arm-frame junction; campod-sw on the rear-left (Motor2) arm |

They are not the same instrument, but the differences that matter are narrower than I previously
wrote. Through 2026-10-25 **the focus mechanism was the same** -- a voice coil pinned by software in
both cases -- and the readout differs by a factor of two, which changes band arithmetic and nothing
else on its own. From 2026-10-26 the campod has no voice coil and the OAK-D still does. The B0310's
factory focus setting is not known; the one bench frame seen so far (indoor, about 1 m) read soft
(operator). Its lens is wider than the `imx708_wide` it replaced, about 0.45 mrad per pixel on
average against about 0.39, so pixel amplitudes are not comparable across the change.

**The 260923 campod mount was VHB, deliberately.** Not a hard mount: the prior was that hard-mounting
would not work, and VHB was the simplest first step of a soft one. Cameras will be on more arms than
one, so a result from this flight is a result about this mount on this arm.

**The vibration field at either camera was not measured on 260923.** The arm ADXL345 pair sat on
the arm, not on top of the VHB layer the camera sat on. The OAK-D has its own low-rate IMU whose data
has not been examined. The FC's IMU is low-rate and on bobbins.

**On 2026-10-25 each campod carried two ADXL345s:** one on the arm and one on the camera mounting,
near the camera. The camera-side sensor is on the mounting, not on the camera module and not on the
lens; it measures what the mounting does and cannot see motion inside the module. Its band RMS at
the motor lines is in *Measured baseline* below. For the fixed-lens modules an ADXL is taped
directly to the module housing (operator), which on a fixed-lens module rides with the lens and
sensor.

---

## Rolling shutter arithmetic -- not a theory

A periodic disturbance during readout writes itself into the frame as horizontal bands, and a band
pitch of *R* rows corresponds to `f = rows/readout_time / R`. That is division, and it follows from
how a rolling shutter works rather than from any measurement here.

For the two readouts, a line at *f* Hz gives:

| f (Hz) | campod bands/frame | OAK-D bands/frame |
|---|---|---|
| 87 | 5.9 | 2.9 |
| 120 | 8.1 | 4.0 |
| 153 | 10.4 | 5.1 |
| 175 | 11.9 | 5.8 |
| 262 | 17.8 | 8.6 |

Two consequences worth keeping separate from any theory. The campod's longer readout spreads the
same frequency over more bands, so counting is easier there. And **frequencies close in Hz are close
in band count** -- 153 and 175 Hz differ by 14% in pitch -- so band counting cannot distinguish
candidates that near each other.

---

## Measured baseline, 2026-10-25

Flight `2026-10-25-soft-mount-check-flight`, `fc-log-22.bin`, three-blade set B, motor soft mounts,
campod focus `infinity`, 5 ms exposure. Hover window FC t 252.3-260.3 s. Notebooks and JSON are in
that flight's `derived/`. ESC instance to motor: 0 = Motor1 front-right, 1 = Motor3 front-left,
2 = Motor4 rear-right, 3 = Motor2 rear-left, from `SERVOn_FUNCTION` and the pitch and yaw pairs.

**Motors.** Throttle output (`CTUN.ThO`) median 0.18; `MOT_THST_HOVER` in the param file is 0.35.

| motor | median rpm | once-per-rev | twice-per-rev | blade-pass (x3) |
|---|---|---|---|---|
| Motor1 front-right | 6443 | 107.4 Hz | 214.8 | 322.2 |
| Motor3 front-left | 6899 | 115.0 | 230.0 | 345.0 |
| Motor4 rear-right | 5728 | 95.5 | 190.9 | 286.4 |
| Motor2 rear-left | 4964 | 82.7 | 165.5 | 248.2 |

Thrust split by rpm^2: front/rear 1.55, left/right 0.97, Motor1+Motor2 against Motor3+Motor4 (the
two spin directions) 0.82. On a square X with 161 mm fore-aft half-spacing, the front/rear figure
puts the CG about 35 mm forward of the motor centre; the spin-direction figure is a steady yaw
moment and is not a CG effect. With the CG centred and the same total thrust the four rpm^2 values
average 36.6M, about 6050 rpm, 101 Hz. `docs/campod.md` was written against an assumed ~9900 rpm
and a 330 Hz two-blade fundamental.

**Acceleration at the lines**, band RMS in m/s^2 over +/- the notebook half-band, hover window. The
floor row is the median over 37 off-line bands of equal width. Bench floors are from
`bench-20261023-simflight-21`, same bands, motors off.

| | FC IMU0 | FC IMU1 | SE arm | SE camera mount | SW arm | SW camera mount |
|---|---|---|---|---|---|---|
| bench floor | -- | -- | 0.027 | 0.028 | 0.012 | 0.022 |
| flight floor | 0.058 | 0.058 | 0.841 | 0.479 | 0.711 | 0.373 |
| Motor2 once-per-rev | 0.696 | 0.787 | 1.045 | 0.618 | 1.243 | 0.475 |
| Motor4 once-per-rev | 0.568 | 0.607 | 0.797 | 0.843 | 1.079 | 0.625 |
| largest blade-pass | | | | 2.324 (Motor3) | 2.371 (Motor3) | 4.035 (Motor1) |

**Bands in the stills** (`still_banding.row_sharpness`, in-flight frames):

- SE: 215 of 230 frames fit a band, median 82.0 Hz (464-row pitch). Per frame the band frequency
  tracks Motor2's rev: r = 0.80, median |difference| 1.4 Hz; the best single scale against Motor2 is
  0.994 (residual 2.8 Hz) and against Motor4 1.113 (6.3 Hz); Motor2 and Motor4 revs are
  uncorrelated across frames (r = -0.05). Band amplitude is 0.42 when Motor2 runs 76-79 Hz and
  0.28-0.32 at its other speeds.
- SW: 86 of 236 frames fit a band, median 77.3 Hz; no per-frame tracking.
- Operator's read of the frames: about a tenth of each frame is sharp, and which rows those are
  changes from frame to frame.

**Displacement at the band pitch** (`derived/bands/radial_shift.py`, six strips per frame, uniform
shift U plus magnification term M): SE 214 frames, |U| 1.6 px, |M| 1.8 px per 1000 px; SW 85
frames, about 2 px for both. Per-strip amplitudes: 4.1 px in flight, 2.9 px armed on the ground,
2.8 and 5.7 px at wrong-pitch nulls. The synthetic check recovers about 70% of a known amplitude
with 1.2-1.7 px of leakage between U and M.

**Exposure arithmetic for the same lines.** For a swing of p at f Hz over an exposure t the smear is
p sin(pi f t). At 5 ms the 82 Hz line smears 0.96 p; at 1 ms, 0.26 p. Blade-pass at 250-345 Hz
smears the full swing at 5 ms and 0.71-0.88 p at 1 ms. Rolling-shutter waviness does not change
with exposure. Scaling this flight's gains by 5 for a 1 ms exposure: SE median 1.46 to 7.3, SW 1.12
to 5.6, 4% of frames above 16 on each pod.

---

## Topic 1 -- Do the ADXL345s have a stable, discoverable rest position?

Relative to the FC frame, and relative to each other. Everything downstream that compares the two
sensors, or places either in the airframe, needs this and it has not been shown.

**Why it is open:** it has not been demonstrated that the parts are stable enough for the question to
have an answer -- electronically (bias and scale drift) or physically (the mounting holding position
across battery swaps and flights).

**Observed:**

- On 260926, sitting still on a bench where |a| must read 1.000 g, the two channels read **1.0879 g**
  and **0.9874 g**. That is a real disagreement with gravity and with each other, measured once, in
  one orientation.
- The 260926 capture includes six held orientations, recorded for exactly this question. **They have
  not been fitted.** Until they are, scale and directional bias cannot be separated from one
  orientation -- one orientation is one equation.

**What would discriminate it:** fit the six orientations and see whether a single scale-and-bias model
explains all six. Repeat on a later capture and see whether the fitted values move. If they move
between captures with nothing touched, the premise fails and no cross-sensor amplitude comparison is
available.

### How the calibration is to be done

Operator's direction, recorded here so it is not re-derived or quietly re-litigated. This is method,
not a plan: it says how to do the solve whenever it is done, and what the data selection has to
respect for the answer to mean anything.

**Collection.** The vehicle is held by hand in each of six orientations. Between poses there is
positioning, hand shake, and the operator settling into a stable hold -- so a session is a small
number of usable spans separated by transients, not a continuous record of held poses.

**Data selection: one contiguous span per pose.** Fitting the whole session lets the transients
dominate, which is exactly backwards -- the point is to calibrate *from* the quiet parts, not to
average the noisy ones in and then try to work around them.

**Take the span from the CENTRE of a quiet period, not the first window that satisfies the
criterion.** A first-satisfying-window search lands on the leading edge of the criterion, which is
where the transient is still decaying and where the signal only just qualified. Centring the
requested duration inside the quiet region avoids collecting exactly the samples the criterion was
meant to exclude. This is a property of any threshold-crossing selector and applies beyond this
calibration.

**The whole-session alternative, kept rather than dismissed.** Processing the entire record and
exploiting temporal continuity between poses is a real option and might use information that
per-pose spans throw away. Its cost is that it also admits every positioning transient, so it needs
a model that can represent those or a way to downweight them. Not chosen for the first pass; not
ruled out.

**Sampling-rate drift over the session should be looked at**, and it is probably clock drift rather
than the part's output rate changing. It matters less here than it would elsewhere: the measurement
is the **direction and magnitude of the gravity vector while stationary in each pose**, not a
frequency response, so a slowly wrong time axis does not move a static mean. Look at it anyway --
if it is large or steppy it says something about the sensor path that other analyses will care
about, and "we thought we were measuring across samples what we were not" is the kind of thing that
is cheap to check and expensive to assume.

**What there is to solve for.** Six orientations give enough to determine scale and bias per axis
per sensor, and from those the **relative orientation of the two accelerometers to each other**.
With the FC dataflash covering the same poses there is a third instrument seeing the same gravity
vector, which ties both sensors to the **flight controller's frame** -- either as part of the
solution or as the residual that checks it. All three should agree about where gravity points in
each pose, and any disagreement is the measurement.

### What the literature says, and what transfers

Searched 2026-10-02. The short version: the method being used here is the consensus method, and
two things that were open are closed by it.

**[Frosio et al., *Autocalibration of MEMS Accelerometers*](https://www.researchgate.net/publication/220408257_Autocalibration_of_MEMS_Accelerometers)
and [Tedaldi et al., *A robust and easy to implement method for IMU calibration without external
equipments*](https://www.researchgate.net/publication/273383944_A_robust_and_easy_to_implement_method_for_IMU_calibration_without_external_equipments).**
The canonical in-field approach: the norm of the triad equals |g| during static intervals, solved
as a non-linear least squares problem. *Transfers fully* -- same sensor class, same constraint of
no external equipment, same few-deliberate-orientations regime. This is what `pose_calibration.py`
does, arrived at independently. Tedaldi additionally chooses the static-interval threshold BY the
fit, sweeping it and keeping the smallest residual, which is strictly better than choosing it by
judgement and is now implemented. *Caveat found on implementing it:* the sweep needs a model with
spare degrees of freedom. Swept against the 6-parameter fit on six poses it returns exactly zero
for every threshold, because an exactly determined fit absorbs whatever the selection hands it.
Pointed at an over-observed model it works -- and reports that for this capture the knob is not
load-bearing, moving the residual 0.3% across a 16x range of tolerance.

**[NXP AN4399, *High-Precision Calibration of a Three-Axis Accelerometer*](https://www.nxp.com/docs/en/application-note/AN4399.pdf)
and the ellipsoid-fitting line of work.** The identifiability result here is the important one and
it *transfers completely, being structural rather than empirical*: the magnitude constraint
determines an **ellipsoid**, which is 9 parameters -- three axis lengths, three orientation angles,
three offsets -- and those 9 cannot uniquely determine the 12 parameters of a full 3x3 gain matrix
plus bias, because the matrix's nine coefficients are not recoverable from the ellipsoid's six
shape-and-orientation parameters. **The rotation part of the matrix is invisible to |a| at any
number of poses.** That is exactly the runaway this project hit -- biases to 4000 g with the matrix
shrinking to compensate -- and it means the failure was structural, not a solver problem that
bounds would have fixed. *What does not transfer:* the "high-precision" framing assumes a
controlled fixture, where ours is a hand-held drone.

**[Six-position testing of MEMS accelerometers](https://www.researchgate.net/publication/283140754_Six-position_testing_of_MEMS_accelerometer)
and [An Optimal Calibration Method for a MEMS IMU](https://journals.sagepub.com/doi/10.5772/57516).**
Six positions determine bias and scale factors but **cannot** estimate axis misalignments or
non-orthogonalities. *Transfers exactly*, and was confirmed numerically here before it was read:
the Jacobian is short by 3 for the cross-axis model at six poses, whether those poses are
axis-aligned or tilted. *What does not transfer:* the procedure assumes a levelled surface with
each axis pointing alternately up and down, which a hand-held airframe cannot achieve -- ours are
approximate orientations, which is why the directions are solved for rather than assumed.

**[Examining the number of required stationary orientations](https://www.researchgate.net/publication/368680217_Examining_the_number_of_required_stationary_orientations_for_efficient_accelerometer_calibration).**
Classical methods need a minimum of twelve and preferably sixteen orientations, evenly spaced;
improved methods reach seven or ten with less dependence on distribution. *Transfers*, and agrees
with the independent Jacobian result in `pose_set_identifiability`: twelve minimum, fourteen
better conditioned. Convergence from two directions on the same number is worth more than either.

**[Attitude-Aided Linear Calibration of Triaxial Accelerometers](https://arxiv.org/html/2606.06308).**
Needs only five arbitrary orientations -- but by using an external attitude reference. *Does NOT
transfer as stated:* there is no attitude truth on this bench. The multi-sensor joint fit here is a
weaker cousin, since the sensors give each other RELATIVE attitude but nothing gives absolute, so
it buys spare degrees of freedom without buying the rotation part of the matrix.

**[Autocalibration using local gravity and temperature](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4187052/).**
The temperature term *transfers in principle and not in regime*: that work has days of wear data
with abundant incidental static periods in uncontrolled orientations, where we have six deliberate
ones. The reason to care is below.

**[ADXL345 datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/adxl345.pdf)
-- this is the actual part, so it transfers trivially, and it settles the open residual.**

| specification | ADXL345 | fitted here |
|---|---|---|
| cross-axis sensitivity | **+/-1%**, i.e. up to **0.57 deg** of apparent misalignment | joint-fit angular residual **0.30-0.43 deg** |
| sensitivity | 230-282 LSB/g (3.5-4.3 mg/LSB), typ 256 / 3.9 -- a **+/-10%** spread | effective 3.77-3.98 mg/LSB |
| zero-g offset X, Y | +/-150 mg (typ +/-35) | +44 to +55 mg |
| zero-g offset Z | +/-250 mg (typ +/-40) | -72 and -15 mg |
| sensitivity tempco | +/-0.01 %/degC | -- |
| zero-g offset tempco | X,Y +/-0.4 mg/degC, **Z +/-1.2 mg/degC** | -- |

**The unexplained residual is the part's specified cross-axis sensitivity.** 0.30-0.43 deg sits
inside the 0.57 deg that +/-1% coupling permits, so there is nothing wrong with these sensors and
nothing left to explain. Every fitted scale and bias is in spec too, and the 3.3% scale spread is
small against the +/-10% the part allows -- using the header's nominal 3.9 mg/LSB was always going
to leave a few percent on the table.

**Correction to the identifiability claim above, which was too strong.** A general gain matrix
factors as `M = R * S`, a rotation times a symmetric matrix. The magnitude constraint cannot see
**R**, the package's overall rotation -- that part is genuinely invisible at any number of poses.
But it fully determines **S**, and the off-diagonals of S *are* the cross-axis coupling. So
non-orthogonality IS measurable from gravity alone; only the absolute rotation is not, and that is
not needed here because sensor-to-sensor rotation is measured from the poses directly. The model to
fit is therefore 9 parameters -- symmetric matrix plus bias -- not 6 and not 12.

### What pose set to hold, for the 9-parameter model

Two families appear in the literature and both are geometrically what one would guess:

- **[Hung's twelve](https://www.iaeng.org/publication/WCE2011/WCE2011_pp2164-2167.pdf)**, as used
  on a V-block rig: six orthogonal positions plus six at 45 degrees between two axes.
- **[NASA TM-2020-5005041](https://ntrs.nasa.gov/api/citations/20205005041/downloads/NASA-TM-2020-5005041%20corrected.pdf)
  Table A.1**: pitch +/-45 with roll at -45/45/135/225, which works out to every sign combination of
  `(+-1/sqrt2, +-1/2, +-1/2)` -- 45 degrees from one axis and 60 from the other two.

**Scored as ADDITIONS to the six already being held**, which is the question that gets asked --
nobody discards poses they already have, and scoring sets in isolation answers a different and
misleading one. `incremental_identifiability` does this; the diagram is
`derived/pose-sets.png`.

| addition to the 6 cardinals | added | total | short by | cross-axis SE |
|---|---|---|---|---|
| ring about **one** axis, 90 deg | 0 | 6 | 3 | -- |
| ring about **one** axis, 45 deg | 4 | 10 | **2** | -- |
| ring about **one** axis, 30 deg | 8 | 14 | **2** | -- |
| ring about **one** axis, 22.5 deg | 12 | 18 | **2** | -- |
| ring about **one** axis, 15 deg | 20 | 26 | **2** | -- |
| **8 cube corners** | **8** | **14** | **0** | **0.0077 deg** |
| NASA 8 | 8 | 14 | 0 | 0.0103 deg |
| rings about all three axes, 45 deg | 12 | 18 | 0 | 0.0073 deg |
| 12 edge midpoints (the same points) | 12 | 18 | 0 | 0.0073 deg |
| rings about all three axes, 30 deg | 24 | 30 | 0 | 0.0059 deg |
| edges + corners | 20 | 26 | 0 | 0.0053 deg |

**A pose is the direction gravity points in the body frame -- 2 degrees of freedom -- and TWO
DIFFERENT ROTATIONS get confused here.** Yaw about the **gravity** axis is a no-op: body-frame
gravity is unchanged, so vehicle heading is free. Yaw about the **vehicle's own up axis** is NOT,
once the vehicle is tilted: gravity moves between body x and y while the body z component holds, so
spinning a tilted vehicle traces a line of constant elevation and every position is a new pose. The
two coincide only when the vehicle is level, and an earlier version of this section checked the
level case and wrongly concluded that look direction never matters.

That second rotation is the physically cheap pose family -- **one tilt, then spin** -- and it is
much easier to brace than separately setting up eight arbitrary orientations. Two facts decide
whether a sweep is worth doing:

- **PHASE MATTERS MORE THAN ELEVATION.** Spun to azimuth 0/90/180/270 the poses land in the same
  vertical planes the cardinals already occupy, refining existing great circles instead of adding
  one, and the cross-axis error comes out in the **millions of degrees**. Offset 45 degrees and it
  is 0.010; anything from roughly 15 to 75 degrees of offset works.
- **Tilt hard.** Elevation +/-35.26 deg -- the vehicle 54.7 deg off level -- gives 0.0077 deg.
  Gentler is much worse: +/-60 deg elevation, only 30 deg of tilt, gives 0.0205.

**Two sweeps of four at +/-35.26 degrees and 45 degrees of phase ARE the eight cube corners.** So
the cheapest sufficient addition is a pair of tilt-and-spin sweeps, not eight separate setups.

*Tool note:* `full_rank` is not `usable`. A rank test at a 1e-9 cutoff passed sets whose cross-axis
standard error was in the millions of degrees -- numerically rank 9, usefully rank 7. Usability is
now judged against the specification being measured: the part's own +/-1% cross-axis spec, 0.57 deg,
since a calibration that cannot beat the spec it is measuring is not a calibration.

**The right language is great circles, and the earlier claim that refining one axis "never helps"
was wrong.** A circle is the set of tilts about one axis. Standalone deficiency:

| circles sampled | 90 deg | 45 deg | 30 deg | 15 deg |
|---|---|---|---|---|
| 1 | short 5 | short 4 | short 4 | short 3 |
| 2 | short 3 | short 1 | short 1 | short 1 |
| 3 | **short 3** -- this *is* the six cardinals | **0** | 0 | 0 |

Refining does help -- 5 to 4 to 3 on one circle -- but it **saturates near 45 degrees per circle**
and never reaches sufficiency without all three. The six cardinals are exactly three circles at 90
degrees, which is why they earn their place over any smaller set: they take the deficiency from 5
to 3. And a 45-degree pose between "up" and "front" lies on the *y* circle, one of the three, so it
is in the group that does help.

What is true, and is the marginal case that got overstated: **refining a SINGLE circle past 45
degrees adds nothing.** Added to the cardinals, an equatorial ring leaves the deficiency at 2
whether it is sampled every 45 degrees (4 added) or every 15 (20 added). Points must land on more
than one circle.

Diagrams: `derived/pose-sets.png` (sphere) and `derived/pose-sets-flat.png` (azimuth against
elevation, so nothing hides behind the sphere, with the three tilt circles drawn so set membership
is visible).

A caution on all of the above: these are rank and conditioning results for noiseless synthetic
data at the measured noise level. They say which parameters are reachable and how precisely, not
that a hand-held pose can be held steadily enough to realise it.

**And this topic's own question gets a number from the datasheet rather than from more
measurement.** Z-axis offset drifts +/-1.2 mg/degC, so a 20 degC change moves Z bias by 24 mg --
a third of the camera-colocated sensor's fitted -72 mg. A calibration is therefore valid near the
temperature it was taken at and not elsewhere, which makes temperature a parameter of the result
rather than a caveat on it.

**Where the code and the answers live.** Beyond very initial exploration, this is code in the repo,
and the calibrations actually used are produced by CI or by a cluster job -- **not** numbers taken
from an interactive analysis and pasted into the repo as the one true calibration. A calibration
that cannot be regenerated from its inputs is not traceable to the data it came from, and the
regeneration is what makes it checkable when a sensor is moved or a session is re-run.

---

## Topic 2 -- Is the blur largely a focus problem?

**Stance when written (operator):** probably substantially, on the campod. Through 260926 no sharp
still had been seen off that camera under any circumstances, including stationary capture -- which
is what made focus the first suspect rather than motion or vibration.

**Since then:** the campod was found focused at 1.25 m while photographing the ground at flight
altitude, a defocus floor on every survey frame to that date; #419 set `infinity`, and 2026-10-25
flew at that setting. From 2026-10-26 the campods carry fixed-lens B0310 modules, so for them the
question becomes whether the factory focus is at infinity, which is not known.

**Observed:**

- Bench stills exist at approximately the right subject distance, taken deliberately for this. **They
  have not been analysed, and no path to them has been provided for direct inspection.** That is the
  cheapest outstanding item here and it needs no flying.
- The 260926 bench run added 507 stills with the vehicle static and deliberately tilted, so a range
  of distances is in frame.
- The campod's lens is pinned in software to a value chosen for a subject distance; whether the
  commanded position is *achieved*, and held, has not been verified against a target.

**Against, or at least complicating:** the OAK-D produced at least some sharp frames, and shares the
focus mechanism. So focus cannot be the whole story for both cameras -- though it may well be the
story for the campod specifically.

**What would discriminate it:** photograph a target at a known distance, stationary, sweeping the
commanded lens position, and score sharpness against position. If no commanded value produces a sharp
frame, focus is not the explanation and something else is wrong with that camera path. If one does,
the flight value can be set from the measurement.

---

## Topic 3 -- Is the blur structured as bands of blur and sharpness?

Carried forward from the OAK-D, where it was seen by eye. When written, **whether it was even present
in the campod stills was unknown**, and might not have been testable if they were uniformly soft --
banding is a *contrast* between bands, and there is no contrast to find in a frame that is soft
throughout.

**Observed, campod, 2026-10-25:** present. 215 of 230 in-flight SE frames and 86 of 236 SW frames
fit a band; the numbers are in *Measured baseline*. The OAK-D mono cameras are global-shutter, so
their never showing bands says nothing about the mechanism.

**Observed, OAK-D only:**

- An operator annotated 4 horizontal bands on one hover still (260730), which via the 33 ms readout
  is ~120 Hz, near the faster motors' rev rate. One frame, one flight, a visual count.
- The banding is not a whole-frame focus state: it alternates within a single frame and its phase
  moves between two frames 10 s apart.
- It survives a 5 ms exposure cap. At ~100 Hz a half cycle is ~5 ms, so a 5 ms exposure still
  integrates most of a peak-to-peak excursion.

**A caution that belongs with this topic, not buried elsewhere:** automated band detection on these
stills has a poor record. A var-Laplacian detector over-counted (6-10 bands, and fired on a ground
frame); another attempt reported band counts in the hundreds; a scene-cancelled ratio method was
internally consistent within one flight and unstable across flights. The visually obvious pattern has
repeatedly not been what the numeric methods found. Any new numeric result here should be checked
against frames marked up by eye before it is believed.

The method in use since 2026-10-25 is `still_banding.row_sharpness`: each row's 95th percentile of
local contrast, blown-out pixels excluded, rather than any row mean or variance. It agreed with the
operator's eye-marked bands on the campod frames it was first checked against, where a
Laplacian-energy ratio had put its crests on visibly blurry rows. That is agreement on a handful of
frames, not a validation; the module docstring records what it does and does not fix.

**What would have discriminated it:** whether any campod still shows a sharpness *contrast* between
rows at all. 2026-10-25 answered that; see above.

---

## Topic 4 -- Is the commanded focus actually held?

Distinct from topic 2: not whether the chosen value is right, but whether the VCM holds the position
it was told to hold, under vibration, for the duration of an exposure and across a flight.

**Why it is open:** there is no evidence either way. A voice coil holds position against a spring by
applied current; it is not a mechanical stop. Nothing has verified the achieved position in flight,
by VCM readback or driver state.

**From 2026-10-26** the campod modules have no voice coil, so this topic applies to the OAK-D colour
camera and to campod stills through 2026-10-25. The 2026-10-25 band amplitude peak at Motor2 76-79 Hz
(*Measured baseline*) is the one observation that bears on it: a lens on leaf springs is a resonator
in the 80-200 Hz range `docs/campod.md` cites, and so is the mounting; the camera-mount ADXL has not
been checked frame by frame for a peak at the same rev rate.

**What would discriminate it:** image a fixed target on the bench with motors running and compare
sharpness to motors-off at the same commanded position. If it degrades with the motors on while the
subject and command are unchanged, the lens is moving.

---

## Topic 5 -- What sets the frequency of any banding

Stated as a topic rather than a theory, because the candidate mechanisms are not yet distinct enough
to predict different outcomes.

The interesting form of the question is **whether the banding frequency follows whichever vibration
component is largest where the camera sits.** That is testable in principle and would explain a
rev-rate result on one camera and something else on another. It needs the vibration field at the
camera, which from 2026-10-25 the camera-mount ADXL gives at the mounting.

**Observed, 2026-10-25:**

- The SE band frequency follows Motor2's rev rate frame by frame (r = 0.80, median difference
  1.4 Hz) and not Motor4's, whose rev is uncorrelated with Motor2's across frames. Motor2 is the
  slowest motor (82.7 Hz in hover) and the one whose arm carries campod-sw.
- At the SE camera mount, Motor2's rev line is 0.618 m/s^2 against a 0.479 floor and Motor4's is
  0.843; the largest line there is Motor3's blade-pass at 2.324. The band frequency is not the
  largest line at that sensor.
- Band amplitude against Motor2's rev: 0.42 at 76-79 Hz, 0.28-0.32 at the other speeds flown.

**Observed, earlier:**

- 260923, the arm sensor on the left-rear arm: the largest line at that sensor is its own motor's
  blade-pass, larger than that motor's rev line. That sensor sits in prop wash on the arm carrying the
  motor, so this may be a fact about that location rather than about the airframe, and it is not
  anchored to anything seen in an image.
- The same flight's line frequencies were computed from mean RPM over the analysis window. Whether
  RPM held steady enough across that window for a mean to be meaningful **has not been checked**, and
  the lines move if it did not.
- 260923 flew three-blade props, so blade-pass is the third harmonic on that flight. Blade count is a
  fact about a flight, recorded in that flight's notes -- two- and three-blade props are on hand
  specifically because fundamentals and harmonics in the 100-250 Hz region collide easily and blade
  count is one of the few ways to move one family of lines without moving the others.

- The 260926 informal strike test produced a repeated peak near **152.9 Hz** on both arm channels
  (arm 152.94 +/- 0.02 Hz over 5 impulses, camera-colocated 152.92 +/- 0.04 Hz over 3, of 16
  impulses each). Caveats that travel with it: the vehicle was on a table held down by hand and
  moved under every strike, so the root condition is not a clamped one; the strikes clipped the
  sensors; the two channels disagree about damping by about a factor of three at that frequency,
  which they should not if it is one mode; and it is **not among the six most prominent peaks** on
  either channel, which matters only because the band search used to stop at six and so never
  fitted it at all -- it now has no cap.
  Whether it has anything to do with an image is untested -- no image measurement has been
  compared against it.

**What would discriminate it:** a banding frequency measured from an image, on a camera whose local
vibration is also measured, on two configurations that move the candidate lines apart. 2026-10-25
gives the first two on one configuration. Moving the lines: 8-inch props at the same thrust raise
rpm by about 1.56 (thrust goes as n^2 D^4), 83 to about 129 Hz; two-blade 10-inch props move
blade-pass from 3x to 2x rev, onto the 165-230 Hz twice-per-rev family; centring the CG moves the
lowest line from 83 to about 96-101 Hz and brings the four lines together.

---

## Topic 6 -- Motion blur during the exposure

**Observed, OAK-D only, 260712:** in-flight sharpness collapsed ~43x against at-rest (var-Laplacian
median 4827 to 113) and no in-flight frame reached at-rest sharpness; exposure ran 1.2 to 6.1 ms;
streak and arc morphology rather than uniform softness. Correlates were VIBE -0.81, exposure -0.66,
EKF velocity -0.53, gyro -0.42.

**Why that does not settle anything:** VIBE correlating harder than exposure means vibration and
translation are not separated, and at takeoff everything covaries. "No in-flight frame reaches at-rest
sharpness" is as much a statement about vibration as about translation. The set also spans a focus
configuration change, so the morphology may not be one population.

**A handheld collection run exists** which removes the vibration confound entirely -- motion without
rotors -- and has never been used for this. That is the available discriminator and it needs no new
flying.

**Not available, despite my having asked for it twice:** "motors running on the ground." The rotors
cannot turn at hover RPM with the vehicle on the ground because it will not stay there. Movement
without vibration is available by carrying it; vibration without movement is not, short of removing
the props and running the bells up, which probably does not reproduce the disturbance since moving
blades are likely most of it.

**Operator's current framing, which changes what matters here:** if hover can be held to within a few
centimetres of drift, motion blur can be set aside and the question becomes why a hover still is not
sharp. That makes topics 2 and 4 the ones that matter now.

**What has been disproven:** `blur = rate x exposure`. It holds only while the rate is roughly
constant across the exposure, `f*t_exp` below about 0.45, which is ~90 Hz at 5 ms. Above that the
motion reverses within the exposure and the smear saturates while the formula keeps growing: it
overstates by 1.48x at 120 Hz and 8.89x at 800 Hz. Every rotor-band blur figure computed that way was
inflated.

**One exposure datum from the OAK-D, 260730 (coordinator#158):** the global-shutter monos ran at a
median 0.75 ms and were sharp while the colour still, pinned at the 5 ms cap, was not. The monos
have no rolling shutter and coarser pixels, so this bears on exposure and not on the rest.

---

## Topic 7 -- Image motion measured against a global-shutter reference (method, awaiting data)

Every measurement above infers image motion indirectly: from row sharpness, from band pitch, or from
accelerometers that are not on the camera module. The OAK-D carries a rolling-shutter color sensor
and global-shutter mono sensors on one device. A mono frame from the same moment as a color still is
a reference for where each row of the color frame should sit.

**The idea:** find bright, high-contrast transitions in the mono frame, such as shadow lines. Find the
same transitions in the color still and measure how they bend there relative to the mono.
- The bending of an edge against row is image displacement against time across the 33 ms readout,
  at the IMX378's 10.9 us line time.
- That record does not depend on which structure moves or on where an accelerometer sits.
- An edge's own shape in the scene, such as a shadow following ground relief, is common to two
  cameras this close together. It appears in both images and drops out of the comparison.

**No 3D reconstruction.** Frames are chosen from a stable hover with a far-field view: low FC EKF
motion and no kick in the accelerometers around the still. Under those conditions one fixed
mono-to-color mapping, from the device calibration, carries the edges across. Parallax from the
few-cm baseline is then a near-constant offset rather than something to solve per feature.

**What it needs from a capture** (a capture outside the VIO pipeline, with static config or MQTT
topics, owned by the flight-platform side):

- color still at full resolution and the mono frame(s), unrectified;
- device timestamps for both streams on one clock, with exposure time and gain per frame, so the
  mono exposure can be placed within the color readout;
- the device calibration (intrinsics, color-to-mono extrinsics), once per session;
- FC log and accelerometer data covering the stills, for choosing the frames.

**Limit:** this measures image motion at the OAK-D mount, on the body. It reaches the campod images
only if a mono and a campod see the same ground, so one can be mapped onto the other.
