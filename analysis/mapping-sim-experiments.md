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

**Nothing here is marked confirmed.** There is one completed run.

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
Not yet run.

### 3. Resolution and texture recovery

The house carries a Siemens star (36 sector pairs) on each wall and the roof. The radius at which a
reconstruction stops resolving the sectors reads off its resolution there. This needs
densification, so the ODM step has to run past OpenSfM. Until then the stars also give the sparse
stage sharp corners.

### 4. Rigid rig against free alignment

Solve once with the four cameras held at fixed relative poses (OpenSfM's rig model) and once with
every image free, then compare. At perfect data the two should agree. Two measures: the free
solution's camera-to-rig poses, as spread between captures and as distance from the true mounts;
and OpenSfM's run time per stage, since a rig model reduces the unknowns. How ODM 3.6.2 passes a
rig through is not yet checked.

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
