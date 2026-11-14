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

**Nothing here is marked confirmed.** Six runs have reached a reconstruction, each once; none has
been repeated.

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
| `map-test-flown-20261107d` | the same, rasterised, WGS84 priors | 1272 | 14 min, llvmpipe on lancer | -- | the render for the two runs below |
| `map-test-flown-20261107d-rig` | ODM on that render with the rig model | 1272 | (reused) | 56 min to `reconstruction.json`; ODM then failed in `export_geocoords` | topic 4 |
| `map-test-flown-20261107d-free` | ODM on that render without it | 1272 | (reused) | 88 min, ODM completed | topic 4 |
| `map-test-flown-20261107d-dense` | ODM past OpenSfM on that render, seeded with `-free`'s OpenSfM | 1272 | (reused) | (reused) | topic 3; four attempts, none finished: the last two OOM-killed fusing |

OpenSfM times are the sum of its stages in `opensfm/profile.log`. The renders before 2026-11-07
were ray-traced; the rasteriser is now the only renderer. For the `20261107d` runs, stage times
are from the modification times of each stage's output in `opensfm/`.

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
| similarity fit: scale, rotation | 0.99964, 0.652 deg | 0.99964, 0.652 deg |
| camera position error, median / p90 / max | 0.66 / 1.11 / 2.95 mm | 0.72 / 1.15 / 1.79 mm |
| camera rotation error, median / p90 / max | 0.0015 / 0.0025 / 0.0043 deg | 0.0019 / 0.0030 / 0.0053 deg |
| sparse points; distance to true surface, median / p90 | 840,671; 3.1 / 23 cm | 926,434; 3.0 / 24 cm |
| OpenSfM reconstruct stage | 2382 s | 3366 s |
| ODM pod: wall, peak / mean memory, core-hours | 72 min, 17.8 / 9.1 GiB, 5.9 | 93 min, 21.2 / 11.4 GiB, 6.9 |

The scores are of each run's `opensfm/reconstruction.topocentric.json`, in
`score-topocentric.json` in its run directory. The workflow's `score.json` beside it scored ODM's
UTM copy (topic 4) and gave 7.2 and 6.6 mm median position error, a 0.0044 and 0.0032 deg fit
rotation. The 0.652 deg is the frame of these runs' priors (topic 4). So is the 0.99964 scale, as
far as it is accounted for: the priors were scene metres laid out as UTM grid metres, and the UTM
point scale factor at the site is 0.99968; the remaining 4e-5 is not accounted for. Since
coordinator#716 the fit scale is 1 to 1e-8 (topic 4).

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

Code citations below are ODM v3.6.2 and the OpenSfM it builds (c5328439). Where a statement rests
only on what one run did, it says so.

**What ODM does with the fisheye before densifying.** From the code:

- *Undistortion keeps the centre of the fisheye.* For `fisheye` and `fisheye_opencv` cameras
  OpenSfM's undistort replaces the camera with a perspective one of the same focal length, width
  and height (`opensfm/undistort.py` L51-52, L338-345). For the sim's lens (2200 px, 4608 x 2592,
  r = f theta) that frame spans 92.6 x 61.0 deg of the 120 x 67.5 deg and holds 63.8% of the
  fisheye's pixels. OpenMVS and mvs-texturing both work from the undistorted images. No ODM option
  changes this.
- *The undistorted size comes from the target GSD.* `gsd.image_scale_factor` (`opendm/gsd.py`
  L49-68) takes the reconstruction's GSD, adds 50% for error, and returns the largest power of two
  not above GSD / target, capped at 1. By its method (`opensfm_reconstruction_average_gsd`, L102),
  recomputed by hand from `-free`, the median height above the nearby sparse points is 29.8 m and
  the GSD 1.26 cm, so 1.89 cm with the margin. At the default `--orthophoto-resolution 5` that gives
  0.5, and `-free` undistorted to 2304 px (`undistorted_image_max_size` in its
  `opensfm/config.yaml`). At 1.5 it gives 1, and the dense run logged 4608.
