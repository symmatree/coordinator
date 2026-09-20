# campod Overview

## Overview

Each of the four arms carries some amount of sensing. Currently this is a campod: a pi zero 2 w driving a camera module, collecting from a pair of accelerometers, and
talking to the coordinator over a gadget network on USB.

On the Zero 2 W's behaviour under load -- it becomes barely able to make progress when
the capture stack starts and when it stops, sometimes for minutes -- see
[analysis/pi-zero-unresponsiveness-experiments.md](../analysis/pi-zero-unresponsiveness-experiments.md).

Campods are mounted at the **arm-frame junction** (where the arm meets the central frame plates -- the structurally stiffest point of the arm). The mount is a clamshell clamping the arm,
with the pi zero mounted on the top side and the camera in the lower half, with a gap between the clamshell faces so the arm-faces are compression loaded for friction, as well as
a certain amount of geometric constraint (the arms are not quite parallel sections in that region). Currently held on with two zipties around the outside, in grooves at each end of the
mount unit.

Future iteration adds a **vertical ring** of cameras to complement this horizontal (downward-looking) ring. The idea would be to provide **360-degree side-scan**
in a plane approximately perpendicular to the direction of travel, covering the full azimuthal circle. Combined with the horizontal ring, the vertical ring extends coverage upward from the upper limit of the downward facing camera and provide a full "tunnel" as it travels; target use case for this vertical ring is collection missions in the understory where there are features of interest (trees) as well as lots of
hazards that we would like to model. We could also potentially use these upward cameras to detect canopy gaps for safely "surfacing" into better GPS coverage as a "logical loop closure".

Intent, not built: Each Zero is responsible for triggering its camera and recording the results locally. USB is used for upstream communication (simulated network device), including low-rate telemetry, NTP, and libcamera sync messages from some pacesetter. The **Coordinator** (the Raspberry Pi 4B that also runs VIO -- see [central-hub.md](rekon10/central-hub.md)) bridges the USB network to the sibling Zeros and serves NTP for "absolute" time initialization. It also collects telemetry and informationally reports successful captures back through MAVLink. Those capture times can get stamped into the telemetry and dataflash logs. This is NOT a load-bearing timestamp signal, just telemetry for the operator to know the system thinks it is capturing.

### Aim geometry

