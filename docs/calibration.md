# Calibration & the capture chain

How camera/IMU calibration is produced, validated, stored, and kept honest over time. This is a **working plan**, not a frozen spec; claims about ArduPilot params and ODM behavior are inference until bench-confirmed.

The governing idea: **calibration is a deliberate, evaluated, off-vehicle process — never an accidental byproduct of a flight, and never frozen-and-forgotten.** On-vehicle disk is not stable storage for data with quality implications. The vehicle is a **read-only consumer** of blessed calibration; the system-of-record lives in version control (small artifacts) and the NAS datasets share (bulk run outputs).

## Calibration artifacts (different things, different keys)

Calibration is not one number. The artifacts belong to different physical objects, change independently, and so are **keyed differently**. Conflating them is the main error to avoid.

| Artifact | Belongs to → key | Source | Validated against |
|----------|------------------|--------|-------------------|
| OAK-D cam↔IMU extrinsics + time offset (`body_T_cam0/1`, `td`) | the OAK-D **unit** → MxId/serial | VINS online estimate during flight | GPS/RTK trajectory in the `.bin` |
| OAK-D↔FC mount (`VISO_POS_*`, `VISO_ORIENT`) | the **airframe** | hand-measure → EKF refine | EKF vision innovations |
| Pi Zero camera intrinsics | each **Zero** → serial | ODM self-calibration (feedback loop) | reprojection residuals |
| Zero↔Zero / Zero↔OAK-D rig extrinsics | the **camera rig / airframe** | ODM bundle adjustment | reprojection + cross-dataset rig consistency |

Two consequences that fall out of the keying:

- **VINS works in the OAK-D IMU frame and stays there.** We do not re-express the VIO estimate at the FC IMU ourselves; ArduPilot accounts for the OAK-D→FC offset via `VISO_POS_*`/`VISO_ORIENT`, and `coordinator-mavlink` only does the fixed ENU/FLU→NED/FRD axis conversion (not a calibration). cam↔IMU travels with the camera; the mount travels with the frame — swap an OAK-D and the mount is unchanged; remount the same camera and the cam↔IMU is unchanged.
- **The Zero rig is the same shape, sourced from ODM instead of flight logs.** Intrinsics are per-Zero; the inter-camera geometry is per-rig. They come out of an ODM bundle adjustment rather than a VINS estimate, but they get **treated identically**: deliberate capture, evaluate, propose-not-bless, keyed storage, continuous re-check.

## Sources: two pipelines, one treatment