- *The depth-map size comes from `--pc-quality`.* `get_depthmap_resolution` (`opendm/utils.py`
  L20-44) takes the largest image side times 0.5 / 0.25 / 0.125 for ultra / high / medium. `high`
  gives 1152 px, and OpenMVS runs at `--resolution-level` round(log2(4608 / 1152)) = 2
  (`stages/openmvs.py` L57). Texture is drawn from the undistorted images, not the depth maps.
- *Texturing.* The 3D model is textured without nadir mode; the orthophoto's 2.5D mesh is textured
  with `--nadir_mode`, unless `--use-3dmesh`, which textures the orthophoto from the 3D mesh
  (`stages/mvstex.py` L33-47, L91-92).

**Flags.** The dense run used `--orthophoto-resolution 1.5 --pc-quality high --use-3dmesh
--mesh-size 2000000 --mesh-octree-depth 12`, after the second house run in `fables`
(`Datasets/odm-maps-bond_ave-2026-02-21-house-2/`). That run also used `--feature-quality ultra`
and `min_num_features` 30000; those belong to the OpenSfM stage and are not tried here. The house
runs once found `--auto-boundary` worse on every metric they scored
(`Datasets/experiments-house-model.md`).

**Reusing one run's OpenSfM in another.** From the code, and what one attempt each showed:

- ODM compares the options with the run directory's `options.json` and reruns from the earliest
  stage any changed option belongs to (`run.py` L37-41, `opendm/arghelpers.py` L63-76).
  `--pc-quality` belongs to the opensfm stage (`opendm/config.py` L71), which does the undistortion.
  So the first attempt, which changed it, reran features, matching and reconstruction from scratch
  over the seeded `opensfm/`; it was stopped 70 min in. Giving any of `--rerun`, `--rerun-from`
  or `--rerun-all` turns the comparison off (`arghelpers.py` L65).
- `opensfm/image_list.txt` holds absolute paths and is kept when it exists (`opendm/osfm.py`
  L139-140). The second attempt, with `--rerun-from openmvs`, failed in undistortion on the
  seed's paths into `-free/images/`. The third ran with them rewritten.
- With no `updated_config.txt` in the seed, ODM rewrites `undistorted_image_max_size` and
  undistorts again (`stages/run_opensfm.py` L86-93). The third attempt kept the reconstruction and
  undistorted at 4608.
- ODM deletes `openmvs/depthmaps/` when the openmvs stage reruns (`stages/openmvs.py` L29-31,
  L45-46), and after it succeeds only under `--optimize-disk-space` (L240-250).

**Densification, third attempt (`mapping-sim-process-cztbj`).** Undistortion and OpenMVS setup took
12 min. In `DensifyPointCloud`: depth-map estimation 48 min, then two geometric-consistency passes
of 15.7 min each, then filtering of about 3.5 min. Fusion reached 424 of 1272 depth maps (33%) in
44 s, and the container was OOM-killed at its 48 GiB limit. Its working set, sampled every 30 s,
went 12.4, 15.2, 22.0 and 32.2 GiB over the last 2 min. The kill fell between samples, so the peak
was not recorded. The run took 102 min and 20.5 core-hours, a mean of 12.1 cores
(`resources/mapping-sim-process-cztbj.md` in the run directory).

ODM retries a `DensifyPointCloud` that exits 137 with tiled fusion (`stages/openmvs.py` L115-118).
That did not happen here. The log ends mid-fusion and the whole container was killed. My
inference, not checked: the kernel killed every process in the container's memory group, not
only `DensifyPointCloud`.

