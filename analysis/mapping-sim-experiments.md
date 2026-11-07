# Experiments: mapping from synthetic captures

*What the mapping pipeline does with data whose answer is known. Open topics, each with what has
been observed and what would decide it. Form follows `fables/Datasets/experiments-house-model.md`.*

## The premise

Simulated captures can settle real-world parameters before they are flown: the lens, the camera
mounts, the flight path, the prior accuracy, and the processing settings. A synthetic run has a
known shape, so a reconstruction of it can be scored exactly. Changing one parameter in a config
and re-running isolates that parameter's effect on the map, which a real flight cannot. Doing this
also decouples the processing pipeline from data capture: it can be developed, timed and compared
while the vehicle is on the bench.

The premise is itself a theory. It holds for a parameter only as far as the simulation models what
that parameter interacts with. What the render does not model is listed in
[`mapping_sim/README.md`](mapping_sim/README.md); today that is rolling shutter, motion blur,
vibration, defocus, noise, exposure, lens shading and lighting. A result about, say, line spacing
transfers to real flights only if none of those change the answer, and that is checked by
comparing against a real flight once one exists, not assumed.

**Nothing here is marked confirmed.** Four runs have completed, each once; none has been repeated.

## Method

- **The tool:** [`mapping_sim/`](mapping_sim/). A config names a scene, a rig, a path and the
  priors. `render.py` writes the stills, `geo.txt` and `truth.json`, and `score.py` scores an
  OpenSfM `reconstruction.json` against the truth.
- **A config is one experiment.** Varying a parameter is another config, not a feature of the
  tool. A sweep is several submissions.
- **Running:** the `tiles` `mapping-sim` WorkflowTemplate renders on lancer, runs the ODM CLI
  (`opendronemap/odm:3.6.2`) to the end of OpenSfM, and scores. Each run is a directory under
  `mapping-sim/` on the NAS `datasets` share, with `provenance.json` recording the coordinator
  commit, the config and the ODM image.

## Runs

| run | config | images | render | OpenSfM | result |
|---|---|---|---|---|---|
| `as-built-20261104-1814` | `as-built.yaml`, earlier path (40 x 40 m at 15 m) | 55 of 280 | stopped | -- | not a result: the render step ran on a 3-CPU shared worker at about 100 s per image, and was stopped to move it to lancer |
| `as-built-20261104-2240` | `as-built.yaml`: one 45 m pass each way, 15 m apart, at 40 m | 128 | 10 min, 16 processes | 6.6 min | below |
| `map-test-20261105-1551` | `map-test-mission.yaml`: the 2026-10-30 mission as planned, at 30 m | 1104 | 109 min, ray-traced | 68 min | topic 2 |
| `map-test-flown-20261105-2105` | `map-test-flown.yaml`: the same mission along the FC's estimated poses | 1272 | 120 min, ray-traced | 90 min | topics 2, 4 |
| `map-test-flown-rig-20261107` | ODM again on the render above, with OpenSfM's rig model | 1272 | (reused) | 58 min, ended in ODM's statistics step | topic 4 |
| `map-test-flown-rig-20261107b` | `map-test-flown.yaml`, a camera model per mount | -- | stopped | -- | not a result: the render was ray-traced and was stopped to use the rasteriser |
| `map-test-flown-rig-20261107c` | the same | -- | failed | -- | not a result: lancer ran a cached notebook image from before moderngl |
| `map-test-flown-20261107d` | the same, rasterised, WGS84 priors | 1272 | running | -- | topic 4: then ODM with (`-rig`) and without (`-free`) the rig model |

OpenSfM times are the sum of its stages in `opensfm/profile.log`. The renders before 2026-11-07
were ray-traced; the rasteriser is now the only renderer.

### `as-built-20261104-2240`

As-built rig: four B0310s modelled as equidistant fisheye at f = 2200 px, at the cheese-plate
corners, 45 deg off track, 56.26 deg down, landscape. Ideal capture, 1 cm priors, the original scene
of a textured plane, four boxes and four cylinders.

- All 128 images in one reconstruction.
- Similarity fit to the truth: scale 0.99964, rotation 3e-6 deg.
- Camera position error, median / p90 / max: 1.9 / 3.1 / 4.1 mm. Rotation error: 0.0079 / 0.0088 /
  0.0093 deg. Each camera alone is within 1.7-2.1 mm and 0.0072-0.0087 deg median.
- Lens: recovered focal 0.477409 (x) / 0.477407 (y) against 0.477431 true; centre offset and
  k1-k4 all under 1e-4 against zero true. All four cameras were grouped as one lens.
- Sparse-point distance to the true surfaces, 60,570 points, median / p90 / max / rms: 4.6 cm /
  27 cm / 20 m / 33 cm. Not yet split by range or by surface.
- Anchoring across time, from `tracks.csv` (measured with a one-off script, not yet in
  `score.py`): no reconstructed point is seen at only one capture time. The capture span of a
  point, p10 / median / p90, is 1 / 10 / 31 captures (3 / 30 / 93 m of flight). 75-94% of each
  capture's points are also seen 5 or more captures away.