- **Flight-log / VINS pipeline** (OAK-D cam↔IMU). Fly **GPS/RTK-primary** with VINS running in parallel. The `.bin` already holds an independent reference trajectory (EKF/GPS fused pose), so the VINS-vs-GPS residual is the objective: minimize it over extrinsics to *propose* a calibration; evaluate it with the blessed extrinsics to *confirm* one. **Any flight that passes the GPS-quality gate is a calibration-validation sample** — drift trends per serial across flights instead of surprising us. Quality of the cross-reference depends on time alignment between the FC log clock and the VINS clock → **chrony+PPS (#11) is a precondition**, not unrelated host plumbing.
- **ODM pipeline** (Zero intrinsics, rig extrinsics). The mapping bundle adjustment self-calibrates intrinsics and solves camera poses; the rig constraint (fixed relative geometry) is extracted from those. **Even once we "bake in" a fixed rig for ODM, a parallel alignment check keeps running** — baking-in is an optimization, not a reason to stop verifying the rig hasn't shifted (knock, thermal, vibration creep). Same standing-experiment posture as the flight-log case.

## Propose ≠ bless (observation/inference separation)

Whatever the source, the analysis step **proposes**; it never writes the blessed artifact:

- The notebook/ODM pass emits a verdict — `confirm` (new estimate within tolerance of the blessed value for that serial/rig) or `propose-update` (new values + convergence/residual metrics + **regression delta vs the prior blessed artifact**; a large delta flags damage, not improvement).
- **Blessing is a separate authoritative act:** a human/agent commits the proposed values to the keyed path in this repo, which Ansible then deploys. An analysis auto-writing the blessed config would collapse inference into observation — the failure the whole design exists to prevent.

## Storage split

- **NAS datasets share** (`flights/<id>/`, `mapping-missions/<id>/`, …): bulk run outputs — `.bin` logs, VINS pose logs, captured images, rendered analysis PDFs, manifests. Git is for source and intent; the datasets share is for run outputs ([facts notebook-analysis design-intent §5](https://github.com/symmatree/fables)).
- **This repo** (`calibration/`): the small blessed artifacts + provenance, keyed by identity:
  ```
  calibration/oak-d/<MxId>/{oak_d.yaml, provenance.yaml}      # cam↔IMU, frozen estimate_extrinsic: 0
  calibration/zero/<serial>/{intrinsics.yaml, provenance.yaml}
  calibration/rig/<rig-id>/{rig-extrinsics.yaml, provenance.yaml}
  calibration/airframe/<frame-id>/{viso_mount.yaml, ...}      # OAK-D↔FC mount
  ```
  `provenance.yaml` records serial/rig-id, date, depthai/tracker/estimator (or ODM) versions, the flight/mission id, eval metrics, operator. Ansible seeds the identity-matched blessed file onto the matching device.

## Post-flight recovery step

Multi-source, because artifacts originate on different devices; the coordinator is the hub (FC link + VINS + `br0` bridge to the Zeros). Three layers:

1. **Flight identity** assigned at capture: `flights/<YYMMDD-NN>/` is the subject root (browse by subject; timestamp is a field). It is the join key across all sources.
2. **In-flight recording** (prerequisite): the VINS pose **time-series** must be logged during flight (not just final extrinsics) against the PPS-disciplined clock. Only one consumer can bind the `chobits_server` dgram socket, so the pose consumer logs: `coordinator-mavlink` when it forwards (flight), or a standalone recorder when VINS is not driving the FC (GPS-primary/calibration). Capture `extrinsic_parameter.csv` too; optionally the raw IMU+feature streams (enables offline reprocessing with a better estimator — at a real data cost).
3. **Post-flight pull** (`coord flight pull <id>`, operator-triggered post-disarm; automate later) gathers into `flights/<id>/`:
   - `.bin` from the FC — MAVLink log download over the link (automatable, slow) or SD-card pull (fast, manual fallback)
   - VINS logs from the coordinator's own disk
   - images from the OAK-D / Zero pods — over `br0` (#12) or **card-pull**; card-pull is fine **only as a declared step**, so the bundle is never silently incomplete (the notebook must distinguish "no images captured" from "images not recovered yet")

## Evaluation = a flight-analysis notebook

The calibration eval is not a bespoke subsystem — it is one more **flight-analysis notebook** in the [facts notebook-analysis framework](https://github.com/symmatree/fables), living in this repo (domain-specific):

- **Reconstructable** (immutable `.bin` + versioned notebook ⇒ output is a GC-able cache), **`alongside-input`** placement (`flights/<id>/calibration-eval.pdf`).
- **Assumption-gated**: vibration (`VIBE < 30`, zero clipping), GPS quality (`GPS.Status >= 5`), ESC oscillation, log completeness. A calibration from a bad-quality log is **"unassessable, not false"** — it halts before emitting a verdict instead of blessing garbage. This is what makes "deliberate, validated" fall out for free.
- Reads the `flights/<id>/` bundle, scores VINS-vs-GPS (or, for the rig, ODM residuals), emits confirm/propose with metrics.

## Cross-dependencies

- **#11 chrony+PPS** — time alignment for VINS↔GPS cross-validation; precondition for trustworthy residuals.
- **#10 coordinator-mavlink** — the pose consumer that logs the VINS stream in flight.
- **#12 Pi Zero `br0`** — network path to pull Zero images (vs card-pull).
- **`feature_tracker` MxId** — must emit the OAK-D serial so bundles/artifacts key correctly (today it prints "Device name" but not the MxId).

## Open decisions

- `.bin` recovery default: MAVLink download (automatable, slow) vs card-pull (fast, manual).
- Recover raw VINS inputs (enables reprocessing) or just pose + extrinsics (less data)?
- Recovery trigger: explicit `coord flight pull` vs auto-on-disarm.
- Zero images: `br0`-pull vs card-pull as the primary path (gated on #12).
- Notebook source confirmed in `coordinator`; one notebook spanning flight-log + ODM cases, or per-source variants?

## Related docs

- [vio-integration.md](vio-integration.md) — IPC layout, `oak_d.yaml` mount, the VINS binary plan
- [ardupilot-vio.md](ardupilot-vio.md) — `VISO_*` params, EKF source lanes (exploratory)
- [bench-estimator.md](bench-estimator.md) — the seed `oak_d.yaml` and online-refinement note
- [architecture.md](architecture.md) — compose profiles, ipc volume, host responsibilities