ODM's tiled fusion is not the single pass split up. It cuts the scene with `--sub-scene-area
660000` and fuses each piece with `Optimize = 0`, `Estimation Geometric Iters = 0`, `Min Views
Filter = 1`, `--postprocess-dmaps 0` and `--geometric-iters 0`. Then it concatenates the pieces'
point clouds (`stages/openmvs.py` L127-200). How that changes the cloud is not measured.

**Fourth attempt (`mapping-sim-process-ft8v4`).** It had a 56 GiB limit, set in the workflow's
`podSpecPatch`, and `--rerun-from odm_filterpoints`, so the openmvs stage kept `depthmaps/`.
OpenMVS loaded the kept `.dmap` files and skipped estimation, then ran both geometric-consistency
passes and filtering on them again. From the OpenMVS source (ODM's fork, tag 355, which ODM 3.6.2
builds): an existing `.dmap` stands in for estimation only (`libs/MVS/SceneDensify.cpp` L1961).
Each geometric pass is recomputed and renamed over the `.dmap` (L1899-1900). So the kept files
were already the twice-refined and filtered maps, and this attempt refined and filtered them a
second time. Fusion reached 883 of 1272 depth maps (69%) in 92 s, and the container was
OOM-killed at its 56 GiB limit. The last sample before the kill was 54.3 GiB
(`resources/mapping-sim-process-ft8v4.md`). The third attempt's 1272 depth maps, its `scene.mvs`
and its `Densify.ini` were copied beforehand to `runs/map-test-flown-20261107d-dense-depthmaps/`.
To fuse those maps without reprocessing them, run `DensifyPointCloud` with `--geometric-iters 0
--postprocess-dmaps 0` (L1836, L1970-1978). ODM passes those flags only in its tiled path.

**What fusion holds in memory, from the source.** In tag 355, `FuseDepthMaps` (L1372-1597) loads
every depth map before it fuses any (L1395-1400). One map is depth, normal and confidence as
floats: 20 bytes a pixel, 14.9 MB at 1152 x 648, the size of each `.dmap`. 1272 of them are
19 GB. Each image also gets a 4-byte-per-pixel point index as fusion reaches it, up to 3.8 GB.
Each fused point has a position, colour and normal and three small heap lists (views, weights,
pixel coordinates), roughly 170 bytes by my count of the types. The code estimates the point count
at 15% of all depth pixels (L1405), 142 M here, about 24 GB. The fusion loop is single-threaded
(L1431-1559), so `--max-threads` does not change its memory. Estimation, which is threaded, ran
near 10 GB at 16 threads in the third attempt. Two attempts were killed, at 33% fused within
48 GiB and at 69% within 56 GiB. Neither gives the total a full fusion of these 1272 maps needs.

Upstream OpenMVS (cdcseacave/openMVS `develop`, 2026-11-06, 157 commits past tag 355) fuses
through a depth-map cache sized to free memory (`DMapCache`), not with every map resident. It
also has `--sub-scene-memory`, which splits the scene into sub-scenes that each fit a memory
budget. Whether its `.mvs` and `.dmap` formats read ODM's files is not checked. Neither is what
it changes in per-point storage. ODM's latest release is 3.6.2, and ODM master still builds tag 355.

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
| camera position error after the similarity fit, median / p90 / max | 0.72 / 1.15 / 1.79 mm | 0.84 / 1.3 / 2.0 mm |
| camera rotation error, median / max | 0.0019 / 0.0053 deg | 0.0019 / 0.0049 deg |
| point distance to true surface, median / p90 | 3.0 / 24 cm | 3.1 / 24 cm |
| similarity fit rotation | 0.652 deg | 0.652 deg, about vertical |
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

The first rig run's ODM ended in `compute_statistics` (`KeyError` in OpenSfM's
`stats.rig_statistics`, which tests a stale loop variable and fails on a rig camera not named after
a camera model), so ODM skipped its exports and the workflow skipped scoring; it was scored by
hand. Since coordinator#714 each rig camera is named after its camera model, which that function
skips.

**Observed on `map-test-flown-20261107d-rig` and `-free`**, ODM run one after the other on lancer
on one render (`renders/map-test-flown-20261107d`, coordinator `bfe8e04`, rasterised, priors in
WGS84 since coordinator#716). The rig files are from coordinator#719, with the origin at the mean
of the camera centres; the mean of the four camera centres in the rig frame of
`-rig/opensfm/rig_cameras.json` is zero to 1e-16 m.

| | free | rig |
|---|---|---|
| images reconstructed | 1272 / 1272 | 1272 / 1272 |
| similarity fit: scale, rotation | 1 - 3.0e-9, 0.00034 deg | 1 + 8.1e-9, 0.00034 deg |
| camera position error after the fit, median / p90 / max | 0.65 / 1.06 / 2.20 mm | 0.58 / 0.93 / 1.96 mm |
| camera rotation error, median / p90 / max | 0.0015 / 0.0024 / 0.0040 deg | 0.0010 / 0.0018 / 0.0027 deg |
| camera centres against the truth with no fit, in OpenSfM's topocentric frame: mean (E, N, U); median / max distance | 0 to 0.01 mm; 0.65 / 2.20 mm | 0 to 0.01 mm; 0.58 / 1.96 mm |
| sparse points; distance to true surface, median / p90 | 928,505; 2.9 / 23 cm | 928,182; 2.9 / 23 cm |
| detect, match, tracks (from the first `features/` write) | 15:42 to 16:02, 20 min | 14:33 to 14:53, 20 min |
| reconstruct (`tracks.csv` to `reconstruction.json`) | 50 min | 28 min |
| ODM pod wall | 88 min, to the end of the ODM CLI | 61 min, to the failure |
| ODM pod peak / mean memory, mean / peak cores, core-hours | 21.3 / 11.4 GiB, 3.2 / 13.1, 3.9 | not recorded |

The scores are `score.py` on each run's `opensfm/reconstruction.topocentric.json` (`-free`) or
`opensfm/reconstruction.json` (`-rig`), saved as `score-topocentric.json` in each run's directory.
The free run's resources are `-free/resources/mapping-sim-process-pqkpq.md`; it gives the ODM step
75 min of memory samples against the pod's 88 min, so Mimir has gaps over this run. The rig run's
resources step got HTTP 500 from the Prometheus query and wrote nothing. The ODM container logs
are saved as `odm-pod.log` in each run's directory.

- With the priors in the right frame, the free run's cameras are within 2.2 mm of the truth with no
  fit; the 0.6-1.1 m from its own priors on `map-test-flown-20261105-2105` does not recur. That
  run's priors were in the UTM grid frame (above); no run separates that from any other cause.
- The mounts the rig run ended with (`rig_cameras` in its `reconstruction.json`) equal the given
  ones to under 0.01 mm and 1e-5 deg. With 318 captures OpenSfM adjusts them against the given
  values as priors, and the given values are the true ones, so this run does not distinguish
  "held" from "adjusted and found nothing to move".
- Reconstruct took 28 min with the rig and 50 min without, the same ratio as on the earlier render
  (1797 s against 3366 s). The rest of the ODM run took the same time in both.

**The rig run cannot finish ODM.** With GPS priors ODM converts the topocentric reconstruction to
UTM after the statistics step: ODM 3.6.2
[`stages/run_opensfm.py` lines 72-75](https://github.com/OpenDroneMap/ODM/blob/v3.6.2/stages/run_opensfm.py#L72-L75)
runs `opensfm export_geocoords --reconstruction`, then moves `reconstruction.json` to
`reconstruction.topocentric.json` and the converted copy into its place. In ODM's OpenSfM
(`c5328439`),
[`opensfm/actions/export_geocoords.py` `_transform_reconstruction`, lines 130-142](https://github.com/OpenDroneMap/OpenSfM/blob/c5328439/opensfm/actions/export_geocoords.py#L130-L142)
sets each shot's pose directly (`shot.pose.set_rotation_matrix`, line 139). A shot in a rig
instance takes its pose from the instance and its rig camera, and setting it raises
`RuntimeError: Cannot set an immutable pose` (`-rig/odm-pod.log`, line 31196). So any rig run with
GPS priors stops there, before densification. The first rig run stopped earlier, in statistics,
and did not reach it. A step between reconstruct and the export that writes the rig's solved poses
out as plain shots would get past it; it is not built.

**Decision (2026-11-07): carry on without the rig.** A free run is needed in any case to check the
rig's assumptions on real data: that the calibration and the mounts are right, and that the
cameras do not move against each other in flight. The rig saves 22 of the free run's 88 min of
ODM. The step past `export_geocoords` is not being built for now.

**Camera-to-camera poses in the free run, capture by capture.** Each camera's pose in the ne
camera's frame, at each of the 318 captures of `-free`, against the same from the truth:

| | offset, median / p90 / max | rotation, median / p90 / max |
|---|---|---|
| nw against ne | 0.70 / 1.20 / 1.80 mm | 0.0013 / 0.0022 / 0.0037 deg |
| se against ne | 0.67 / 1.18 / 1.85 mm | 0.0014 / 0.0025 / 0.0038 deg |
| sw against ne | 0.76 / 1.30 / 1.98 mm | 0.0016 / 0.0028 / 0.0040 deg |

This is the free solve's spread with rigid mounts and perfect images: the floor that a mount
moving between captures has to exceed to show in a free run. In the free solve a moving camera's
own pose follows it, so it does not raise the reprojection error. A mount that is wrong but steady
shows as a constant offset from the given mounts, not as spread. On the vehicle each camera's
prior comes from one RTK antenna and that camera's nominal mount, so a camera that has moved by
1-2 cm is off its own prior by about the prior's accuracy, and by much less for millimetres. In a
rig solve the camera cannot follow, and the motion goes into the residuals. OpenSfM's
`stats/stats.json` gives mean reprojection error 0.719 px for `-free` and 0.728 px for `-rig`, with
the true mounts. The sim does not render motion within an exposure.

**Scoring.** Until tiles#959 the workflow's score step read `opensfm/reconstruction.json`, which
after a completed ODM run is ODM's UTM copy: `-free/score.json` gives a fit rotation of 0.619 deg
(UTM grid convergence at the reference point) and a 6.6 mm median position error. Since tiles#959 it
scores `reconstruction.topocentric.json`. The earlier free runs are rescored from theirs, in
`score-topocentric.json` in each run directory; the tables above and in topic 2 are those.

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

- Rig runs stop in ODM's `export_geocoords` (topic 4); not being worked on, by the decision there.
- The `tiles` `mapping-sim` WorkflowTemplates on the cluster are from tiles 0550c12, though Argo
  CD reports the app Synced at bc53d8a. They have no `imagePullPolicy` (tiles#953), so the
  `20261107d` runs set it per workflow with a `podSpecPatch`. They also score
  `opensfm/reconstruction.json`, ODM's UTM copy (tiles#959), so the dense run's `score.json`
  is of that copy.
- Fusion of the dense run's 1272 depth maps at 1152 px has not finished inside 48 or 56 GiB (topic 3).
- No scorer yet for the dense cloud against the true surfaces, or for the stars' resolved radius
  in the textured model (topic 3).
- The render step requests no GPU, so on lancer the rasteriser runs on Mesa's llvmpipe: 1272
  full-resolution images in 818 s, 0.64 s each (`renders/map-test-flown-20261107d/manifest.json`,
  `render_s`). On the Radeon it was about 0.3 s per image.
- OpenSfM's `stats.rig_statistics` and `export_geocoords` failures with a rig (topic 4) are not
  reported upstream.
- The match-depth section of `mapping-sim-features.ipynb` is on a local branch,
  `mapping-sim-match-depth`, not yet a PR.
- EKF-covariance noise on the reported poses (topic 6) is not started.