This discussion was for 8 cameras and "normal" camera modules; the current prototype is using Wide camera modules and only 4 units. So the principles below hold, but the actual tilt angles and
coverage will be different. The key constraints are that the cameras should capture nadir just above the bottom of the frame (assuming say a 3m flight altitude at a minimum) and should cover a
complete "bowl" below the device, with enough overlap between cameras that a single "frame" can (generally) be aligned based on shared features. (The alternative is acceptable but worse: having
insufficient overlap means we can only reconstruct over time, not from a single position. This means we have to trust our extrinsics to be correct and stable, and that we couldn't do any kind of
point-in-time partial panorama.

#### 8-camera

All 8 cameras are aimed at **-70 degrees depression** (20 degrees from nadir), distributed azimuthally at 45-degree increments offset 22.5 degrees from the flight line. Using compass-point naming (forward = N): **NNE, ENE, ESE, SSE, SSW, WSW, WNW, NNW**.

No camera points straight down. The array captures sides of vertical structures (walls, tree trunks, terrain relief) that a nadir camera cannot see. This is the fundamental design intent -- heightfields and dense point clouds require viewing geometry from many angles, not just overhead.

**Why -70 degrees:** The Camera Module 3 standard has 66 degrees horizontal / 41 degrees vertical FOV (75 degrees diagonal). At -70 degrees depression with 20.5-degree vertical half-FOV:

- **Bottom edge:** -70 - 20.5 = -90.5 degrees. Just past nadir -- by design, the bottom edge kisses straight down. No nadir gap exists in the array.
- **Top edge:** -70 + 20.5 = -49.5 degrees. Well below horizon; every pixel looks at least 49.5 degrees below horizontal.

The oblique projection at the top edge widens the effective azimuthal footprint: the 66-degree horizontal FOV sweeps approximately 86 degrees of azimuth at the -49.5-degree top edge. With 8 cameras at 45-degree spacing, adjacent cameras overlap by ~41 degrees in the far field -- nearly complete double coverage everywhere. Cameras mirrored through a plane perpendicular to the axis of travel (e.g. ENE and WSW) see opposite sides of the same objects (at different points in time).

### Pod camera assignment

For the 4 camera design, the initial assumption is "outward" facing from each NE / SE / SW / NW pointing direction. The "cross-eyed" alternatives could be considered to either give better nadir coverage, or
to work around landing gear or other interferenece.

#### 8-camera version

Again, I put a lot of thought into this but i want to get a 4-node version working first (and if it's sufficient, that would be nice!)

Two design constraints govern which cameras share a pod. These are choices imposed for specific benefits, not inherent requirements of the system.

**Constraint 1 -- no angularly-adjacent cameras in the same pod (for baseline):** Adjacent cameras in the azimuthal ring are less than an inch apart if co-located on the same arm. Requiring non-adjacent cameras in each pod guarantees that each adjacent pair has ~4-5 inches of baseline in overlap regions. This baseline is not useful for stereo at 25 m altitude, but could be valuable for nearfield depth (twigs, obstacles).

**Constraint 2 -- each pod holds cameras exactly 90 degrees apart (for mounting simplicity):** This is stricter than constraint 1 and implies it. Each pod holds one "forward/back-ish" and one "side-ish" camera (e.g. NNE + ESE). Benefits: landing/ground-protection feet can stay in camera blind spots and be roughly identical (rotated) across all 4 legs; 90-degree pairs are easier to validate visually and mechanically; more common parts across pods despite arm geometry requiring at least mirroring.

Final angle-to-node assignment is TBD and iterable; these constraints define the feasible set.

## Hardware

### CAD starter library

A first-pass OpenSCAD library of reusable "blank" components now lives at:

- `components/pi-blanks.scad`

It provides starter placeholders for:

- `pi_zero2w_blank(...)` -- fixed board outline and 4-hole pattern, with optional envelope
- `camera_module3_blank(...)` -- fixed standard CM3 board outline and 4-hole pattern, with optional lens keepout

These are intended as mount-design primitives for the downward pods, vertical ring nodes, and rover nodes before any detailed cosmetic modeling.

### Thermal

These are all unproven claims about load and thermals.

- **Camera Module 3:** Not a thermal concern. Pulling still frames at 1-2 Hz for photogrammetry is very low duty cycle.
- **Pi Zero 2 W:** The real thermal bottleneck. The quad-core CPU runs very hot under load (chrony, network, USB gadget, SD writes). Will hit 80 degrees C throttling if it can't breathe.
- **Solution:** Full-length aluminum heatsinks on the Zeros, with the pod design leaving the center channel open for prop-wash cooling.
- **Hardware sources:** Pi Zero 2 W, Camera Module 3 (standard), ribbon cables, and heatsinks from PiShop.

### Proposed: Signal wiring (PPS timing from DS3234 SQW)

Run a **twisted pair** from the hub PPS buffer (driven by [**SparkFun DeadOn RTC DS3234**](https://www.sparkfun.com/sparkfun-deadon-rtc-breakout-ds3234.html) **SQW**, typically 1 Hz): signal wire + dedicated signal ground (any GND pin from the Pi header). This keeps the loop area near zero and prevents ESC EMI from corrupting the pulse.

At the Pi side, connect the signal ground through a **100-ohm resistor** to prevent it from becoming a high-current shortcut during a motor failure, while still providing a clean 3.3 V reference.


## Vibration and camera mounting rationale

This section documents the analysis and design alternatives so future reviewers don't re-litigate the vibration question from scratch. See also [oak-d-mount.md](rekon10/oak-d-mount.md) for the OAK-D's current vibration isolation approach (bobbins).

### Spectrum of isolation approaches

**Extreme isolation (gondola/pendulum):** Hang the cameras on a suspended platform below the drone, decoupled from frame vibration by compliant tethers. Cameras stay rigid to each other. Not the preferred path.

**Moderate isolation (bobbins, like the OAK-D mount):** Elastomeric isolators absorb high-frequency vibration while maintaining macro-scale pose rigidity. This empirically worked to make stills from the OAK-D legible
but it still has bands of vibration. Equivalent isolation for the pi camera modules would need to account for the much lighter mass of the cameras.

**Rigid mounting:** Clamshell around the carbon arm + bolted to frame mounting holes at the arm-frame junction. Cameras become part of the frame's rigid body. This maximizes the available mass to couple to, but
only if it doesn't decompose into nodes and modes. Empirically this was unusable for the OAK-D; no conclusive cause established due to the "howling nightmare" imagery being hard to decompose into causes.

### First-principles displacement analysis: Probably wrong

This is an interesting argument but expected to be wrong in practice. We have 2- and 3-blade props and hover at way under 50% throttle, at present. We will evaluate this logic with a series of instrumented
flights once the accelerometers are working, but the unusable images from the OAK-D (prior to isolation) and the fact that every single drone I can find details on does SOME kind of soft mounting for its
cameras makes me think this is an attractive but low-odds analysis. Note that versions of this analysis have tried to defend "why the oak-d is different" but ignore that the key evidence from the oak-d was
specifically on its usability for still imagery not VIO.

The dominant vibration source is the 2-blade props. At hover (~50% throttle), the motors spin at roughly 900 KV * 22 V * 0.5 = ~9900 RPM, giving a 2-per-rev fundamental of ~330 Hz. At this frequency, vibration amplitude on a stiff carbon fiber structure at the arm-frame junction (not the motor end) is expected to be in the tens-of-microns range.

At ~1 cm GSD, one pixel corresponds to ~7.5 mm of camera displacement. Even 0.1 mm (100 microns) of vibration amplitude at the camera = ~0.013 pixels. Millimeter-scale displacement would be needed for visible effects in imagery, and at 330 Hz that would be catastrophically violent -- audible, tactile, and likely destructive.

The harmonic notch filter (fed by bidirectional DShot RPM telemetry from the AM32 ESCs) removes motor vibration from the FC's control loop, preventing the FC from amplifying vibration through feedback. This doesn't physically reduce frame vibration, but it prevents the control system from making it worse.

**Cantilever mode shape (first bending):** A carbon arm is roughly a **cantilever**: the **motor end** is an **antinode** for the lowest bending mode (large transverse motion); the **bolted root** is near a **displacement node** for that same mode. Pods at the **arm-frame junction** therefore see **less** of that mode's tip flapping than pods at mid-arm or at the motor would. This is **not** isolation from **all** motion: the root is not a perfect clamp, **higher-order** bending modes and **torsion** still move the junction, and **whole-body** attitude motion moves the hub and arms together.

**Control-loop coupling (separate from prop-line resonance):** Vibration can appear on **gyros**; the attitude loop can then **command torque** at frequencies where **phase margin** is thin, adding energy into the airframe. That is a real failure mode in FPV tuning lore, but blaming **D alone** at a fixed **30-80 Hz** is oversimplified -- **P, I, D, filters, and delays** set the limit-cycle frequency together. **Harmonic notch** (above) targets **blade-pass** from **RPM**; **gyro low-pass**, **D-term filtering**, **gain discipline**, and the **FC soft mount** are the other usual mitigations. For sinusoidal motion, **peak acceleration ~ amplitude * (2*pi*f)^2** -- **1 mm** at **50 Hz** is **~10 g** peak (the formula Gemini used is correct). Whether the **hub** ever reaches **1 mm** at those frequencies in your build is an empirical question; it would be **obvious** in flight and in logs long before "mythical" extremes. **Whole-hub** motion at **smaller** amplitude or **lower** frequency can still matter for stills before anything that dramatic.

### Autofocus voice coil (VCM) vs whole-body vibration

The body of this section is pre-flight analysis, and should be viewed with caution. And the distinctions between "lens glued in place", "lens held in place by VCM", and "auto-focus algo actively running" must be
kept sharper than presented here. Autofocus in flight is likely to be massively compromised by vibration reading as defocus.

---

Camera Module 3 autofocus uses a **voice coil motor (VCM)**: the focusing lens group is suspended and translated axially relative to the sensor package. It is not a rigidly locked cine lens. That adds an **internal** mechanical degree of freedom in addition to rigid-body motion of the pod.

This is a **different failure mode** from rolling shutter geometry:

- **Rolling shutter shear / line-time jello** come from **rigid-body** motion (and readout order) during exposure. The first-principles argument above is about **whole-camera** displacement at the arm-frame junction; it does not bound motion **inside** the lens stack.
- **VCM-related blur** would come from **axial** (focus) drift or small **relative** motion of the lens group **with respect to the sensor** during integration. That widens the point spread (defocus-like or generalized blur). **Micron-scale** axial error can hurt sharpness before millimeter-scale whole-body motion dominates the RS discussion.

**Why this is expected to be benign at operating RPM:** Phone-class VCM actuators (the CM3 uses the same construction) have a mechanical resonance set by the lens mass and leaf-spring stiffness, typically in the **80-200 Hz** range. The prop fundamental at hover (~330 Hz for 2-blade, higher for 3-blade) sits **above** that resonance by roughly 2-4x. Above resonance, transmissibility **rolls off** -- the lens group is too heavy to follow the housing, so the VCM suspension acts as a **passive lowpass filter** at operating RPM. The lens stays relatively still while the housing vibrates around it. During motor spinup the RPM sweeps **through** resonance transiently, but mapping captures do not happen during spinup.

**Survivability:** The VCM will not be physically damaged by frame vibration at these amplitudes. Phone cameras with the same actuator architecture survive walking, pocket vibration, car rides, and drop impacts -- environments with far more energy at far more problematic (low) frequencies than a carbon fiber frame at ~330 Hz and tens-of-microns amplitude. Tens of microns of axial lens shift also produce no detectable defocus at mapping altitudes (depth of field at 25 m AGL is meters deep). "Destroy the VCM" or "overwhelm its ability to hold focus" would require energy orders of magnitude beyond what the arm-frame junction delivers.

Whether prop-band vibration actually excites the VCM suspension enough to cause **subtle** image softness on this mount remains an **empirical** question. The **Pod-integrated vibration logging** plan ties mechanical spectra **at the camera load path** to image quality (sharpness, AF behavior) so this is testable rather than hand-waved.

**Mitigations if tests show a problem:** Prefer **fixed-focus** mapping captures -- lock lens position after one AF cycle, or use a constant-focus / manual mode in software so the VCM is not hunting while the shutter is open; avoid AF moves immediately before each shot on a vibrating airframe.

### What we don't know

- **Actual vibration amplitudes.** The first-principles estimate above is reasonable but unverified. The FC has floating-hole isolator mounts, but its accelerometer and gyro data will still be useful for characterizing frame vibration when the motors first spin up.
- **2-blade vs 3-blade props.** 3-blade props shift the fundamental to 3x RPM (potentially different amplitude and frequency). Comparing FC vibration data between 2-blade and 3-blade configurations would be informative.
- **Resonant modes of the pod itself.** The clamshell pod has its own structural dynamics. If a pod resonance happens to coincide with the prop frequency, local amplification could occur. Test imagery will reveal this.
- **VCM suspension at prop-band frequencies.** Whether the floating lens group picks up enough relative motion during a still exposure to soften imagery is unknown without correlation between **pod-path accel** logs and sharpness / AF state.

---

## Rolling shutter considerations

The Camera Module 3 uses the Sony IMX708, which is an **electronic rolling shutter** sensor. It reads out line-by-line from top to bottom, not all at once. This section distinguishes between "has a rolling shutter sensor" (a hardware fact) and "exhibits rolling shutter problems" (an empirical question that depends on speed, vibration, and processing).

### "Has RS" vs "has RS problems"

The DJI Mini 3 Pro also has a rolling shutter sensor. It was flown at 5.6 m/s (20 km/h) at 25 m AGL for house-mapping experiments documented in [experiments-house-model.md](https://github.com/symmatree/fables/blob/main/Datasets/experiments-house-model.md). The best result (house-2) reconstructed 400/446 shots with 0.86 px reprojection error, **without rolling shutter correction enabled in ODM**. The problems identified exhaustively in that document -- autofocus locking on treetops, nearfield parallax, turnaround gimbal instability, boundary tuning -- are not rolling shutter artifacts. RS correction was never enabled because there was no evidence it was needed.

The Rekon array at 3-5 m/s with similar or shorter readout times should have less forward-flight RS displacement than the DJI at 5.6 m/s.

### Forward-flight displacement

At 3-5 m/s with ~26 ms readout (approximate for the IMX708 in 12 MP mode -- needs confirmation from datasheet or measurement): 7.8-13 cm of physical camera displacement during readout. At ~1 cm GSD this is 8-13 pixels of systematic affine shear (parallelogram distortion). This is predictable, not random, and is exactly what ODM's rolling shutter correction models.

### Look-angle geometry

The simple "v * t_readout" is the naive worst case. Actual RS displacement per pixel depends on the angle between the velocity vector and each camera's line of sight.

With all 8 cameras at -70 degrees depression, the depression is steep enough that the geometry is still nadir-like: the cos^2 correction factor ranges from 0.88 (near-along-track cameras like NNE) to ~0.94 (cross-track cameras like ENE). The cross-track cameras (ENE, ESE, WSW, WNW) see the most RS because forward velocity is nearly perpendicular to their line of sight. The along-track cameras (NNE, SSE, SSW, NNW) see ~6% less RS because some velocity is along their line of sight.

The DJI comparison is slightly conservative: the DJI at -70 forward-facing had its velocity partially along the LOS, giving it ~12% less RS than pure nadir. The Rekon's cross-track cameras are modestly worse off. Net: the DJI at 5.6 m/s forward-facing likely saw comparable or slightly less RS distortion per pixel than the Rekon's worst-case cross-track cameras at 3-5 m/s. The DJI produced usable photogrammetry without RS correction. The Rekon should too -- but enabling RS correction in ODM is free accuracy, especially for the cross-track cameras.

### Vibration-induced jello vs forward-flight shear

Two distinct RS artifacts with different signatures:

- **Forward-flight shear:** Systematic parallelogram distortion from camera translation during readout. Predictable, correctable given readout time. Signature: consistent lean of vertical lines in the direction of flight.
- **Vibration jello:** Periodic waviness from camera oscillation during readout. Requires millimeter-scale lateral displacement at the camera (see Vibration section above for why this is unlikely at ~330 Hz on a rigid carbon frame). Signature: sinusoidal waviness in lines that should be straight.
- **Internal lens motion (VCM):** Camera Module 3 autofocus suspends the lens on a voice coil. Relative axial or lateral motion of the lens group during integration causes **defocus-like or generalized blur**, which is **not** the same artifact as RS shear or line-time jello. See **Autofocus voice coil (VCM) vs whole-body vibration** under *Vibration and camera mounting rationale*.

### Processing: ODM rolling shutter correction

ODM supports `--rolling-shutter` with a readout time parameter. Document the IMX708 readout time once confirmed (approximately 26 ms for 12 MP mode based on similar quad-bayer sensors). Enabling RS correction is a free accuracy improvement -- it models the affine shear and removes it from the bundle adjustment.

### Multi-camera spatial advantage

8 cameras with inter-camera overlap provide the dense feature matching that RS correction models rely on. This is coverage a single-camera platform cannot match.

### Multi-camera temporal advantage

This is arguably the most important advantage of the synchronized array.

With PPS-synchronized capture (microsecond alignment via chrony), all 8 cameras freeze the scene at the same instant. A single camera (like the DJI) doing two crosshatch passes captures the same area minutes apart -- shadows shift, leaves move, twigs change position between passes.

The matching problems identified in the DJI experiments (nearfield parallax, "parallax soup," wind-induced twig movement, inconsistent features across captures) are fundamentally **single-camera sequential problems**. With synchronized multi-camera capture:

- **Twig features are frozen** at one physical position across all 8 images. Within a single synchronized capture, even transient nearfield objects are perfectly stable features.
- **Intra-pod stereo baseline** (~4-5 inches between cameras in the same pod) produces small, manageable parallax even for nearfield objects, vs meters of baseline between sequential single-camera captures.
- **Forward-and-back cameras** capture opposite sides of a tree within seconds of passing overhead, vs a lawnmower grid where front and back come from different passes (a full track-width of lateral displacement, minutes apart).
- **Feature tracks** may not extend across captures taken seconds later (wind moves things), but within each synchronized burst the stitching web should be far more robust than sequential grids.
