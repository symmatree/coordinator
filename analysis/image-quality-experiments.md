# Experiments: image quality

*Why the stills are not usable. Open topics, each with what has actually been observed and what
would decide it. Form follows `fables/Datasets/experiments-house-model.md`.*

**Nothing here is marked confirmed.** There is one powered flight with instrumented arms (260923) and
one bench run (260926) whose analysis is barely started. A thing that happened once in one flight is
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
| focus | VCM, pinned to a fixed value in software | VCM, pinned to a fixed value in software |
| mount, 260923 | hub, on bobbins | **VHB to the left-rear arm** |

They are not the same instrument, but the differences that matter are narrower than I previously
wrote: **the focus mechanism is the same** -- a voice coil pinned by software in both cases -- and the
readout differs by a factor of two, which changes band arithmetic and nothing else on its own.

**The 260923 campod mount was VHB, deliberately.** Not a hard mount: the prior was that hard-mounting
would not work, and VHB was the simplest first step of a soft one. Cameras will be on more arms than
one, so a result from this flight is a result about this mount on this arm.

**The vibration field at either camera is not known.** The arm ADXL345 pair sits on the arm, not on
top of the VHB layer the camera sits on. The OAK-D has its own low-rate IMU whose data has not been
examined. The FC's IMU is low-rate and on bobbins. Nothing available measures what either sensor
actually experiences, so any statement of the form "this camera sits in an X-dominated field" is a
theory and is treated as one below.

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

`ellipsoid_identifiability` evaluates them against this project's measured noise:

| pose set | n | short by | cond | cross-axis SE | bias SE |
|---|---|---|---|---|---|
| cardinals (as flown) | 6 | **3** | -- | -- | -- |
| NASA 8 | 8 | **2** | -- | -- | -- |
| cube corners | 8 | **2** | -- | -- | -- |
| **edge midpoints** | 12 | 0 | 2.1 | **0.007 deg** | 0.12 mg |
| Hung 12 | 12 | 0 | 2.8 | 0.013 deg | 0.18 mg |
| cardinals + cube | 14 | 0 | 1.6 | 0.008 deg | 0.12 mg |
| cardinals + edges | 18 | 0 | 1.5 | 0.007 deg | 0.10 mg |
| single-axis circle | 8 | **4** | -- | -- | -- |

**The cardinals contribute almost nothing to the cross terms**, so twelve edge midpoints alone beat
Hung's cardinals-plus-six at the same pose count. An axis-aligned pose has zero projection on two
axes and so cannot excite their coupling -- which is why adding 45-degree poses matters and adding
more cardinals does not. **Eight of anything is insufficient**, including both published eight-point
sets, and a single-axis rotary fixture is worse than six hand-held cardinals because gravity never
acquires a component along its rotation axis.

Against the datasheet's +/-1% cross-axis spec, which is 0.57 degrees of apparent misalignment,
0.007 degrees is an 80x margin. So precision is not the constraint and the only real question is
how many orientations can be held steadily: **twelve edge midpoints is the recommendation, and
anything from twelve upward measures the coupling comfortably.**

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

**Current stance (operator):** probably substantially, on the campod. No sharp still has been seen off
that camera under any circumstances, including stationary capture -- which is what makes focus the
first suspect rather than motion or vibration.

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

Carried forward from the OAK-D, where it was seen by eye. **Whether it is even present in the campod
stills is unknown**, and may not be testable with the stills so far if they are uniformly soft --
banding is a *contrast* between bands, and there is no contrast to find in a frame that is soft
throughout.

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

**What would discriminate it:** whether any campod still shows a sharpness *contrast* between rows at
all. If none does, the topic is not yet testable on that camera and waits on topic 2.

---

## Topic 4 -- Is the commanded focus actually held?

Distinct from topic 2: not whether the chosen value is right, but whether the VCM holds the position
it was told to hold, under vibration, for the duration of an exposure and across a flight.

**Why it is open:** there is no evidence either way. A voice coil holds position against a spring by
applied current; it is not a mechanical stop. Nothing has verified the achieved position in flight,
by VCM readback or driver state.

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
camera, which as noted above nothing currently measures.

**Observed:**

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
vibration is also measured, on two configurations that move the candidate lines apart. Nothing
currently satisfies all three.

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
