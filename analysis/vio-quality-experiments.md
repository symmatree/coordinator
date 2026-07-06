# Experiments: VIO quality (rekon10 VINS-Fusion)

*Single place to track theories, evidence, and experiments for **why the regenerated VINS
pose diverges from the FC EKF/GPS**, and whether the estimator can produce a usable trajectory
from this vehicle's data at all. Modeled on `fables/Datasets/experiments-house-model.md`.*

**Origin:** coordinator [#42](https://github.com/symmatree/coordinator/issues/42) (bench capture,
vibration question), [#35](https://github.com/symmatree/coordinator/issues/35) (replay harness).

**Principal docs (feed these in):**
- **`vins_fusion_offline`** (#54) — the deterministic offline harness: `containers/vio-estimator/overlay/VINS-Fusion/vins_estimator/src/main_offline.cpp`, built via `build-local.sh`. The trustworthy estimator.
- `docs/vio-offline-replay.md` — how pose is regenerated offline; the known failure mode.
- `analysis/vio-ekf-comparison.ipynb` + `analysis/vio_ekf_compare.py` — output side: align VINS pose to the FC EKF, ATE + scale.
- `analysis/vio-input-alignment.ipynb` — input side: OAK-D IMU vs FC IMU, vibration PSD, feature health.
- Upstream source (pinned): `chobitsfan/VINS-Fusion@c525184` `vins_estimator/src/estimator/estimator.cpp` (solver), `.../main.cpp` (socket loop); `chobitsfan/oak_d_vins_cpp@378f40f` `feature_tracker.cpp` (IMU/feature packing).
- Seed calibration: `host/ansible/roles/coordinator/files/oak_d.yaml`.

---

## What "working" means (levels)

1. **Produces plausible, non-diverging pose** from real inputs (doesn't run away to km). *Not yet.*
2. **Tracks the FC EKF/GPS within a few metres** over a flight. *Not yet.*
3. **Metrically accurate** (scale ≈ 1, drift bounded) good enough to feed the FC in flight. *Goal.*

The distinction that actually matters right now (per the plan): **can the algorithm+calibration
produce a good trajectory from this data at all** — a feasibility question — separately from whether
we can reproduce that in real time on the Pi. Prove feasibility first; then solve real-time.

---

## Data

Two matched captures, 2026-07-05 (coordinator #42), on the NAS under `datasets/flights/rekon10/`:

| run | dir | motors | role |
|-----|-----|--------|------|
| **handheld** | `260705-handheld-noarm/` | **off** | vibration-free control |
| **armed** | `260705-vio-logged/` | **on**, hard-mounted OAK-D | vibration treatment |

Each has: `wave-*.feat` (+ `.feat.json`) estimator-input fixture, the FC `.bin` (1980-dated), tlog/rlog.

> **Trust status.** The early evidence (E1–E8) was regenerated under **qemu with `max_solver_time: 0.04`
> and real-time-ish pacing** — non-deterministic and solver-starved. **That's now superseded by the
> deterministic offline harness (X3 / #54, evidence E9)**, which is bit-reproducible and un-starved and
> reproduces the same divergence — so the *divergence itself* is confirmed real. Fine-grained qemu numbers
> (exact ATE, scale) should still be re-derived on the harness before quoting; conclusions marked **[robust]**
> held regardless (common-mode across a controlled pair).

> **⚠ We have never observed live onboard behavior.** VIO was never fed to the FC (no serial link),
> and no one has watched the live pose output in flight. *Every* observation here is from **offline
> replay**. So even the foundational fact — does the estimator spool up / produce pose at all when
> running live on the vehicle — is **unconfirmed**. (Expected to, being open-loop with no controls or
> filtering to damp it — but a bet, not a measurement.)

---

## Theories

Each theory: statement, evidence **for**, evidence **against**, status, and the experiment that
would discriminate it. Evidence IDs (E#) are in the ledger below.

### T1 — Solver-iteration starvation under qemu — **DISPROVEN**
*Under qemu, the wall-clock `max_solver_time: 0.04 s` budget lets Ceres run far fewer than the
configured 8 iterations, degrading the estimate.*
- **For:** E6 (the budget is a real wall-clock cap, `estimator.cpp:1083`; qemu is ~10× slower per iteration).
- **Against:** **E9** — the deterministic offline harness (#54) runs Ceres to the full iteration count with **no** wall-clock cap, and reproduces the **same ~41 km divergence**. Un-starving the solver changed nothing.
- **Status:** **DISPROVEN.** X3 ran; the divergence is not solver starvation.

### T2 — The 0.04 s budget also binds on the real Pi in flight
*Even in production the Pi may not finish 8 iterations in 40 ms, so the deployed estimator is itself
under-solved — i.e. this isn't only a qemu artifact.*
- **For:** the tuning exists (`max_solver_time` set low deliberately) implying real-time pressure on the Pi.
- **Against:** none yet.
- **Status:** unmeasured and important — determines whether "fix the replay" is even enough.
- **Discriminator:** X4 (instrument solver time / iteration count on the Pi in a real flight).

### T3 — Motor vibration corrupts the OAK-D IMU, degrading pose
*The hard-mounted OAK-D IMU eats motor vibration, poisoning IMU preintegration.*
- **For:** E1 (OAK-D accel band-power >5 Hz is ~400–500× higher armed vs handheld — the camera IMU *does* see the vibration).
- **Against:** E2 **[robust]** (the **handheld** run, with motors off and no vibration, **also winds up** — so vibration is *not necessary* for the divergence). E4 (the IMU is likely BNO085 fused output, which is filtered and may not even faithfully carry the vibration into the estimate).
- **Status:** **weakened as the primary cause.** The vibration-free control diverging is the strongest single fact we have, and it's robust to the qemu confound.
- **Discriminator:** X2 (serious handheld-vs-armed control analysis on a clean run).

### T4 — Cam↔IMU calibration / extrinsics are wrong
*The seed `oak_d.yaml` uses identity `body_T_cam` extrinsics and un-refined IMU noise; a wrong
lever-arm / orientation makes the fused estimate drift once accelerating.*
- **For:** E3 (VINS over-scales and drifts from just after takeoff; velocity does not return to zero at hover — the classic signature of a mis-estimated bias / gravity / scale). Happens on **both** runs. E9 (the divergence is fundamental, not a harness artifact — so a fundamental cause like this is now where the probability mass sits).
- **Against:** none yet.
- **Status:** **leading candidate** — with T1/T7 (harness artifacts) disproven, the fault is fundamental, and this is the strongest fundamental theory.
- **Discriminator:** X1 (vision-only removes the IMU/extrinsic path entirely) + X8(b) (true-vs-seed extrinsic) + a real calibration.

### T5 — The OAK-D IMU (BNO085 fused output) is intrinsically unsuitable for tight VIO
*Fused/filtered IMU output (not raw), zero-at-rest, wrong noise model — VINS wants raw high-rate
accel/gyro.*
- **For:** E4 (gyro reads **exactly** 0.000 at rest in both runs — a fused/filtered signature, consistent with BNO085). E3 (velocity won't settle at rest).
- **Against:** none yet.
- **Status:** plausible; also blocks faithful vibration capture (ties to the feature-block roadmap: raw high-rate IMU).
- **Discriminator:** X7 (capture raw high-rate OAK IMU and re-run) + X1 (vision-only sidesteps the IMU).

### T6 — Aggressive motion / feature geometry breaks tracking
*Motion blur, feature loss, or insufficient parallax during aggressive motion starves the visual constraint.*
- **For:** E7 (handheld feature count collapses to ~3/frame during the barrel roll).
- **Against:** E7 also shows the **armed** run diverges while feature counts stay healthy (mean ~30, 0.3% <10) — so feature starvation is not the mechanism for armed.
- **Status:** contributes to the *handheld barrel-roll* endgame only; not the general explanation.
- **Discriminator:** X2 (analyse pre-maneuver windows) + X1 (vision-only under the same motion).

### T7 — Our *replay* is the problem, not the estimator (I/O-timing artifact) — **DISPROVEN (as the cause)**
*The socket loop reads one IMU + one feature per `poll()`, so which measurements are batched — and the
init window — depend on arrival timing. Our replay isn't a faithful or repeatable feed.*
- **For:** E5 (VINS init `R0` **changed with replay pace** — delivery timing leaks into the estimate). E6 (real-time coupling via the wall-clock solver budget).
- **Against:** **E9** — a deterministic, timestamp-ordered offline feed (#54) still diverges **identically**. E8 (the early trajectory tracks the EKF shape — not garbage-in).
- **Status:** **DISPROVEN as the cause of the divergence.** The timing coupling is real (E5), but removing it entirely (X3) leaves the divergence unchanged — so it wasn't what breaks the pose. (The deterministic harness is still the right foundation for trustworthy numbers.)

---

## Evidence ledger

| ID | Evidence | Source | Bears on |
|----|----------|--------|----------|
| E1 | OAK-D accel band-power (>5 Hz) ~400–500× higher armed vs handheld | `vio-input-alignment.ipynb` §3 | +T3 (IMU sees vibration) |
| E2 | Handheld (no vibration) **also** winds up / diverges | `vio-ekf-comparison` handheld run | **−T3** [robust] |
| E3 | VINS over-scales ~from takeoff; velocity doesn't return to 0 at hover | `vio-ekf-comparison.ipynb` §2 | +T4, +T5 |
| E4 | Gyro reads **exactly** 0.000 at rest (both runs) | fixture decode | +T5 (BNO085 fused); −T3 |
| E5 | Init `R0` changed with replay pace | fulliter smoke vs 0.9× run | +T7 |
| E6 | `max_solver_time` is a wall-clock Ceres cap (0.04 s); qemu ~10× slower | `estimator.cpp:1083` | +T1, +T2, +T7 |
| E7 | Handheld feats → ~3/frame at barrel roll; armed diverges with healthy feats | `vio-input-alignment.ipynb` §4 | +T6 (handheld), −T6 (armed) |
| E8 | Early trajectory tracks EKF shape; time-align NCC 0.93 (armed), confirmed by motor-spinup/climb | `vio-ekf-comparison.ipynb` §1–2 | −"fundamentally broken", −T7-as-total |
| **E9** | **Deterministic offline harness** (native x86, full Ceres iterations, `num_threads=1`, timestamp-ordered feed) reproduces the **same ~41 km divergence** (qemu was 55 km, same shape) and is **bit-reproducible** across runs | `vins_fusion_offline` (#54) | **−T1, −T7** (the divergence is real, not a harness/replay artifact); ⇒ +T4/T5 |

*E1–E8 output-side evidence was from qemu/0.04 s runs (preliminary). **E9 supersedes them for the divergence question** — same result on a trustworthy, deterministic estimator — so the divergence itself is no longer in doubt.*

---

## Experiments (next steps)

Ordered by value ÷ cost. **X3 (the trustworthy estimator) is now done (#54)** — X1/X2/X8 run on it.

- [ ] **X1 — Vision-only (stereo, `USE_IMU: 0`).** Run VINS stereo-only on the same features; optionally a GPS-anchored stereo bundle adjustment over the feature tracks (start/end stationary GPS as anchors). **The key discriminator:** if vision alone tracks, the fault is the IMU/extrinsic path (→T4/T5); if vision alone also fails, the feature/camera data itself is the ceiling. Config-level first cut; runnable without the full harness. *(This is Seth's SfM/loop-closure idea; the cheap proxy is `USE_IMU:0`, the rigorous version is the custom GPS-anchored BA.)*
- [ ] **X8 — Synthetic VI ablation (known-world simulator).** Generate the IMU + stereo-feature measurements a sensor suite *would* produce along a known truth trajectory — the FC **EKF** state (position/velocity/attitude) — feed them to the real estimator, and compare the output back to that truth. **Closed loop, so the EKF's absolute accuracy doesn't matter** (we recover the trajectory we generated from). Inverts the problem: start clean, add degradations, find what breaks it.
  - **(a) Perfect world first** — clean features from well-conditioned invented landmarks + ideal IMU. If the estimator can't recover a *clean* world, the ceiling is the **estimator/config/calibration** itself. Cheapest, most decisive first cut (+T4, −input-quality).
  - **(b) Calibration test** — generate with the *true* cam↔IMU extrinsic, run with the *seed identity* extrinsic; if that alone reproduces the over-scale/drift (E3), **T4 confirmed** — and shows whether online `estimate_extrinsic` recovers from a bad seed.
  - **(c) Degrade one axis at a time** — inject the measured vibration spectrum into the IMU (→T3), model BNO085 fusion/zero-at-rest (→T5), add feature noise/dropout (→T6). Compare each **breaking threshold** to the measured real-data characteristics: if real vibration is *below* the threshold, it contributes but isn't the trigger (could resolve the E1-vs-E2 tension).
  - **Caveats:** tests the **estimator only**, not the real tracker (we can't easily test feature extraction); the scene/landmark model is an invented knob; run on the **offline harness (X3)** for clean results; derive the synthetic IMU from EKF **velocity/attitude** (not double-differenced position), lightly smoothed, so it stays dynamically consistent with the features. Real work — a small VI simulator (anticipated in #35).
- [ ] **X2 — Serious handheld-vs-armed control analysis.** Handheld is the vibration-free control; its behaviour directly tests T3. **The winding-up conclusion is [robust]** to the qemu confound because the contamination is common-mode across the pair — if handheld winds up without vibration, vibration isn't necessary. Do this properly (per-axis, pre-maneuver window) instead of hand-waving "maybe vibration."
- [x] **X3 — Deterministic offline harness — DONE (#54).** `vins_fusion_offline`: reads the fixture directly, feeds IMU+features in `t_mono` order synchronously, drops the wall-clock solver cap, single-threaded Ceres → **bit-reproducible** output; built native x86 via `containers/vio-estimator/build-local.sh`. **Result:** the divergence is **unchanged** from the qemu runs → **T1 and T7 disproven** (E9), the divergence is real. This is now the trustworthy base for X1/X8, and its pose is **sensor-time-keyed** (aligns to the FC directly, no cross-correlation).
- [ ] **X4 — Measure solver time / iteration count on the real Pi in flight.** Directly tests T2 (does the 0.04 s budget bind in production?). Determines whether fixing the replay is sufficient or the deployed estimator is itself under-solved.
- [ ] **X5 — FC-IMU substitution (redesign; not on current data).** Feed the FC IMU (better vibration damping) in place of the OAK-D IMU to test T3. **Confounded on existing data:** the FC IMU is logged at only ~25 Hz (batch off) — too sparse for preintegration — and the cam↔IMU extrinsic is calibrated for the OAK-D location, not the FC. Needs a purpose-built capture (high-rate FC IMU logging + a measured FC-IMU→camera extrinsic). Until then, **X2's motors-off control is the cleaner "less vibration" test.**
- [ ] **X7 — Capture raw high-rate OAK-D IMU (vs BNO085 fused).** Tests T5 and unblocks faithful vibration capture. Ties to the tracker/feature-block roadmap (raw IMU mode, higher rate).

---

## Conclusions (distilled so far)

1. **The divergence is REAL — not a harness / replay artifact.** The deterministic, un-starved offline harness (X3, #54) reproduces the same runaway (E9), so **T1 (qemu starvation) and T7 (replay timing) are disproven.** The "is our replay faithful?" question is *closed*, and the numbers are now trustworthy and reproducible.
2. **The estimator initialises and tracks the takeoff**, then diverges (E8) — a degradation/drift, not garbage-out, and it happens the same way on a clean estimator.
3. **Vibration is not the necessary cause** — the motors-off control diverges too (E2, [robust]). Stop treating "maybe it's vibration" as unfalsifiable.
4. **The fault is fundamental — calibration / IMU (T4/T5) is the leading candidate.** Over-scale from takeoff and velocity that won't zero at rest are bias/gravity/scale signatures on both runs. Next: X1 (vision-only) splits IMU/calibration from feature/camera data; X8(b) (true-vs-seed extrinsic on the harness) tests T4 directly.

---

## Methodology confounds

- **qemu solver starvation (T1) — RESOLVED** by the offline harness (X3, #54): full iterations, no wall-clock cap.
- **Non-determinism / I/O timing (T7) — RESOLVED** by the offline harness: file-fed in `t_mono` order, single-threaded Ceres → bit-reproducible. (You cannot get time-invariant replay from real-time-clock code without controlling time — so we forked the input loop and thread count.)
- **Provenance — still open.** The old `*.vinspose.csv` on the NAS have no sidecar (fixture sha, image digest, config). Don't extend analysis on those; the offline harness (X3) is now the source of trustworthy pose — when we persist its output, give it a provenance sidecar (fixture sha256, estimator image digest, config).