- ODM extracted features at `feature_process_size: 2304`, half the sensor width.

## Topics

### 1. Does ideal data reconstruct?

On the one short path above, the poses and the lens come back to millimetres and thousandths of a
degree. The point-to-surface tail is unexplained. Splitting it by range from the camera and by
surface would say whether it is distant ground at coarse footprint or a reconstruction fault.

### 2. A real mission over a realistic layout

`map-test-mission.yaml` flies `ardupilot/map-test.waypoints`, the mission flown on 2026-10-30, at
30 m: six passes of 96-147 m, 22-26 m apart, 1104 images. The scene puts a 12 x 9 x 8 m house at
the centre of the mission area with a 10 m yard round it, then rings of 3 x 3 x 15 m trees every
10 m out to 100 m, for scale and occlusion. This is the first run that has distant overlapping
passes, so it is where range-dependent redundancy and scale variation would first show.

`map-test-flown.yaml` flies the same mission along the FC's own pose estimates from that flight,
with real heading, roll and pitch.

| | mission as planned | as flown |
|---|---|---|
| images reconstructed | 1104 / 1104 | 1272 / 1272 |
| similarity fit: scale, rotation | 0.99964, 0.0044 deg | 0.99964, 0.0032 deg |
| camera position error, median / p90 / max | 7.2 / 10.8 / 15.8 mm | 6.6 / 12.1 / 33.2 mm |
| camera rotation error, median / p90 / max | 0.0079 / 0.0092 / 0.0120 deg | 0.0080 / 0.0112 / 0.0216 deg |
| sparse points; distance to true surface, median / p90 | 840,671; 3.2 / 23 cm | 926,434; 3.2 / 24 cm |
| OpenSfM reconstruct stage | 2382 s | 3366 s |
| ODM pod: wall, peak / mean memory, core-hours | 72 min, 17.8 / 9.1 GiB, 5.9 | 93 min, 21.2 / 11.4 GiB, 6.9 |

The recovered focal length was 0.47744-0.47745 in both, against 0.47743 true, from a starting
value of 0.4722 (the EXIF 35 mm equivalent is an integer, 17 / 36): the 1.1% starting error did
not survive bundle adjustment. All four cameras were one lens model in these runs, because the
render gave them identical EXIF; since coordinator#714 each mount is its own model.

### 2a. Point error against sighting geometry

[`mapping-sim-features.ipynb`](mapping-sim-features.ipynb) treats each track as a scene point and
measures, per reconstructed point, its error against the mean of its observations' true surface
points, and three properties of its sightings: time from first to last, the largest angle
between rays to the cameras that saw it, and median range. On both runs above, with points whose
observations all lie within 0.25 m of each other:

- Error, p50 / p90 / p99: 5.4 cm / 41-43 cm / 3.2-3.3 m.
- Spearman correlation with error: angle subtended -0.52, range +0.44 to +0.45, range / angle
  +0.56 to +0.57, time in view -0.31 to -0.32.
- Time in view, within cells of fixed angle and range: +0.03 to +0.04.

Time in view tracks error through the angle it buys; with angle and range held, it carries
almost none. The flown path's longer time-in-view tail (p99 214 s against 93 s) left the error
percentiles unchanged.

### 3. Resolution and texture recovery

The house carries a Siemens star (36 sector pairs) on each wall and the roof. The radius at which a
reconstruction stops resolving the sectors reads off its resolution there. This needs
densification, so the ODM step has to run past OpenSfM. Until then the stars also give the sparse
stage sharp corners.

### 4. Rigid rig against free alignment

Solve once with the four cameras held at fixed relative poses (OpenSfM's rig model) and once with
every image free, then compare. At perfect data the two should agree. Two measures: the free
solution's camera-to-rig poses, as spread between captures and as distance from the true mounts;
and OpenSfM's run time per stage, since a rig model reduces the unknowns.

**How the rig gets in.** ODM 3.6.2 has no option for a multi-camera rig (its `multi_camera` path
is multispectral band alignment). Its OpenSfM reads `rig_cameras.json` and `rig_assignments.json`
from the project if they are there, and ODM leaves them alone.
[`opensfm_rig.py`](mapping_sim/opensfm_rig.py) writes them from a render, and the `tiles`
workflows' `rig=yes` places them before ODM runs. With them OpenSfM solves one pose per capture.
It holds the mounts fixed while there are 10 captures or fewer; past that it adjusts them against
the given values as priors (0.1 m, 0.1 rad). Each capture gets one GPS prior: the mean of its
images' positions, at their mean accuracy, applied to the rig's origin. Since coordinator#719 that
origin is the mean of the camera centres.

**Observed on `map-test-flown-rig-20261107`**, against `map-test-flown-20261105-2105` on the same
render:

| | free | rig |
|---|---|---|
| camera position error after the similarity fit, median / p90 / max | 6.6 / 12 / 33 mm | 0.84 / 1.3 / 2.0 mm |
| camera rotation error, median / max | 0.008 / 0.022 deg | 0.0019 / 0.0049 deg |
| point distance to true surface, median / p90 | 3.2 / 24 cm | 3.1 / 24 cm |
| similarity fit rotation | 0.0032 deg | 0.652 deg, about vertical |
| camera centres against OpenSfM's own GPS priors, horizontal, median / max | 0.62 / 1.08 m | 1 / 2 mm |
| OpenSfM reconstruct stage | 3366 s | 1797 s |
| ODM pod: peak / mean memory, core-hours | 21.2 / 11.4 GiB, 6.9 | 21.2 / 9.4 GiB, 6.2 |

Detect, match and tracks took the same time in both (about 28 min). The mounts the rig run ended
with were each 0.0286 m and 0.021-0.022 deg from the ones given, the same shift for all four:
consistent with the 3 cm between the body origin and the cameras' mean, which the GPS prior was
applied across before coordinator#719.

**The 0.652 deg is the frame of the priors, not the reconstruction.** Until coordinator#716,
`render.py` wrote `geo.txt` as the scene's east/north added to a false UTM 17N origin, so the
scene's north was UTM grid north. At this site (1.004 deg east of zone 17's central meridian,
40.53 N) grid north is 0.652 deg from true north, and OpenSfM works in a true-north tangent frame.
The rig run followed its priors to millimetres and so came out rotated against the truth by
exactly that. Since coordinator#716, `geo.txt` is WGS84 converted from the scene's tangent plane.

