# What VINS-Fusion solves in stereo-only mode (`USE_IMU=0`)

Reference for the coordinator VIO work (#42). Source analysis of the pinned estimator
**`chobitsfan/VINS-Fusion@c525184`**; all line numbers are against that checkout
(`vins_estimator/src/...`). The coordinator overlay changes only I/O (`main_offline.cpp`)
and one `num_threads` line -- **not** the factors or optimization -- so this analysis of
upstream applies unchanged. Generated from a source read (2026-07-08); spot-check line
numbers if the pin moves.

**One-line answer to "is stereo-only just optical flow dressed up as absolutes?"** No. It
is a **metric, sliding-window bundle adjustment** -- Ceres jointly optimizes *structure*
(per-feature inverse depths) and *motion* (keyframe poses), with metric scale fixed by the
known stereo baseline. It is *absolute in scale, relative in datum*: metric locally, but its
origin/heading float and slowly drift because there is no loop closure or global anchor.

## What a feature observation contains

`FeatureManager::FeaturePerFrame` (`feature_manager.h:26-56`) per feature per frame:
`point` (normalized ray `(x,y,1)`, unit-free, **no depth**, `:31-33`), `uv` (pixels,
`:34-35`), `velocity` (image-plane velocity for `td` compensation, `:36-37`), and the
right-camera match `pointRight/uvRight/velocityRight` + `is_stereo` set by
`rightObservation()` (`:41-56`). Depth is not in an observation -- it is solved for.

## The Ceres problem (`optimization()`, `estimator.cpp:964`; problem built `:969`)

| Factor | AddResidualBlock | present when `USE_IMU==0`? |
|---|---|---|
| Marginalization prior | `:1009` (guard `:1005`) | **yes** |
| IMU factor | `:1019-1020` inside `if(USE_IMU)` `:1012` | **no** (skipped) |
| Temporal, one camera (`ProjectionTwoFrameOneCamFactor`) | `:1046` | **yes** (core between-frame link) |
| Temporal, cross camera (`ProjectionTwoFrameTwoCamFactor`) | `:1056` (guard `:1049`) | **yes** |
| Same-frame stereo (`ProjectionOneFrameTwoCamFactor`) | `:1062` (guard `:1058`) | **yes** |

So stereo-only runs **marginalization prior + three projection-factor families**; only the
IMU preintegration factor is dropped. A feature must be seen in >=4 frames to be optimized
(`:1029-1030`). Solver: `DENSE_SCHUR` + `DOGLEG`, iteration cap + `SOLVER_TIME` budget
(`:1073-1085`), solved `:1088`.

## Where "metric" comes from -- the baseline, not the IMU

`ProjectionOneFrameTwoCamFactor` (`factor/projectionOneFrameTwoCamFactor.cpp:42`) is the
same-timestamp left->right reprojection. It back-projects the left feature at the current
inverse depth (`:60`), lifts to body via the **left** extrinsic (`:61`), notes both cameras
share the body pose so `pts_imu_j = pts_imu_i` (`:62`, **no frame pose appears**), projects
into the **right** camera via the right extrinsic (`:63`), residual = predicted vs observed
right point (`:69-70`). The only free quantity is `inv_dep_i`; the extrinsics
(`TIC[0]`/`TIC[1]`) encode the physical **baseline in metres**, so Ceres must pick the
*metric* depth that lands on the observed right pixel given a known-length baseline. That is
triangulation: known baseline + disparity -> absolute distance.

Contrast `ProjectionTwoFrameOneCamFactor` (`:43`): parameters are two *frame* poses; depth
and inter-frame translation multiply each other, so it alone is **scale-ambiguous** (it
constrains parallax/shape, not scale). Scale is pinned only by the stereo factors.

## Optimized state (parameter blocks)

Packed by `vector2double()` (`:770-825`), unpacked `double2vector()` (`:827-913`):
- `para_Pose[i]` -- per-frame body pose, added `:977-978`, always optimized.
- `para_Feature[i]` -- inverse depth per feature (`:820-822`); enters implicitly via the
  projection factors; the "structure" half.
- `para_Ex_Pose[c]` -- cam->body extrinsics, added `:987-988`.
- `para_Td[0]` -- time offset, added `:1000`.
- `para_SpeedBias[i]` -- velocity + biases, **only `if(USE_IMU)`** (`:979-980`). **Absent in
  stereo-only** -- which is why the logged `vx/vy/vz` are identically zero.

Fixing rules with `USE_IMU=0`:
- **Gauge fix:** `SetParameterBlockConstant(para_Pose[0])` (`:982-983`) -- no gravity/velocity
  to anchor the window, so frame 0 is held to remove the 6-DoF gauge freedom.
- **Extrinsics + `td` are forced constant:** `ESTIMATE_EXTRINSIC`/`ESTIMATE_TD` are forced to
  0 at load without IMU (`parameters.cpp:135-139`), and only written back `if(USE_IMU)`
  (`:891-911`). This keeps the stereo baseline a rigid known constant.

Net optimized state: **all window poses (frame 0 fixed) + all feature inverse depths.**

## No IMU: what links frames, and how the window slides

No preintegration is added (the `IMUFactor`/`IntegrationBase` construction is entirely inside
`if(USE_IMU)`, `:1012-1022`). Consecutive frames are linked by the **temporal projection
factors** on co-visible features (`:1046/1056`): the same 3D point seen from two poses must
reproject consistently -- rigidity of structure across frames *is* the motion constraint.

Keyframe/marginalization decision `addFeatureCheckParallax` (`:380-389`) sets `MARGIN_OLD`
(enough parallax -> drop oldest) or `MARGIN_SECOND_NEW`. `MARGIN_OLD` (`:1100-1216`) folds the
old prior + every frame-0-anchored projection factor into a new `MarginalizationInfo`, then
`marginalize()` (`:1194`) Schur-complements frame 0 into a dense linearized prior over the
remaining blocks. So the between-frame prior that IMU would otherwise provide comes instead
from the **marginalization factor** -- the accumulated, linearized memory of dropped visual
constraints.

## Initialization -- where metric scale enters without IMU

Stereo-only skips monocular SFM. In `processImage`, `INITIAL` dispatches to the
`if(STEREO && !USE_IMU)` block (`:474-488`): `initFramePoseByPnP` (`:476`), stereo
`triangulate` (`:477`), `optimization()` (`:478`), and at window-fill flip to `NON_LINEAR`
(`:480-484`). Metric scale enters in `FeatureManager::triangulate` stereo branch
(`:309-346`): it builds left/right camera projections separated by the physical baseline
`TIC[1]-TIC[0]` (`:312-323`), DLT-triangulates (`:328-338`), and reads metric depth off
`localPoint.z()`. `initFramePoseByPnP` then reconstructs metric 3D points as
`point*estimated_depth` (`:273-274`) and solves PnP (`:289`), propagating the baseline scale
into every pose. **Scale is never estimated -- it is imported from the fixed baseline `TIC` at
every triangulation.**

## Is the pose "absolute", and why does it drift?

Metrically absolute in *scale and local geometry*, but not in *global position/heading*, and
it drifts, mechanically:
- The world origin is arbitrary: frame 0 fixed at wherever it started (`:982-983`); no gravity,
  GPS, or map reference.
- Each slide Schur-complements the oldest frame into a **fixed linearized** Gaussian prior
  (`marginalize()`, `:1194`) -- locally correct, but small errors are baked in and never
  revisited; over many slides these accumulate (the textbook source of VO drift).
- **No loop closure / global reference in this estimator** (relocalization is a separate node),
  so nothing re-pins absolute position.

**Implication for us:** stereo-only is a genuine metric mini-SLAM back-end (this is why our
vision-only run recovers metric scale ~0.9, the ~10% being baseline/calibration error), and
the drift comes from lossy incremental marginalization with no global anchor. That is exactly
the gap an **offline GPS-anchored batch solve** ([#59](https://github.com/symmatree/coordinator/issues/59))
closes: it replaces the incremental marginalization with the full window at once and adds the
global datum this estimator structurally lacks.