**Not yet known:**

- Why the free run sits 0.6-1.1 m from its own 1 cm priors while its shape matches the truth. Both
  runs had the same priors at the same accuracy; the free run applies one per image, the rig run
  one per capture. `map-test-flown-20261107d-free` is the first free run with priors in the right
  frame.
- Whether the rig and free runs agree with each other and with the truth once the priors are in
  the right frame: `map-test-flown-20261107d-rig` and `-free`, on one render, run one after the
  other on lancer.
- The rig run's ODM ended in `compute_statistics` (`KeyError` in OpenSfM's `stats.rig_statistics`,
  which tests a stale loop variable and fails on a rig camera not named after a camera model), so
  ODM skipped its exports and the workflow skipped scoring; it was scored by hand. Since
  coordinator#714 each rig camera is named after its camera model, which that function skips.
  `-rig` is the first run with that.

### 5. Anchoring forward and backward in time

Whether captures tie to their neighbours in time, or reconstruct as islands of one or two
captures placed loosely against the rest because each capture's feature budget went on tracks
within itself. Measured once (above), where it did not happen. To go into `score.py` and be read
on every run, especially longer and noisier ones.

### 6. Noise

With topics 1-5 as a baseline at perfection, add noise one place at a time and measure how it
damages the reconstruction and the derived rig poses: position priors, timing, mounts, intrinsics,
then image effects. Prior covariances can come from a flown log so that they vary over time the
way real ones do; the 2026-10-30 log is a candidate.

### 7. Lenses and camera count

Four wide B0310s against the original eight narrower Camera Module 3s, for resolution and the cost
of distortion within the camera model. A separate single-camera arm: how resolution for one sensor
trades against reconstruction quality and the number of passes, over varying tracks.

### 8. Lens starting values, and what real stills will carry

OpenSfM starts each camera's focal length from EXIF (the 35 mm equivalent / 36) and holds it to
that with a log-scale prior of 0.01, while bundle adjustment refines focal and k1-k4 against the
images. In topic 2 a 1.1% starting error was refined away.

The campod stills from `campod-capture` have carried no EXIF. coordinator#717 (open) stamps them
on board: Make `Arducam`, Model the pod name, capture time to the microsecond from
`exposure_wall_ns`, FocalLength 2.87 mm and FocalLengthIn35mmFilm 16, from Arducam's B0310 spec.
16 / 36 = 0.444 as a starting ratio; the sim's lens is 2200 px, 0.477, chosen for 120 deg
horizontal under r = f theta. The two differ by about 7%, and nothing measured says which is
nearer the real lens.

Not yet known: how far the starting focal can be off before OpenSfM's initial pair and resection
fail or settle wrong. The sim can test it by rendering with the EXIF focal off by a set amount.
The sim's lens has no distortion, so it tests less than a real lens would.

## Open

- `map-test-flown-20261107d`: the render is running; ODM with the rig model, then without, follow
  on the same render (topic 4).
- coordinator#719, the rig origin at the mean camera centre: on auto-merge. The `-rig` run waits
  for it.
- The `tiles` `mapping-sim` WorkflowTemplates on the cluster have no `imagePullPolicy`, though
  tiles#953 sets `Always` and Argo CD reports the app Synced at that commit. The `20261107d` runs
  set it per workflow with a `podSpecPatch`.
- The render step requests no GPU, so on lancer the rasteriser runs on Mesa's llvmpipe. Its full
  resolution speed there is unmeasured; on the Radeon it was about 0.3 s per image.
- OpenSfM's `stats.rig_statistics` bug (topic 4) is not reported upstream.
- The match-depth section of `mapping-sim-features.ipynb` is on a local branch,
  `mapping-sim-match-depth`, not yet a PR.
- EKF-covariance noise on the reported poses (topic 6) is not started.
