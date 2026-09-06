# Experiments: VIO quality (rekon10 VINS-Fusion)

*Single place to track **whether the OAK-D + VINS-Fusion estimator can produce a usable trajectory from
this vehicle's data**, and under what configuration.*

**How this doc works.** It is a **map of what we believe and how sure we are** — not a project plan, and
not a linear write-up. Three kinds of node, and how they relate:

- **Theories (`T#`)** are the spine: claims about the system, each carrying its current status/certainty,
  the **evidence for and against** it, and the **probes that would move it** (its discriminators). You
  decide how hard to push a theory by reading *that theory* — nothing here commits us to running anything.
- **Evidence (`E#`)** are observations, each with **provenance**: the run and the *execution
  configuration* that produced it. That description is always incomplete — there is usually a factor we
  have not named (see Methodology confounds) — so evidence is dated, sourced, and open to re-weighting,
  never final. The ledger is the **registry** of these; theories cite them by ID.
- **Probes (`X#`)** are candidate ways to learn more, and they **live under the theory they inform** —
  there is no separate roster. Each is **uncommitted**: a backlog you prioritize by reading that theory,
  not a program to run end-to-end. A completed probe (`[x]`) is *not* a checkbox that "settled" anything —
  its finding is recorded where it argues (under the theory, cited in the ledger); the `X#` stays as an
  audit pointer from probe → result. A probe that discriminates several theories lives under its **primary
  owner** and is referenced by ID from the others.

Two disciplines run throughout. **A single run rarely settles anything** — collect *directional
indicators* and resist premature certainty; the theses here have already inverted more than once and will
again. And **anchor to the real system** — a result whose input *and* expected output are both authored by
us (synthetic / sim-only) tests our wiring, not the vehicle. *(Structure generalizes the house-experiments
model, `fables/Datasets/experiments-house-model.md`, from a map to an estimator.)*

**Origin:** coordinator [#42](https://github.com/symmatree/coordinator/issues/42) (bench capture,
vibration question), [#35](https://github.com/symmatree/coordinator/issues/35) (replay harness).

**Reframed objective (2026-07-07).** The operational need is a **GPS-denied fallback** (multipath /
canopy), where the scene is **near-field** and the goal is **local stability** — hold a hover, or hop
10s of metres "ice-hole to ice-hole" — with graceful **"in doubt, land"** behaviour. The threat to
design against is a **sudden lateral jump into an obstacle**, *not* global position error. So "VIO
quality" decomposes, in priority order: (1) bounded local drift for hover + short hops; (2) a
trustworthy **health/confidence signal** → hold/land (fail-safe, not fail-confident); (3) obstacle
sensing from the same stereo depth. Global metric accuracy is a *post-hoc* mapping product, not a
real-time requirement. See [`docs/rekon10/canopy-ops.md`](../docs/rekon10/canopy-ops.md) (ice-hole doctrine + error budgets).

**Principal docs / artifacts:**
- `analysis/vio-quality.ipynb` (+ `analysis/vio_ekf_compare.py`) — per-flight VINS-vs-FC-EKF/GPS
  comparison on the **tracked** pose, papermill-parameterized, emits `vio-quality.json`.
- `containers/vio-estimator/offline_runner.py` (`vio-offline-runner`) — deterministic pose regen +
  provenance sidecar (`*.vinspose.polisher.json`, records fixture/config/source SHAs).
- `analysis/vio-input-alignment.ipynb` — input side: OAK-D IMU vs FC IMU, vibration PSD, feature health.
- `analysis/vio-online-offline-comparison.ipynb` — **online↔offline↔EKF**: does offline replay reproduce the flight's *own onboard* pose (`VISP`)? Emits `vio-online-offline-comparison.json`. New (260712, E16).
- `analysis/image-sharpness-vs-motion.ipynb` — image-data quality (UC6/mapping side): var-of-Laplacian focus vs exposure / motion / EKF-velocity / VIBE, split by flight regime. Emits `image-sharpness-vs-motion.json`. The analytic toolkit (blur budget, rolling-shutter/vibration-jello line-straightness, autofocus checks) overlaps the house-model doc — reuse it there.
- Upstream (pinned): `chobitsfan/VINS-Fusion@c525184`; `chobitsfan/oak_d_vins_cpp@378f40f`.
- Seed calibration: `host/ansible/roles/coordinator/files/oak_d.yaml` (`imu: 0`, `estimate_extrinsic: 0` — stereo-only default since [#69](https://github.com/symmatree/coordinator/issues/69); was `imu: 1`/`estimate_extrinsic: 2` pre-#69).
- Offline global-solve direction: [#59](https://github.com/symmatree/coordinator/issues/59) (GTSAM batch factor graph).

---

## What "working" means — two fitness axes, not one Boolean (sharpened 2026-07-29)

**"Runaway" is not a state; it is the bottom of an accuracy spectrum** — and where a pose sits on that
spectrum decides fitness *for a named purpose*, never in the abstract. "Sub-metre, it broadly knew where it
was" is a measurement, not a verdict. Two axes matter, and a pose can pass one while failing the other:

- **A — Local health (interactive fitness).** With a human closing the loop on **direct observation /
  VTX**, absolute pose barely matters; **local consistency** does — low drift and few discontinuities over
  the seconds between corrections. Metric: **sliding-window local ATE + reset/jump rate** (E14 method),
  *not* global ATE. *Hybrid tier:* a pose that drifts **slowly and locally** (bounded rate, no jumps) can
  **hold a hover** despite large accumulated error — which buys either **waiting for the operator** to come
  on the loop, or a **graceful landing**. "Hover-capable" is a distinct tier below "globally accurate," and
  it needs a trustworthy health signal ([#124](https://github.com/symmatree/coordinator/issues/124)) to
  trigger the hold.
- **B — Accumulated error (autonomous-mapping fitness).** With no human, fitness is a **clearance
  corridor**: how far the path could stray before striking an obstacle on either side — the **max
  deviation** from truth, which **grows with distance/time** (one mis-estimated turn offsets everything
  downstream, no loop closure to recover — E14), so the corridor **widens with penetration depth**. The
  **ice-hole doctrine is the mitigation** ([`canopy-ops.md`](../docs/rekon10/canopy-ops.md)): a periodic GPS re-anchor (or a clean
  VINS reset, [#67](https://github.com/symmatree/coordinator/issues/67)) **bounds** the accumulated error,
  resetting the corridor at each hole instead of letting it grow without limit. Feasibility here =
  achievable **mission length** and **max penetration depth** for a given corridor budget.

**Tiers (worst → best; each fit for strictly more):**
0. **Runaway** — worse than a random walk; fit for neither purpose. (`imu:1`, all regimes.)
1. **Non-diverging / better-than-noise** — bounded, tracks the shape; rules out (0) but clears nothing
   operational yet. (`imu:0`; the 260728 baseline sits here — ~1 m global over a 31 m out-and-back;
   measured **local ATE 0.17 m median**, ~5 recovered resets, corridor **0.23–10.7 m by leg length** —
   **E21**.)
2. **Hover-capable** — slow bounded local drift, jumps rare/rejectable → hold for the operator or land
   gracefully (axis-A hybrid). *Needs the health signal (#124) to be actionable.*
3. **Interactive-usable** — local health good enough to hand-fly a corridor on VTX.
4. **Corridor-usable (autonomous)** — accumulated-error corridor known and small enough to fly a mapping
   grid strike-free, with ice-hole resets bounding it.
5. **GPS-equivalent** — precise enough to run **under the trees** as primary nav. **The actual designed
   target** (everything above is a step toward it, not a substitute).

**"Post-alignment error < X" is a lab metric, not "works" — a separate gate stands between them.** Every
ATE in this doc is measured *after* an offline **Umeyama fit** that absorbs rotation, scale, and
translation, so it reports **trajectory shape** and is deliberately **blind to the frame/scale/extrinsic
error a real FC would be stuck with**. Operational readiness for interactive flight is the different, harder
claim — **flip `EK3_SRC` to VIO and keep flying** — and it additionally needs (none of which the aligned
metric can see):
- **VisOdom healthy** — the FC's ExtNav health check must accept the *live* stream. Today it constantly
  reports *"VisOdom not healthy"* (it blocked arming ~10 min on 260709); this gates arming regardless of
  post-hoc pose quality. Covariance/rate/delay + a sane pose (health signal: #124).
- **Calibrated VIO↔FC extrinsics** — the OAK-D→FC body transform (`VISO_POS_*` / orientation) is **not
  calibrated**, so the *raw* pose the FC would consume is in the wrong frame *even when the post-aligned
  trajectory is excellent*. The Umeyama fit silently corrects exactly this, which is why the aligned ATE
  can look great while the vehicle would still fly into things.
- **Real-time, un-aligned** — the FC gets the raw pose live; there is no offline fit to rescue the frame.

**Orientation is not a static `VISO_ORIENT` — it needs the T265 backend (2026-07-29).** Chasing the
"orientation" piece from the logs turned up a sharper story. Stereo-only VINS has **no heading
reference**, so its world-frame yaw is pinned *arbitrarily at each init* (`vins-stereo-only.md`): on the
one clean flight (260728) the VISP→NED horizontal rotation is **+11.5°** (non-cardinal; the divergent
260712 differs) — so a *fixed* `VISO_ORIENT` cannot correct a per-flight-varying datum. And the FC's
GPS-anchor (`align_position_to_ahrs`) is **translation-only** (verified in 4.7 source — updates
`_pos_correction`, never rotates). The yaw-alignment that *does* rotate the VIO frame onto the AHRS
heading lives **only in the IntelT265 backend** (`VISO_TYPE=2`, auto-aligns on the first pose via
`_align_yaw`), **not** the MAV backend (`VISO_TYPE=1`) we run — though both consume the same
`VISION_POSITION_ESTIMATE`. **SITL mechanism check** (`harness/sitl_extnav/orchestrate_yaw.py`): fed a
yaw-offset pose, `VISO_TYPE=2` emits `VisOdom: yaw shifted 273 to 3 deg` (the FC rotating the frame to
AHRS) while `VISO_TYPE=1` emits nothing — direct evidence of the backend difference. **Not settled** (a
neat check, not a verdict): the *position-level* consequence wasn't traced — SITL's lockstep died after
the VISO_TYPE-change reboot in-sandbox — and the align landed at ~3° vs a measured AHRS ~−12°, so
*whether it aligns correctly* (VINS attitude quality, init-relative roll/pitch, align-trigger timing) is
open. Candidate fix on the table: **`VISO_TYPE 1 → 2`**, to be confirmed on a real-frame flight.

So a good post-alignment ATE is **necessary but not sufficient**. A full "works" criterion (tiers 3+) must
fold in this operational chain; until then, quote *"post-alignment error < X,"* not *"works."* This gate is
tracked as a **task** in [#138](https://github.com/symmatree/coordinator/issues/138) (lever arm / orientation
/ delay / VisOdom-health, and its interaction with 4.7's GPS-anchored VisualOdom, #80/#65) — verified state
*as of that writeup*: `VISO_POS_X/Y/Z = 0`, `VISO_ORIENT = 0`, `VISO_DELAY_MS = 10`. **(Superseded on the
vehicle: 260814 flew `VISO_TYPE = 2`, `VISO_POS_X/Y/Z = 0.072 / -0.0375 / -0.116`, `VISO_DELAY_MS = 100` —
i.e. the package described below was applied. E36. What that changed, if anything, is not
assessed here.)** Two of these are now better understood
(see the orientation note above and E16): **orientation** is not a static `VISO_ORIENT` at all — the
candidate is `VISO_TYPE 1 → 2` (T265 backend yaw-align); and `VISO_DELAY_MS = 10` is just the ArduPilot
default, vs ~100 ms measured (E16) — set it from the pipeline latency, not defended harder than the default was.

**Lever arm measured 2026-07-31, with a frame subtlety on Y (now resolved).** Physical setup:
the OAK-D **module center** is ~72 mm forward, ~116 mm above the FC, on centerline → `VISO_POS_X/Z = 0.072,
-0.116` in body FRD (+X fwd, +Y right, +Z down). **But VINS reports pose in the `cam0` = left-imager frame,
not the module center**: `oak_d.yaml` has `body_T_cam0 = identity` and `body_T_cam1 = +0.075 X`. The two mono
cameras share X and Z and differ only in the horizontal baseline, so **only `VISO_POS_Y` is affected** — the
left imager sits ~37.5 mm (half the ~75 mm seed baseline) off module-center laterally. **Sign resolved:** both
imagers face forward, unrotated, so camera-right = vehicle-right and `cam0` (left) is on the vehicle's **left**
→ **`VISO_POS_Y = -0.0375`** (−Y in FRD). (`VISO_POS_X/Z` unaffected. The 37.5 mm rides on the machined
~75 mm baseline — physically fixed to sub-mm, not a drifting quantity — so this value is solid. The separate
~10% recovered-*scale* error (E17, one post-hoc flight) is an intrinsics/rectification question, **not** a
baseline error, and does not touch this lever arm.)

**Near-term measurement, before any map exists:** assess **max actual deviation from EKF/GPS** (eventually
from post-hoc SfM-aligned truth, given a good data-collection story) over a flight → the clearance a path
would have needed to avoid a strike. Feeding a *confident absolute pose* to the FC for autonomous traverse
stays **explicitly out of scope until tier 5** — the operator (FPV) + post-hoc reconstruction cover it
meanwhile.

---

## Data

Two matched captures, 2026-07-05 (coordinator #42), on the NAS under `datasets/flights/rekon10/`:

| run | dir | motors | role |
|-----|-----|--------|------|
| **handheld** | `260705-handheld-noarm/` | **off** | low-vibration control (but hand motion ≠ vibration-free) |
| **armed** | `260705-vio-logged/` | **on**, hard-mounted OAK-D | vibration treatment |

Each has: `wave-*.feat` (+ `.feat.json`) estimator-input fixture, the FC `.bin` (1980-dated), tlog/rlog.

**Two reconstructions per flight** (config-tagged): the **then-deployed** config (`imu: 1`, IMU-fusion — the 260705-era default, since superseded by stereo-only `imu: 0` in [#69](https://github.com/symmatree/coordinator/issues/69)) and
**vision-only** (`imu: 0`, stereo-only). Provenance in the `*.vinspose.polisher.json` sidecar records
which config (by sha256) produced each pose — the analysis records the `config_sha256` it consumed.

> **✅ Provenance now tracked (was not).** Pose is regenerated by `vio-offline-runner` in the
> vio-estimator image: single-threaded, no wall-clock solver cap, timestamp-ordered — **byte-reproducible**
> (verified: the real fixtures re-run to identical sha256). This **supersedes** the earlier qemu/`0.04 s`
> scratchpad poses, which had **no provenance** and disagreed with the tracked regen by ~2× on raw
> displacement (e.g. armed 41.9 km tracked vs 73.9 km loose). Do not use the old loose `*.vinspose.csv`.

> **UPDATE 2026-07-09 -- first live onboard flight (`260709-vio-first-light`).** VIO was fed to the FC
> (`VISO_TYPE=1`); the estimator ran live (~27 ms/solve) and its pose reached and was logged by the FC
> (`VISP`=8420 / `VISV`=8419). **But the live pose was garbage** (E15): positions diverged to +/-1.3e6 m,
> velocities to +/-2.6e7 m/s -- far worse than any offline replay -- so the FC's VisOdom health check
> rejected it and it flew GPS-primary (`EK3_SRC1=GPS`). So the estimator *does* spool up live and we now
> have first onboard evidence -- but not yet a *working* onboard pose. (Prior caveat, kept: until this
> flight every observation was offline replay.)

> **⚠ GPS-good open proxy (260705) vs. real canopy (260709).** The E1-E14 numbers are from the two
> **260705** flights: RTK throughout, good visual conditions, canopy *simulated* by withholding
> intermediate GPS -- treat those as an **optimistic floor**. **260709** is the first *real* under-canopy
> flight (GPS actually degraded RTK-fixed -> float -> DGPS -> 3D; feature-poor woods) and it **confirms
> "likely worse"**: the live estimator diverged to megameter scale (E15). The caveat now has data behind
> it, not just caution.

> **★ UPDATE 2026-07-12 — the missing joint capture, and offline replay REPRODUCES the live estimator
> (`260712-vio-flight-treecrash`).** This is the flight the "missing joint capture" was waiting for: the
> `.feat` tee (#78) **and** the onboard pose (`VISP`) **and** `LOG_REPLAY=1`, all on one run — so we can
> finally ask whether **offline replay reproduces what ran live**. It does, decisively: replaying the
> flight's `.feat` through `vins_fusion_offline` reproduces the flight's own **online** VINS pose (`VISP`)
> to **6 mm median / 42 mm RMSE, Umeyama scale 1.000** over the ~88 s pre-divergence window (rigid 180°
> VINS→ExtNav frame flip, +100 ms router latency; gyro time-sync NCC 0.76). **E16.** This is the biggest
> *methodology* result to date: it closes the central **"does offline represent live"** confound (below)
> for a *bounded* flight — the offline E9–E12 conclusions can now be read as speaking to the live
> estimator, not just an offline artifact.
>
> **260814-woods captures** (offloaded 2026-09-05), under
> `260814-woods/captures/18443010B1D8BC0800/20260814T125736Z/`: colour stills, `mono_rect_left`
> frames ([#125](https://github.com/symmatree/coordinator/issues/125)), disparity, the `.feat`, and
> the coordinator journal. Figures and method: that flight's `derived/README.md`. Observations:
> **E30-E36**.
>
> Two more from this flight. **The online pose was BOUNDED** this time — `VISP` stayed within ~50 m and
> tracked the FC EKF to **0.36 m** ATE for 88 s before a velocity-runaway divergence — **not** the 260709
> megameter blow-up (E15). So a live onboard pose *can* track for a window on this vehicle (**E17**); the
> divergence is the familiar `imu:1` endgame. And it ended in a **tree crash** — recovery + power-loss
> behaviour in `docs/power-loss-filesystem.md` and the crash writeup; **image-data quality** collapsed in
> flight (E18), pulling in the house-model doc's blur/vibration toolkit.

---

## Theories

The spine. Each theory owns its whole argument in one place: statement, **evidence for/against** (cited by
`E#` — the ledger is the registry, the theory is the home), current **status/certainty**, and its
**probes (`X#`)** — the candidate experiments that would move it, listed right here, uncommitted (`[x]` =
done, its finding folded into the cited evidence). Read a theory to decide whether, and how hard, it is
worth firming up; ignore the ones you never need. Status is provisional by construction — several below
are already marked disproven or reframed.

**Each theory as an operational claim (2026-07-28).** A theory here is not merely *"X causes Y"* — it is a
claim about **an observable metric**, the **region of driver-space that governs it**, and the **decision it
informs**. Each carries a one-line **Claim —** *metric* (what we can measure) · *region* (the drivers /
thresholds that dictate it) · *decision* (the task/resource choice it changes). This is the operational
inverse of "how good can it get": we are mapping **where each performance level holds** so we can *choose
tasks and resources* — the cross-capability version is `analysis/operating-envelopes.md` — not building a
black-box failure predictor. The aim is an **interpretable** driver model (understand *why*), not a fitted
one. (We didn't start this way on purpose: only with attempts in hand do you know what is easy — macro
flight stability has been fine, so there is deliberately no shudder/jerk theory — and what is the real
challenge worth a metric and a threshold.)

### T1 — Solver-iteration starvation under qemu — **DISPROVEN**
> **Claim —** *metric:* offline ATE / divergence · *region:* solver iteration budget (6–32 iters) · *behaviour:* **flat / invariant** (X11) · *decision:* solver tuning is **not** a lever — spend no compute there.
- **Against:** E9 — the deterministic offline harness runs Ceres to completion (no wall-clock cap),
  single-threaded, and reproduces the **same divergence**. Un-starving the solver changed nothing.
- **Status:** **disproven as the cause.**
- **Probes** (solver adequacy; also bear on T2, the `max_solver_time` 0.04 s cap in E6):
  - [x] **X11 — Ceres iteration-count sweep (offline, stereo-only) — DONE ([#63](https://github.com/symmatree/coordinator/issues/63)).**
    `analysis/tools/vio_param_sweep.py` swept `max_num_iterations` = {2,4,6,8,12,16,24,32} on the flown
    fixture (`260705-vio-logged`), scored vs FC EKF. Result: **ATE is flat (~0.95–0.99 m) from 6 through
    32 iterations, and 24 vs 32 are numerically identical (solve converged); scale ~0.90 throughout.** So
    the **deployed 8-iteration budget does not starve the solve** — the offline answer to whether 0.04 s /
    8 iters binds, complementing the "un-starving changed nothing" divergence angle above. `imu:1` for
    contrast scores only a degenerate ~16 s pre-divergence window (scale ~0.1–0.35) — not a tuning signal,
    another face of E10. Caveat: one flight, GPS-good/open; canopy will differ. Per-value tables +
    provenance: `<fixture>.max_num_iterations-sweep.json` in each flight dir. `WINDOW_SIZE` is compile-time
    (not swept). One outlier: `iters=4` scored 1.64 m (convergence artifact), flagged not smoothed.
  - [ ] **X4 — solver time / iteration count on the real Pi in flight** — the live counterpart to X11
    (does the 0.04 s cap bind live?).

### T7 — Our *replay* is the problem (I/O-timing artifact) — **DISPROVEN (as the cause)**
> **Claim —** *metric:* divergence · *region:* replay pacing/timing in the deterministic harness · *behaviour:* **invariant** (E9) · *decision:* trust the offline harness for *config-independent* conclusions, but it is **silent** on real-time/threaded behaviour — don't read live behaviour from it.
- **For (historical):** E5 (init `R0` moved with replay pace).
- **Against:** E9 — a deterministic, timestamp-ordered feed still diverges **identically and reproducibly**.
- **Status:** timing coupling was real in the old path, but removing it entirely leaves the divergence
  unchanged → **not the cause.** The deterministic harness is a trustworthy base for **config-independent**
  conclusions only — the divergence is *real and achievable* with this code + these inputs on some
  hardware, and byte-reproducible. It says **nothing** about real-time, threaded, or cross-platform
  behavior: the offline config (`num_threads=1`, no Ceres wall-clock cap, stepped time) may not represent
  the live one — unestablished, not assumed (see Methodology confounds).
- **Probes:**
  - [x] **X3 — Deterministic offline harness — DONE (E9, #54).** `vio-offline-runner`: byte-reproducible;
    divergence is real (this disproves T1 and T7). A trustworthy base for **config-independent**
    conclusions only (divergence is real, *achievable* with this code + these inputs on some hardware, and
    reproducible) — **not** a base for everything: silent on real-time / threaded / cross-platform
    behavior (see the Status above + Methodology confounds).

### T3 — Motor vibration corrupts the OAK-D IMU — **not isolated; do not credit**
*The hard-mounted OAK-D IMU eats motor vibration, poisoning IMU preintegration.*
> **Claim —** *metric:* `imu:1` pose-divergence onset · *region:* OAK-D vibration band-power (armed/hard-mounted vs isolated) · *behaviour:* hypothesised to tip into runaway above some level · *decision:* whether OAK-D **isolation** is a required resource. **Not isolated** — the low-vibration handheld run *also* runs away (E2), so vibration is not shown *necessary*; the loaded-hover probe (X14) is the discriminator.
- **For:** E1 (OAK-D accel band-power >5 Hz ~400–500× higher armed vs handheld — the camera IMU *does* see vibration).
- **Against:** E2 (the **handheld** run *also* fails on IMU-fusion) — **but** "motors off" is **not**
  "vibration-free": hand tremor, footfalls, and the walkaround itself inject IMU disturbance, so this
  does **not** cleanly isolate vibration. E4 (BNO085 fused output may not faithfully carry vibration).
- **Status:** **vibration-as-rescue unsupported at 2× isolation; not disproven (E22/E23, 2026-07-30).**
  The rubber-isolated flight (260730) **halved** the OAK-D >5 Hz accel (226 vs 444 band-RMS), yet `imu:1`
  **ran away bit-identically** (20,149 m). So 2× isolation does not rescue it -- but 2× is far from zero
  (E1: armed ~400-500× handheld), so this is **one partial-dose point, not a dose-response curve**, and
  does not disprove T3. Combined with E2 (handheld also fails) and E23 (runs away on the continuous 260705
  stream too, no instability warning): vibration keeps failing to *rescue*, but the discriminating
  **near-zero-vibration** test (X14) is still unrun. **Not shown to be the cause, not ruled out.** No
  positive candidate is thereby established -- the other open possibilities (IMU data-path/model, T4/T5;
  time-sync; **or a physically faulty part**) are all still live, none of them proven.
- **Probes:** X8(c) synthetic vibration injection (breaking threshold vs measured spectrum) and X7 raw
  high-rate IMU — both owned under **T4/T5** (the shared IMU-path tooling), referenced here.
- **Related — imagery side (see T9/T10).** Motor vibration also degrades the *imagery* (E18), recorded as
  its own theories **T9** (motion blur) and **T10** (autofocus vs fixed focus) below — the color still is
  the UC6/mapping product, not the VINS input (the rectified 400p mono the features run on is **not
  saved**). The two probes that discriminate vibration for *both* the IMU (here) and the imagery (T9) live
  here:
  - [ ] **X14 — stable hover (loaded-prop vibration with minimal camera motion; RTK truth).** The right
    discriminator for BOTH the IMU (T3) and the imagery (E18): a hover has **loaded** props (real flight
    vibration) but near-zero translation/rotation, so it separates **vibration/jello** (still present) from
    **motion blur** (removed). Easy to get on a calm day with a good RTK lock. Compare IMU spectrum + still
    sharpness + RS/jello line-straightness across **at-rest / hover / forward-flight**; cap exposure to
    probe the motion-blur half. Bonus: it also fills the doc's **"hover untested"** gap (level-2 local
    stability — see "What working means"). *(Props-spinning-on-the-ground is only a quick look — **unloaded
    props vibrate differently** from thrusting ones, so it is not a faithful flight-vibration proxy.)*
    - **The ground alternative is worse than "only a quick look" — it is a different dose, and it is not
      collectable anyway (measured 2026-09-06).** On 260814, armed-on-the-ground sits at median **2057 RPM
      / VIBE 0.99**, against **6970 RPM / VIBE 8.72** in the airborne hover: **3.4× the RPM and 8.8× the
      vibration**. So a motors-on-ground frame is ~11% of the hover dose, not a control for it. And it
      cannot be dwelt in: arming is a **transition, not a state** on this airframe — it times out within
      seconds and forces a disarm/rearm cycle, so the operator arms and launches within ~5 s. E18's
      *"no motors-on-ground frames to separate them"* should therefore be read as naming a control that
      **this vehicle cannot supply**, not a gap to be filled by a ground capture. The hover is the only
      route to it.
    - **260814 supplies a partial instance (imagery half only).** Its t=85–125 s hover is airborne at full
      hover RPM (VIBE 8.2–9.7) with translation **0.01–0.07 m/s** and body rate **0.17–0.59 deg/s** across
      5 stills — i.e. loaded-prop vibration with motion effectively removed, which is what this probe asks
      for. **E34** is measured on exactly those frames. Left unchecked because X14 also asks for the IMU
      spectrum and an at-rest / hover / forward-flight comparison, neither of which was run.
  - **Make it SPECTRAL, not scalar — the resonance question.** `VIBE` (E18's correlate) is broadband RMS;
    it hides *which* frequency. The failure to fear is a **resonance** — a motor prop-pass line (or a
    harmonic) exciting the camera **VCM/autofocus**, the mount, or **aliasing** a high-frequency line into
    the IMU's fused band (→ T5's data-path concern). Find it by making the analysis spectral: OAK-D accel
    **PSD / spectrogram** + the image **jello frequency** (from the line-straightness analysis), both
    plotted **vs motor RPM** (`RCOU`/`ESC`) — a resonance is a peak that blows up at a particular RPM, or a
    fixed structural peak the RPM sweeps through. A hover is *one* RPM operating point; to **find** the
    resonance you want a **sweep** — in flight a slow throttle ramp (or a few steady throttle settings), or
    on the bench a shaker **sine sweep** on the camera/mount. Sweep **slowly**: a high-Q mode needs many
    cycles to build to its steady amplitude, so a fast sweep skips right over it.

### T4 / T5 — Cam↔IMU calibration/extrinsic wrong (T4) and/or BNO085 IMU data-path mismatch (T5)
*The seed extrinsic is a rough guess refined online; the OAK-D IMU is fused/filtered, zero-at-rest,
wrong noise model — either poisons the tightly-coupled fusion once accelerating.*
> **Claim —** *metric:* `imu:1` divergence · *region:* the **IMU/extrinsic data path** (axis-remap, IMU product/rate/time-sync) — *not* the vision path (vision-only tracks, E10) · *decision:* any IMU fix belongs in the data path, not the estimator. **Which** sub-driver (extrinsic vs IMU-model vs time-sync) governs it is **not isolated**.
- **For:** E3 (over-scale from takeoff; velocity won't zero at rest — bias/gravity/scale signature).
  E4 (gyro reads **exactly** 0.000 at rest — a fused/filtered BNO085 signature). **E10** — **vision-only
  (no IMU path at all) tracks the whole flight at ~1 m ATE, while IMU-fusion runs to 41.9 km.** So the
  break is **in the IMU/extrinsic path**, not the vision — which is what T4/T5 assert (without yet
  isolating *which* of the two).
- **Against (argument, not yet measured):** the OAK-D is the **ArduPilot-wiki reference platform** for
  this exact pipeline and is used for indoor nav — where walls are the ground truth and a km-scale
  divergence would be obvious — so the *same hardware* does not inherently run away. That shifts weight
  from "the BNO085 part is unsuitable" toward **our IMU data path** (DepthAI output mode/rate/timestamp
  sync; the IMU→camera **axis remapping**). Extrinsics are rigid factory geometry (single machined
  housing; Luxonis EEPROM cal), so "unknown seed extrinsic" overstates it — baseline/translation are
  trustworthy; the plausible residual is the axis remap. E4 (processed gyro at rest) still stands as a
  real signature we may be feeding VINS the wrong IMU product/mode. **Net: nothing isolates this; less
  "bad part", more "our data path" — unproven either way.**
- **Status (empirical, no attribution):** **`imu:1` has never converged** -- on any flight we have run
  (260705/12/30), extrinsic on or off, isolated or not; **`imu:0` stays bounded throughout.** That is a
  strong *pattern* (enabling IMU fusion reliably breaks it) -- it is **not** a cause. With **zero**
  successful `imu:1` runs we cannot name the mechanism, and we do not even know the OAK-D IMU is physically
  sound. What the recent runs establish is only what they *cannot* decide: the 260730 fixed-vs-calibrated
  **bit-identity** means the extrinsic estimator **never engages before divergence**, so those runs
  **cannot test T4**; and E23 (`imu:1` also diverges on the month-old 260705 capture) is **confounded** by
  a different software + ArduPilot build, so it does **not** exclude the stalls or isolate anything either.
  **Every candidate stays live:** extrinsic/axis-remap (T4); IMU product/rate/noise-model/init (T5);
  time-sync; **or a physically faulty part.** Progress requires a run that varies **one** factor -- X7
  (raw vs fused IMU), a bench IMU-health check, a synthetic-IMU ablation (X8), or a case where the
  extrinsic demonstrably engages before divergence.
- **Probes** (the shared IMU-path tooling; parts also serve T3 and T8):
  - [ ] **X8 — Synthetic VI ablation (known-world simulator).** Generate IMU + stereo-feature measurements
    along a known truth trajectory (FC EKF state); (a) perfect world → is the ceiling the estimator/config?
    (serves **T8**) (b) true-vs-seed extrinsic → isolates **T4**; (c) inject measured vibration / BNO085
    model / feature dropout → breaking threshold vs measured (serves **T3**). Run on the harness. Real work
    (a small VI simulator).
  - [ ] **X7 — Capture raw high-rate OAK-D IMU** (vs BNO085 fused; tests **T5**, and unblocks faithful
    vibration capture for T3's X8(c)).
  - [ ] **X13 — 260709 first-light forensics (what we CAN do without `.feat`).** Feature vectors were
    **not** captured, so offline pose-regen (#42) is blocked for this flight — we cannot re-derive *why*
    the live pose diverged in detail. What we *can* do: the FC `.bin` + ~1104 coordinator captures
    (disparity + 12 MP stills; `analysis/vio-first-light-captures.ipynb`). Bridge the clocks via
    **`VISP.CTimeMS` (coordinator time, logged in the FC `.bin`) ↔ capture `monotonic_ns`**, place
    stills/disparity on the EKF/GPS timeline, and line up scene transitions (clearing the treeline →
    full-sky view, entering shadow) against the GPS-status degradation and the divergence onset.
    Qualitative, but the best available handle on the *why* of the live IMU-path blow-up (E15). **Next
    capture must record `.feat`.**

### T6 — Aggressive motion / feature geometry breaks tracking
> **Claim —** *metric:* stereo-only local tracking (feature count → local ATE, divergence flag) · *region:* **feature count** — driven by scene texture + **available light** — and aggressive motion · *behaviour:* holds while features stay healthy (~30–44), breaks as they collapse (E7 handheld ~3; **E19 canopy ~≤4**) · *decision:* this **is** the live operational envelope for stereo-only (the `imu:0` deployed mode) — a scene/motion/light boundary, mapped in `operating-envelopes.md`. The dominant sub-driver on real canopy was **light** (E19), not motion.
- **For:** E7 (handheld feature count collapses to ~3/frame during the barrel roll).
- **Against:** E7 also shows the **armed** run diverges with healthy feature counts. And vision-only
  tracks the armed flight fine (E10), so features aren't the ceiling there.
- **Status:** contributes to the handheld barrel-roll endgame only; not the general explanation. May
  also inflate the handheld vision-only metric (see the *singular-error* caveat).
- Under canopy the feature count shows a scene effect but no collapse: airborne median 26/frame,
  deep-woods median 18, and 0.8% of deep-woods frames below 10 (**E32**). Separately the tracker's
  feature *output* is absent ~52% of the airborne window, upstream of the scene (**E31**,
  [#156](https://github.com/symmatree/coordinator/issues/156)) — count and continuity are different
  failures, and this theory's metric is count.
- **Probes:**
  - [x] **X12 — Handheld slow-and-steady capture (stereo-only): local shape excellent, global frame smeared — DONE (E14).**
    The `260705-handheld-noarm` `wave` fixture is a **real, valid** passage of the vehicle through space
    (the FC not driving the motors does not invalidate it). Measured facts:
    - **Gentle + slow, by design** (pilot: deliberately slow-and-steady): translation p90 0.38 / **max
      1.20 m/s**, |gyro| p90 10 / **max 83 deg/s**, attitude rate max 44 (0% above the 200 deg/s cutoff).
      The flown `vio-logged` mission moves **~2x faster** (speed max 2.55 m/s; rotation comparable, |gyro|
      max 97). FC truth is **RTK-fixed** (GPS Status median 5, 22–31 sats).
    - **Local shape is tracked very well throughout:** per-20 s sliding-window Umeyama gives **median local
      ATE 0.12 m** (most windows 2–44 cm). The single global-fit ATE (3.34 m) is **~28x worse purely as a
      global-frame offset**, not a tracking failure. Near-stationary holds are the stablest part (tiny
      local error; global scale just unconstrained there on ~no translation).
    - So the poor global number is the **metric + the estimator's lack of a global datum** (no loop closure
      / lossy incremental marginalization, per `vins-stereo-only.md`) — **what the current code does, not
      an inherent limit of the motion or data.** Consistent with the pilot's read that a turn's rotation
      was left too open, offsetting everything downstream in the global frame while relative tracking
      holds. Strong candidate for the batch solve (#59, T8's X9) to reconstruct globally.

    Retractions (don't restate): earlier "discrete breakdown at ~55 s" is **disproven** by the local
    metric; "coincident with `unstable tracking`" was never timed and is dropped; "low-parallax hover
    starvation" is **disproven** (near-stationary is the *stablest* part). Not yet isolated: whether the
    residual global offset is dominated by a rotation vs a scale error — both give local-good/global-bad;
    the drift-vs-speed correlation is confounded by scale (wrong scale → error ∝ distance). TODO:
    per-segment VINS rotation vs FC `ATT` to test the rotation read.
  - [x] **X2 — Local-vs-global agreement check — DONE (E25/E26, 2026-08-30).** Rather than segmenting
    the published pose, this went one level lower: frame-to-frame relative pose solved **directly from
    the tracker's stereo feature stream** (`chobits_features` in the `.feat`), RANSAC 3D-3D, VINS
    entirely out of the loop. Notebook: `flights/rekon10/260812-hover/vio-local-vs-global.ipynb`.
    Result is split by axis -- **attitude confirms the local/global story, position does not**. See
    E25, E26. X12 was the first instance; this is the estimator-independent one.

### T8 — Architecture: redundant, low-quality inertial fusion given authority (**new; leading**)
*We run **two** inertial fusions in series: VINS fuses the **bad** IMU (BNO085 fused output,
hard-mounted, online-estimated extrinsic/time) into a pose **first**, corrupting it, then feeds that to
the FC's central EKF — which already fuses a **good**, vibration-isolated, calibrated IMU. The worse
copy is given authority, and a tightly-coupled estimator **trusts** it (a factor with a weight), so a
wrong IMU doesn't get ignored — it drags the whole solution off a cliff.*
> **Claim —** *metric:* pose-divergence magnitude · *region:* **IMU-given-authority** (`imu:1` vs `imu:0`) — a near-binary switch · *behaviour:* `imu:1` diverges **everywhere tested** (handheld / open / real canopy: km–Mm, E10/E15/#120), `imu:0` stays bounded (~tens of m) · *decision:* **SETTLED — IMU excluded** from the tightly-coupled path; reintroduce only as a weighted relative-velocity factor ([#59](https://github.com/symmatree/coordinator/issues/59)). The lone open sub-question is *why* `imu:1` fails (T3/T4/T5) — it only matters insofar as it decides whether isolation could ever bring `imu:1` back inside the envelope.
- **For:** E10 (vision-only, which uses **no** IMU, tracks; IMU-fusion explodes). The "VINS-Mono needs
  both" assumption is **monocular** — mono is scale-blind, so IMU is its only scale source. **Stereo
  observes scale from the baseline directly**, so VINS-**Fusion** stereo-only is a first-class supported
  mode and needs no IMU for scale. Our `imu: 1` was a *choice*, and the wrong one for these flights.
- **Against:** stereo scale weakens at range vs. the ~75 mm baseline (far/high scenes decay toward
  monocular) — so "drop the IMU" is right for **near-field** (the actual use case) but not universally;
  the long-term answer is likely to let the **FC's good IMU** do the inertial fusion once, not none anywhere.
- **Status:** **leading architectural theory.** The natural fix: stereo VO → FC EKF (one good IMU does
  inertial fusion once); IMU, if reintroduced, as a **preintegrated relative velocity factor** with an
  honest covariance, not global authority ([#59](https://github.com/symmatree/coordinator/issues/59)).
- **E35** measures the front end this fix would consume — stereo VO from the tracker's features, VINS
  out of the loop, on 260814. The two channels come out very differently: attitude drifts **3.59° over
  47 s** against `VISP`'s **51.30°** on the same window, while position is **worse** than `VISP`
  (aligned ATE 0.49 m vs 0.16 m) and under-scaled (fit-scale 0.708 vs 0.860).
- **Probes:**
  - [x] **X1 — Vision-only (stereo, `imu:0`) — DONE (E10).** Tracks the whole flight (~1 m ATE armed).
    The fault is the IMU/extrinsic path, not the vision; scale is stereo-observable without IMU.
  - [ ] **X9 — Offline batch factor-graph solve (GTSAM), GPS-anchored — [#59](https://github.com/symmatree/coordinator/issues/59).**
    Relative visual factors + sparse GPS priors at tack points (intermediate GPS withheld), seeded from
    vision-only; extensible to IMU as **preintegrated relative velocity factors** (velocity-at-time, not
    global assertions) to compare with/without IMU on equal footing. Directly tests the PPK endpoint-lock
    premise and E11. Runnable on existing data.
  - [ ] **X10 — EKF innovation-gate / jump handling (no new filter).** Replay the tracked VIO (jumps and
    all) through ArduPilot EKF3 (SITL/log) and check whether the **existing** `EK3_POS_I_GATE` (+ source
    noise, `EK3_GLITCH_RAD`) rejects the E12 jumps *without* rejecting real motion. Lane switching
    (`EK3_IMU_MASK`/`EK3_ERR_THRESH`) is a **different** mechanism (IMU-core health, not measurement
    outliers). **Don't build a filter** until the existing gate is shown insufficient. (Referenced from
    `sitl-validation-experiments.md`.)
  - [ ] **X5 — FC-IMU substitution** — the redesign this theory points to (let the FC's good IMU do the
    inertial fusion once); confounded on current data (FC IMU ~25 Hz, extrinsic for the OAK-D location).
  - [ ] **X8(a) — perfect-world ceiling** from the synthetic simulator (owned under **T4/T5**): is the
    ceiling the estimator/config rather than the inputs?

> **⚠ Metric-inflation caveat (methodological) — CONFIRMED (E14).** A global metric (ATE, "massive
> divergence") can be dominated by a **few bad segments** — one over/under-tight bend, one angular error
> — while the trajectory is section-wise well matched. **Demonstrated on the handheld vision-only run:**
> per-20 s local ATE **0.12 m** while the single global fit reads **3.34 m** (~28×) — the global number
> is a couple of bad angles (a mis-estimated turn) smearing an otherwise well-tracked trajectory, exactly
> what this caveat warns about. Always check **local segment-wise agreement** before quoting a global ATE.
> (Note: the `imu:1` global figures in E10 were only ever characterized globally, not held to this local
> metric; their divergence magnitude is real — rotation preserves distance — but the mechanism was never
> checked the same way.)

---

### T12 — The 7.5 cm stereo baseline is too short for the scene depth we actually fly — **new; measured, upstream of the architecture question**
*Depth from a stereo pair degrades as Z^2/(B*f). At the ranges these flights actually present, disparity
is a couple of pixels and depth error is tens of percent -- so translation magnitude is unobservable
before any estimator sees it.*
> **Claim —** *metric:* triangulated feature depth + disparity distribution vs the B=0.075 m baseline ·
> *region:* outdoor scene depth (trees, open ground at 5-70 m) × 640x400 mono at f~450 px ·
> *behaviour:* both VINS and a naive relative-pose solve under-report motion by 2-6x ·
> *decision:* whether **camera geometry** (mount orientation / scene distance), not fusion architecture,
> is the first thing to fix.
- **For:** **E27** — 260812-maneuver in-flight features: median depth **14.7 m**, p75 26.9 m, p95 70.7 m,
  **64.5% beyond 10 m**; median disparity **2.25 px** with **45% under 2 px**. Both estimators under-scale
  on the same flight (fit-scale 0.566 relative-pose, **0.162** VISP) and their post-alignment error curves
  are nearly superimposed -- same input, two smoothers.
- **Against / not established — and the depth-accuracy case is weaker than it first looked.** With `fx`
  and the disparity noise **measured** rather than assumed (E29: `fx` = 400.5 px derived from the feature
  stream; at-rest disparity noise median **0.212 px**), depth uncertainty is **7% at 10 m, 14% at 20 m,
  21% at 30 m** — about half what a 0.5 px / f=450 guess gives, and 7% at 10 m is tolerable. So "the
  baseline is unsuited" is **not** supported by the depth-accuracy numbers alone.
  **The mechanism is also unsettled, and the obvious one has the wrong sign:** triangulation with noisy
  disparity *over*-estimates depth (`Z = Bf/d` is convex in `d`), which would predict **over**-scaling,
  while the observed error is **under**-scaling. A mechanism that does predict under-scaling is
  **regression dilution / weak translation observability**: image motion from translation goes as `t/Z`,
  so at 15 m median depth and ~3 m/s a 20 Hz interval gives ~1% image motion, and a least-squares fit
  dominated by uninformative distant points shrinks toward zero (the 5 cm RANSAC threshold on points with
  metres of 3D uncertainty pushes the same way). **That is a hypothesis, not demonstrated.**
  Also unresolved: feature count is low (E28) with its own unknown cause.
- **Status:** **geometry measured; mechanism not established.** No cause named for the overall failure.
  What is defensible is the *observability ratio*, not a depth-accuracy verdict.
- **E32** puts the depth distribution on a real under-canopy flight: disparity median **2.39 px**
  (41.4% under 2 px), depth median **12.6 m** (58.2% beyond 10 m), deep-woods median **10.2 m** —
  against E27's open-field 2.25 px / 45% / 14.7 m / 64.5%. Marginally closer-in, not qualitatively
  different.
- **Probes:**
  - [ ] **X22 — point the camera down.** At 5 m AGL a nadir view puts the scene at ~5 m instead of a
    14.7 m median. The argument is **observability, not depth accuracy**: it roughly triples the
    per-frame translation-to-depth ratio (`t/Z`), which is the term that makes translation estimable at
    all; depth error only improves 7% -> 3.5%. `VISO_ORIENT` is a parameter, not a code change. Costs
    forward obstacle detection; **does not** cost yaw rate, which a downward camera observes directly as
    image rotation about the optical axis. Untested risks: whether ground texture at 5 m supports feature
    tracking, and whether the narrower footprint shortens track length at survey speed. The OAK-D S2 and
    IoT-40 do **not** help -- same or shorter baseline.
  - [ ] **X23 — usable-feature segments.** Operator observation: some flight segments carry usable
    feature counts between dropouts. Score relative-pose quality (inliers, residual, disparity) per
    segment and test whether solve quality tracks scene depth, feature count, or `VIBE` -- the three
    candidates E28 leaves open.

## Theories — imagery quality (UC6 / mapping side)

*The color stills are the mapping product (UC6), **not** the VINS input — the rectified 400p mono the
features run on is not saved. But it's the same vehicle, camera, and vibration, and the analytic tools are
shared with the house-model experiments doc (`fables/Datasets/experiments-house-model.md`: blur budget,
rolling-shutter-vs-vibration-jello line-straightness, autofocus checks, `--cameras` calibration reuse).
Different vehicle (hard-mount, no gimbal, OAK-D ≠ DJI) → different **answers**, same tools.*

### T9 — Motion blur (long exposure × airframe motion) is the dominant cause of unusable in-flight stills — **leading, confounded with vibration**
*Auto-exposure ran to ~30 ms @ ISO 110 in flight (≈4 stops of gain unused), so any airframe rate/translation smears the frame during the exposure window.*
> **Claim —** *metric:* sharpness (var-Laplacian), a proxy for mono feature-trackability · *region:* **blur-pixels** = airframe-motion × exposure-time (÷ GSD), plus vibration-jello · *behaviour:* sharpness collapses as blur-pixels grow (E18 ~43×) · *decision:* the levers are **cap exposure**, **fly slower**, **isolate**; *which* dominates (blur vs jello) decides which lever pays. The **blur-pixels** predictor is itself a testable model claim (X19) — the point is to *understand* the driver, not fit it.
- **For:** E18 — in-flight sharpness collapses ~43× (var-Laplacian median 4827→113); **0/29** in-flight frames reach at-rest sharpness; correlates exposure −0.66, EKF-vel −0.53, gyro −0.42; the streak/arc morphology is *directional* (motion during exposure), not uniform focus-soft.
- **Against / not isolated:** **VIBE is the *strongest* correlate (−0.81)** and everything covaries at takeoff (no motors-on-ground frames), so motion-blur vs **vibration-jello** is *not separated*. A short exposure freezes **both**, so pulling the exposure lever does not discriminate them — the hover/spectral probes (X14, under T3) do.
- **Status:** leading but **confounded, not isolated.** We are pulling the exposure lever now because it is cheap and helps regardless of which dominates — *not* because motion blur is confirmed to be the whole story.
- X15's exposure cap is live on the vehicle: all 45 stills on 260814 came in at exactly **4996 us**,
  with ISO carrying the range 162 to a pinned 1600 (**E33**) — the first hardware data on that lever.
  The banding survives the cap (**E34**); at ~100 Hz a half-cycle is ~5 ms, so a 5 ms exposure still
  integrates close to a full peak-to-peak excursion. Airframe speeds on 260814 were <=1.7 m/s with
  body rates mostly under 1 deg/s, so it is a weak test of this theory's speed axis.
- **Probes:**
  - [x] **X15 — cap the still exposure (deployed, PR #105).** `OAK_STILL_MAX_EXPOSURE_US` (5 ms default) caps the AE shutter → trades to ISO/gain, accepting some underexposure. Hardware-untested (OAK-D down); the next capture's sharpness re-scores it. Attacks the **exposure axis only** — residual motion at 5 ms and any vibration-jello remain (a status, not a disclaimer).
  - [ ] **X18 — blur budget.** Compute the exposure that holds blur < N px at flight speed/GSD (house-model method) → set the cap from physics, not the 5 ms guess.
  - [ ] **X19 — driver-model selection: is *blur-pixels* the right predictor?** On the 260712 stills we already scored, fit sharpness against three candidate drivers: **(a)** exposure time alone, **(b)** airframe speed alone, **(c)** **blur-pixels** = speed × exposure ÷ GSD (image motion *during* the exposure window — the physically-motivated combination). If (c) explains sharpness materially better than (a) or (b) (compare R²/AIC; watch the takeoff covariance and the VIBE confound), the physical blur model is validated and the exposure cap follows from it (X18) rather than a guess. This is model *understanding* — an interpretable, physically-grounded driver — not a fitted black box, and it is the concrete instance of drawing the imagery/VIO envelope in a **velocity × exposure** space. Data already in hand (E18 sidecars: per-still exposure + interpolated EKF-velocity/gyro).
  - [ ] **X20 — read the jello-band frequency off the still and identify it in the accel/RPM.** The color-still banding is a *spatial* fingerprint of a *temporal* vibration written by the rolling shutter: for the mode we shot (**OAK-D main RGB, IMX378, 12 MP**; capture is our patched in-repo driver — the line/readout time for *that* mode is the linchpin of the number), a band pitch of *R* rows maps to **f ≈ row_rate / R** (row_rate = rows ÷ readout-time). So the image *carries a frequency* — the point is to **identify** it against an independent source: OAK-D accel **PSD** (full rate; X7's raw capture — the OAK-D IMU is **too noisy for VIO**, T3/E1, but that "noise" *is* the co-located vibration field we want here), FC raw gyro / `VIBE` spectrum, motor **RPM × blade-pass** (`RCOU`/`ESC`), FC dynamic-notch centres. A match names the mechanism with no mitigation required — the still-side instance of T3's "image jello frequency vs RPM". Pairs with **X14** (hover isolates jello from blur) and the rubber-standoff isolation trial (does killing the fast content turn *blurred* bands into *sharp-but-displaced*, i.e. software-correctable?).
    - **The odd part — *bands of blur*, a low-frequency recurrence of high-frequency smear — is a discriminator, not a curiosity.** A *single* tone already predicts it (rows wobble sinusoidally *and* per-row blur peaks at the velocity maxima — both at f_vib, a quarter-phase apart), and row_rate ≫ f_vib so it is **not** readout aliasing — banded blur alone needs no second source. *But* if the fast smear is modulated by a slower envelope, that is a **beat / AM** (two motors a few Hz apart, or a structural mode gating a prop line), which appears **directly in the full-rate accel** as a modulated amplitude / two close peaks. So one capture tests single-tone vs beat: band pitch = carrier, accel envelope = (or rules out) the modulator.
  - [ ] **X14 — stable hover** (owned under T3) — the discriminator between motion blur and vibration-jello.

### T10 — Autofocus is the wrong mode; a calibrated fixed focus is better — **proposed**
*The OAK-D RGB has an autofocus VCM that can hunt or silently refocus; per the house-model doc, AF also tends to lock the highest-frequency signal (canopy twigs) over the useful subject.*
> **Claim —** *metric:* sharpness / cross-flight focus repeatability · *region:* focus mode × subject distance (AF hunts or locks canopy twigs vs a fixed lens position set for flight distance) · *decision:* fixed **calibrated** focus vs auto — but a *wrong* fixed value is worse than AF, so it needs the bench calibration (X17) first and defaults to `auto`.
- **For:** focus can change with or without a command (VCM); house-model confirmed AF-on-treetops leaves the useful subject soft; a fixed lens position is repeatable across flights.
- **Against / caveat:** a *wrong* fixed position is worse than AF — needs a calibrated lens value first, so the knob defaults to `auto`.
- **Status:** proposed; the knob ships (X16), the value is **uncalibrated** (default `auto`).
- The banding is not a **static or whole-frame** focus state: it alternates in bands within a single
  frame, and the band phase moves between two frames 10 s apart (**E34**). `OAK_STILL_FOCUS = 125`
  (PR #205) merged 2026-09-05, after this flight, so a flight on a pinned lens position is now
  available as the comparison.
- **Probes:**
  - [x] **X16 — fixed-focus knob (PR #105).** `OAK_STILL_FOCUS` (0–255 lens position, AF off); default `auto`.
  - [ ] **X17 — bench focus calibration.** Sweep `OAK_STILL_FOCUS` imaging a target at flight distance; score each with var-of-Laplacian (`image-sharpness-vs-motion` is the scorer); pick the peak → the fixed value. (House-model `--cameras` reuse is the ODM-side analogue for post-crash consistency.)

### T11 — The eye-visible horizontal blur bands are the motor **1st-order (rev)** line written by the rolling shutter — **suggestive, not established**
*The color stills show distinct horizontal bands of blur. If they are rolling-shutter jello from a periodic
vibration, the band pitch maps to a temporal frequency (`f = row_rate / pitch`, `row_rate = 3040 rows / 33 ms`
IMX378 12 MP readout) that should match an independent mechanical source — per-motor RPM, or the FC dynamic
harmonic-notch centre. This is the affirmative of **X20** (measure the jello frequency), raised to a theory by a
first suggestive data point.*
> **Claim —** *metric:* visible band pitch (rows) → Hz via the 33 ms readout · *region:* rolling-shutter readout × motor-order vibration reaching the (isolated) camera · *behaviour:* the eye-visible bands recur at the **1st-order rev line**, not blade-pass · *decision:* (a) whether still banding is a **usable camera-side vibration diagnostic** (cheaper than X7 raw IMU), and (b) whether the **sharp inter-band rows are maskable** for features/densification while the blurred bands are dropped.
- **For:** **E24** — 260730 hover: operator-annotated **4 bands** → ~760-row pitch → **~120 Hz**, on the **fast-pair motor rev (114–121 Hz)** = FC notch 1st-harmonic (`INS_HNTCH_MODE=3` ESC-driven, `HMNCS=1`), **not** the 3-blade blade-pass (~290–360 Hz). Readout is Luxonis-documented (33 ms; `t_line` ~10.9 µs).
- **Against / not established:** rests on the operator's **visual** 4-band count and the 33 ms readout — an **automated** var-Laplacian band detector **over-counts** (6–10 bands, and fires on a *ground* frame too), so it does **not independently confirm** the count or pitch. Metadata↔pixel timing is ~5 s off ([#167](https://github.com/symmatree/coordinator/issues/167)), so the per-frame RPM tie is coarse (hover RPM is flat, so ~120 Hz holds across the window — but n=1 frame, 1 flight). Scene modulates the profile (the operator's least-sure band was blur over a dark low-frequency region).
- **Status:** **suggestive, not conclusive. No cause named** — band=rev is *consistent with* rotational-vibration jello, not established (over-counting detector, coarse timing, n=1). Distinct from **T9** (motion-blur *magnitude*); T11 is about the *frequency* of the residual banding.
- **E34** measures band pitch by a scene-independent route: within 260814 it is tight (**895 rows**,
  IQR 880-901). The cross-flight values differ (1935 on 260728, an unstable 1841 on 260730) at
  essentially the same hover rev line, but **the camera mount changed between all three flights**, so
  that comparison tests nothing here -- see E34. It does raise a candidate this theory does not
  currently consider: the bands may be the **isolator mount resonance**, whose frequency is set by
  stiffness and preload rather than by a motor order. That predicts pitch tracks **preload**, not RPM.
- **Probes:**
  - [ ] **X21 — targeted banding-collection mission.** Fly a **stable hover** in front of a **high-contrast, known-geometry target** (OpenCV checkerboard printout, or the brick wall of the house) at a fixed distance/GSD, so the bands are visually unambiguous and cleanly separable from scene texture. Then: (a) measure band pitch → Hz robustly over many frames (the target kills the scene confound); (b) compare per-frame to per-motor rev / notch centre; (c) check whether an automated detector reproduces the visual count on a clean target (the fix for the over-counting); (d) assess **masking** — are the sharp inter-band rows usable while blurred bands are dropped. Pairs with **X14** (hover isolates jello from motion blur); a tight RPM tie needs [#167](https://github.com/symmatree/coordinator/issues/167) (capture-timing) resolved.

---

## Evidence ledger

The **registry** of observations the theories cite — one row per finding, with its **source/provenance**
(which run, notebook, or decode produced it, under which configuration) and which theory it **bears on**.
An `E#` is dated and re-weightable, never a settled fact: if a discovery later discredits how it was
produced (a config, a decode, a build), trace it here → the theories that lean on it → the conclusions
above. New evidence is authored where it argues (under a theory); this table is the index.

| ID | Evidence | Source / provenance | Bears on |
|----|----------|---------------------|----------|
| E1 | OAK-D accel band-power (>5 Hz) ~400–500× higher armed vs handheld | `vio-input-alignment.ipynb` §3 | +T3 (IMU sees vibration) |
| E2 | Handheld (motors off) **also** fails on IMU-fusion | tracked regen | ~T3 (but motors-off ≠ vibration-free) |
| E3 | IMU-fusion over-scales ~from takeoff; velocity doesn't return to 0 at rest | `vio-quality.ipynb` | +T4, +T5 |
| E4 | Gyro reads **exactly** 0.000 at rest (both runs) | fixture decode | +T5 (BNO085 fused); −T3 |
| E5 | Init `R0` changed with replay pace (old qemu path) | historical | +T7 (historical) |
| E6 | `max_solver_time` is a wall-clock Ceres cap (0.04 s) | `estimator.cpp:1083` | context for T2 |
| E7 | Handheld feats → ~3/frame at barrel roll; armed diverges with healthy feats | `vio-input-alignment.ipynb` §4 | +T6 (handheld), −T6 (armed) |
| E8 | Early trajectory tracks EKF shape; time-align NCC ~0.95 (armed) | `vio-quality.ipynb` | −"fundamentally broken" |
| **E9** | **Deterministic offline harness** (native, full Ceres iters, `num_threads=1`, timestamp-ordered) reproduces the **same divergence**, **byte-reproducible** across runs | `vio-offline-runner` | **−T1, −T7** (divergence is real, not a harness/replay artifact) |
| **E10** | **Vision-only** (`imu:0`) tracks the **whole** flight — armed ATE **0.98 m**, handheld 3.34 m (global best-fit, scale ~0.9); **IMU-fusion** (`imu:1`) runs to **41.9 km** (armed), 218 m (handheld), speed→1076 m/s | `vio-quality.ipynb`, tracked regen | **+T4/T5/T8** (break is in the IMU path); scale from stereo baseline |
| **E11** | **Drift vs. anchor spacing** (armed, GPS withheld, backyard proxy): locally-rigid residual rms **6/16/26/37/64 cm** at K = **2/5/10/20/40 m** | K-sweep on vision-only track | ice-hole leg budget; +[#59](https://github.com/symmatree/coordinator/issues/59) |
| **E12** | Vision-only inter-sample steps: **99% < 10 cm** (smooth), but **rare 1–2 m single-sample jumps** (max 128 cm armed, 239 cm handheld) | jump analysis on tracked track | the real safety threat; fail-**confident** for IMU-fusion (smooth 1076 m/s) |
| **E13** | **Offline continuous timesync** (windowed angular-rate cross-correlation, VINS pose vs FC, flown log): global align **NCC 0.95**; per-40 s residual offset **std 16 ms, range 40 ms** over 4 min — a smooth **~160 ppm** clock-rate drift, no jitter/jumps | `vio_ekf_compare.align_time` (windowed) | +[#65](https://github.com/symmatree/coordinator/issues/65) timing tractable (TIMESYNC-disciplinable; PPS not needed for consistency) |
| **E14** | **Handheld local vs global:** per-20 s sliding-window fit **median local ATE 0.12 m** (2–44 cm) vs **3.34 m** single global fit (~28×) — shape tracked throughout; the global number is a frame offset (a mis-estimated turn), not tracking failure | sliding-window fit (X12) | confirms the metric-inflation caveat; −"handheld vision-only is bad" |
| **E15** | **First live onboard (260709, real under-canopy).** Live stereo pose fed to the FC **diverged catastrophically** -- `VISP` positions to **±1.3e6 m**, `VISV` to **±2.6e7 m/s**, sent with a **constant 0.2 m** covariance (fail-confident); `Rst`=0 (no reset-counter, #67). FC ran GPS-primary (`EK3_SRC1=GPS`); `VisOdom: not healthy` blocked arming ~10 min. GPS degraded RTK-fixed→3D; ~52 m vertical run, 10 m/s max climb. | FC `.bin` decode (`VISP`/`VISV`, #10) | live ≠ offline; real-canopy failure far worse than the ~1 m open proxy; **no `.feat` captured → cause not diagnosable in detail** (the tracker input tee, [#78](https://github.com/symmatree/coordinator/issues/78), now closes this for future flights) |
| **E16** | **Offline replay reproduces the flight's own ONLINE pose.** 260712 `.feat` → `vins_fusion_offline` vs the flight's logged `VISP`: residual **6 mm median / 42 mm RMSE**, Umeyama **scale 1.000**, rigid **180°** frame, **+100 ms** latency, over the ~88 s pre-divergence window; gyro time-sync NCC 0.76 | `vio-online-offline-comparison.ipynb` (260712); provenance in `derived/vio-online-offline-comparison.json` | **RESOLVES "offline ≡ live"** (methodology confound) for a *bounded* flight — offline results represent the live estimator |
| **E17** | **First BOUNDED onboard pose (260712).** Online `VISP` stayed within ~50 m and tracked the FC EKF to **0.36 m** ATE (scale 0.87) for ~88 s, then divergence; offline reproduces it exactly (E16). **This flight was stereo-only (`imu: 0`, #69)** — so the divergence is **not** the `imu:1` runaway; it is stereo-only feature-starvation (**E19**) | FC `.bin` (`VISP`/`XKF1`) + offline regen | live *can* track a window (cf. E15 megameter); the divergence is **stereo-only feature-starvation (E19)**, a distinct failure from the `imu:1` endgame |
| **E18** | **In-flight color stills unusable; vibration the top correlate.** var(Laplacian) median collapses **~43×** (4827 at-rest → 113 in-flight); **0/29** in-flight frames reach at-rest sharpness; exposure 1.2→6.1 ms; strongest correlate **VIBE −0.81** (then exposure −0.66, EKF-vel −0.53, gyro −0.42) — **but all covary at takeoff** (no motors-on-ground frames to separate them) | `image-sharpness-vs-motion.ipynb` (260712) | data-quality / UC6 imagery; +T3 (vibration) directionally; **not** the VINS input (the rectified *mono* is, and it is **not saved**) |
| **E19** | **Feature starvation coincides with the 260712 divergence — and both nav sources degrade together on woods entry.** The tracker's `.feat` **feature payload collapses ~10×** (peak ~4600 B ≈ ~44 feats at motion onset → floor ~330–430 B ≈ ~4 feats) around the divergence (feat-mono ~280–310 s ≈ FC ~452 s, within gyro-sync tolerance). The slide is **bounded** — VISP ≤ ~52 m, plateaus ~40–47 m (**not** the E15 megameter) — and `PErr` stays pinned at **0.2** throughout (fail-confident, cf. E15). Ground truth: altitude **~1–2 m** at divergence (the ~10 m peak is the terminal strike at ~556 s), GPS-primary so VIO never drove. Scene at divergence (color still + disparity): **dense backlit forest**, **not open sky**. **Light collapse:** color-still auto-exposure ramps **1.2 ms → 30 ms ceiling** (+ISO) on woods entry, maxed ~445–480 s (≈divergence), recovering to ~1.6 ms on exit — an ambient-light drop (a scene property, so the **mono** VINS pair is starved too, though its exposure isn't logged). **GPS co-degrades:** RTK-float → **3D fix at ~468 s (~15 s after the VIO divergence)**; **sat count steady 30–32, HDop 0.48–0.54** (a naïve health check misses it), the tell is accuracy **HAcc ~1 m / VAcc ~1.5 m** (vs cm under RTK) — canopy carrier-phase attenuation, not sat loss | `.feat` payload series + FC `.bin` (`VISP`/`GPS`/`GPA`/`XKF1`) + still exposure sidecars (`exposure_us`/`iso`) + disparity/stills (260712) | **[evidence]** feature count collapses ~10× coincident with the divergence (bounded, fail-confident); exposure ramps to its ceiling (light collapse); GPS drops RTK→3D ~15 s later with sat-count/HDop staying green. **[operational]** both nav sources degrade on the same woods-entry event, **VIO first** — the backup fails in the exact regime it exists to cover, and a sat-count/HDop check would miss the GPS side (accuracy `HAcc`/`VAcc` or RTK status is the real signal). **[conjecture]** light-starvation → feature loss → divergence (coincident + mechanism, causation unproven, ±clock-sync); the **mono VINS input is not saved** (E18), so mono exposure/blur is inferred from the color cam's ambient reading, not measured. **[flag]** derived `oak_d.yaml` is **`imu:0` (stereo-only)** — reconcile with E17's `imu:1` attribution |
| **E20** | **x86 ≡ arm64 offline (the #85 cross-arch check).** Same image (`sha256:36f38a3c`), same **stereo-only** seed config (`imu:0`, as deployed -- both fixtures), `vins_fusion_offline`: **native amd64** (in-cluster pod / daemonless rootfs — **byte-identical** to each other) vs **arm64-under-qemu**. On the **bounded** 260705 run: position agrees to **0.32 mm median / 0.66 mm rms / 2.3 mm max** (extent 3.25 m), attitude to **0.057° max**. On the 260712 flight: **≤2.4 mm for the first 279 s (75% of rows)**, then the stereo-only pose's own seed-calibration **velocity-runaway ~88 s after takeoff** (FC t+452, **mid-flight** -- the known `vins-stereo-only` limit, **not** IMU and **not** the physical crash, which came ~100 s later at ~t+555) goes ill-conditioned and the arches split to **tens of m** (post-onset rms 26 m, max 64 m). Native ~**14×** faster than qemu. | notebook + in-cluster (`analysis/tools/pull_estimator_rootfs.py --arch`, this doc's Option B/C); scratch harness in #85 | **RESOLVES "x86 ≡ arm64 offline"** — the diverge/bounded verdict is **arch-invariant**; finer agreement is sub-mm where the run is bounded. The tens-of-m tail split is the estimator's own **mid-flight** runaway (~88 s in; the strike came ~100 s later — see E19), neither arch is truth there, not an arch fidelity fault |
| **E21** | **First measured fitness-axis numbers (260728 sunny baseline).** Online `VISP` vs FC `XKF1` on the exact FC clock (no cross-correlation, `compare_visp`), airborne window ~365–590 s, ~31 m out-and-back. **Axis A (local health):** per-20 s sliding-window Umeyama gives **median local ATE 0.17 m** (p90 0.68 m) — good local consistency, well under the ~1 m global — **but ~5 reset/jump events** (VISP position-derived speed **>5 m/s** vs the real **<1.6 m/s**, at t≈397/443/484/558/568 s) spike a window to **3.6 m**. Local pose is usable *between* resets; the **jumps**, not the drift, are the axis-A gate. **Axis B (clearance corridor):** per-segment (reset-bounded) global Umeyama, **max deviation grows with un-reset leg length** — 2 m leg → **0.23 m**, 14 m → **0.50 m**, 16 m → **3.6 m**, 31 m → **10.7 m** (median 1.4 m). | online `VISP` (`compare_visp`, exact FC clock, merged `3264487`) + per-segment sliding-window Umeyama on 260728 `.bin` (`VISP`/`XKF1`) | **First measured numbers for the axis-A/B tiers.** Axis A: a **tier-2/3 candidate** on local consistency, **gated by** the resets being made rejectable ([#67](https://github.com/symmatree/coordinator/issues/67) reset-counter, [#124](https://github.com/symmatree/coordinator/issues/124) health). Axis B: the corridor-vs-leg table **sizes the ice-hole interval** — resetting every ~10–15 m holds the corridor sub-metre; a 31 m un-reset leg blows it to ~10 m. **CAVEAT: per-segment Umeyama → post-alignment**, a **lower bound** on real flyability (the uncalibrated extrinsic the FC is stuck with, [#138](https://github.com/symmatree/coordinator/issues/138), would widen it); **bright/open → optimistic floor** (canopy starves features, E19) |
| **E22** | **Rubber isolation does not rescue `imu:1` (260730).** Same isolated-flight `.feat` (`20260730T115037Z`), offline `vins_fusion_offline`: **imu:0** stereo-only **bounded 69 m**; **imu:1** (`estimate_extrinsic:2`, pre-#69 default) **runs away 20,149 m / 239 m/s**; **imu:1** with a **fixed** extrinsic (`estimate_extrinsic:0`) is **bit-identical** (20,149 m). Isolation was mechanically real -- it **halved** the OAK-D >5 Hz accel band-RMS (**226** vs **444**, non-isolated 260705). Logged failure mode: **`numerically unstable in preintegration`** from the first frames. | native amd64 rootfs (`pull_estimator_rootfs.py`, Option B1) + `.feat` IMU/feature streams | Isolation at **this dose does not rescue** `imu:1` -- but 2× is far from zero (E1: armed ~400-500× handheld), so this is one partial-dose point that **weakens vibration-as-rescue without disproving T3**; near-zero-vibration (X14) is the real test. The fixed==calibrated **bit-identity means the extrinsic estimator never engaged** (divergence precedes it), so this run **cannot test T4** -- neither supports nor refutes it. **No cause is named:** it rules nothing out cleanly and -- with **no successful `imu:1` run, ever** -- establishes nothing positively. Open discriminators: X14 (near-zero vibration), X7 (raw vs fused IMU). |
| **E23** | **`imu:1` also runs away on the month-old 260705 capture.** `imu:1` on 260705's `.feat` **runs away 25,910 m / 686 m/s** with **zero** `numerically unstable` warnings -- diverges **smoothly** (bounded ~30 s, then integrates away), unlike the gappy flights' preintegration instability. 260705's IMU stream is continuous ~100 Hz (no ~5 s stalls) -- **but see provenance.** | native amd64 rootfs (`pull_estimator_rootfs.py`) + 260705 `.feat` -- **captured ~1 month earlier under different onboard software AND a different ArduPilot build**, pre the in-tracker-capture stall; **not reproducible under the current build**. Differs from the recent flights in *many* variables, not just the stalls. | **Confounded -- does NOT isolate the gaps.** Because 260705 differs in software, ArduPilot version, and a month of changes, `imu:1` diverging here **does not exclude** the stalls as a contributor to anything; at most it shows `imu:1` divergence is **not unique to the current build**. No attribution follows: with **zero** successful `imu:1` runs and the OAK-D IMU's physical health unknown, no cause -- IMU model, product/axis-remap, time-sync, init, or **a physically faulty part** -- is named or ruled out. The stalls remain a separate safety problem ([#156](https://github.com/symmatree/coordinator/issues/156)). |
| **E24** | **Eye-visible still banding ~ motor 1st-order rev (260730 hover; suggestive, n=1).** Operator annotated **4 horizontal blur bands** on an in-flight hover still (`_00000020_`, ~2.5 m, motors ~6400 rpm) → ~760-row pitch → **~120 Hz** via the IMX378 12 MP **33 ms** readout; matches **fast-pair motor rev (114–121 Hz)** = FC harmonic-notch 1st-harmonic (`INS_HNTCH_MODE=3`, `HMNCS=1`), **not** 3-blade blade-pass (~290–360 Hz). | operator visual annotation + `image-sharpness-vs-motion` blur profile (PR [#161](https://github.com/symmatree/coordinator/pull/161)) + `ESC`/`INS_HNTCH` decode (260730 `.bin`) | **+T11 (suggestive).** **Caveats:** rests on the *visual* count — an automated var-Laplacian detector **over-counts** (6–10 bands, fires on ground frames too), so **not independently confirmed**; metadata↔pixel timing ~5 s off ([#167](https://github.com/symmatree/coordinator/issues/167)) → coarse per-frame RPM tie (hover RPM flat, so ~120 Hz holds); **n=1 frame, 1 flight**. **No cause named** — *consistent with* rotational-vibration jello, not established. Probe: **X21** (targeted target mission). |
| **E25** | **Local relative pose is sound where global attitude is not (260812-hover).** Frame-to-frame rigid pose from the tracker's stereo features, no VINS: on an FC-VIBE-confirmed **at-rest** capture (VIBE X/Y/Z all 0.01; OAK-D accel sd 0.052 m/s^2) 4011/4079 pairs solve, median 15 inliers, **4.4 mm** residual, **0.22 m** drift over 208 s, **1.46 deg** attitude. In the armed hover window: 690/1647 solve (42%), median **5** inliers, **23.4 mm** residual, **6.75 deg** attitude drift (max 13.1). `VISP` attitude vs AHRS on the sibling flights reached **142-158 deg**. | `.feat` `chobits_features` + `analysis/ardupilot_log`; notebook `260812-hover/vio-local-vs-global.ipynb` | **+T8 on attitude only.** With `imu: 0` there is no gravity reference, so global roll/pitch/yaw are **gauge freedoms** -- unobservable, held only by the marginalisation prior. A relative rotation has no gauge. `harness/sitl_extnav/orchestrate_yaw.py` already records the yaw half: *"Stereo-only VINS has no heading reference, so its world-frame yaw is arbitrary."* |
| **E26** | **On position the local solve is NOT better -- and neither is usable (260812-maneuver, true extent 30 m).** Aligned ATE vs FC EKF: relative-pose integration **7.48 m** rmse (med 5.43, max 19.3, fit-scale 0.566); `VISP` **9.22 m** (med 7.28, max 18.6, fit-scale **0.162**), or 24.3 m rigid. Both collapse the 30 m ground track into a ~10 m blob; their error curves are nearly superimposed. The equivalent comparison on **260812-hover is degenerate and withdrawn** -- true extent there is ~1.2 m in a near-straight line, so Umeyama has almost nothing to constrain rotation or scale (fitted scales 1.658 / 0.534). | same pipeline, 260812-maneuver `.feat` + `.bin` | **Does NOT support "local deltas are sufficient" for position.** The failure is shared, so it is upstream of the fusion interface -- see **T12**. Retract any reading of the hover-window ATE numbers. |
| **E27** | **Stereo geometry is the measured upstream limit.** In-flight triangulated depths (260812-maneuver, armed): p25 7.4 m, **median 14.7 m**, p75 26.9 m, p95 70.7 m; 64.5% beyond 10 m. Disparity median **2.25 px**, 45% under 2 px. With B=0.075 m, f~450 px: depth error 7%/15%/30%/44% at 5/10/20/30 m. | `.feat` features, direct triangulation | **+T12 (new).** Explains the shared under-scaling in E26 and the ground-vs-flight residual split (4.4 mm at near-field vs 22 mm at 15 m median). |
| **E28** | **Feature supply is low, and light and autofocus are eliminated as causes.** In-flight median **24-31** stereo-matched features/frame against `setNumTargetFeatures(16*5)=80` per camera; the `118` in the send loop is datagram-buffer arithmetic, not a tuning cap. Mono (the VIO input) ran at **417-1376 us p50 exposure at ISO 100-101** across 260728/260730/260812 -- minimum gain, so in **open flight** a longer exposure buys nothing. **This does NOT eliminate light under canopy:** no flight with captures ever saw low light on mono (>5 ms frames: 0-1.4% everywhere), so the dark case is *unmeasured*, not excluded. `OAK_MONO_MAX_EXPOSURE_US = 0` leaves mono **uncapped**, and 260709 shows what the ceiling looks like when it is reached -- its *still* camera pinned at **29,995 us / ISO 1068**. A mono AE free to run to 30 ms under canopy would motion-blur the VIO input, which is E19's starvation mechanism with a number on it. The mono pair is **fixed-focus**, so `OAK_STILL_FOCUS` / AF cannot affect the VIO input at all (it is the colour still camera only). | `.feat` counts; `mono_rect_left` capture sidecars; `feature_tracker.cpp` | **No cause named for the low count.** Scene content and vibration remain open (**X23**). Candidate relaxations, in source terms: stereo preset is `HIGH_ACCURACY` + `setLeftRightCheck(true)` which *rejects* uncertain disparity and an invalid disparity kills its feature outright; `PAIR_DIST_SQ = 9` (3 px) match tolerance; then `setNumTargetFeatures`, which would also require raising `big_buf`. **The under-canopy case is retrievable, not lost:** 260814-woods has FC logs but its coordinator-side captures were never pulled off the aircraft. Those would test mono AE pinning, canopy feature counts, and T12's depth distribution in close-in trees -- the one geometry where a 7.5 cm baseline might be adequate. **[AMENDED 2026-09-06: those captures were offloaded 2026-09-05 and now exist. The three tests this row names have been run and are recorded as E32 (canopy feature counts, depth distribution) and E33 (mono AE under canopy). The "no cause named for the low count" verdict is untouched; E31 adds a fourth candidate alongside scene and vibration.]** |
| **E29** | **Camera intrinsics and disparity noise, measured rather than assumed.** The feature stream carries both raw pixels and undistorted normalised coords, so `fx` falls out directly: **fx = fy = 400.5 px**, cx 311.5, cy 207.8 (residual 0.0000 px), i.e. **77.3 deg HFOV** on the 640x400 mono -- not the ~450 px previously assumed. Disparity noise measured from the at-rest capture (a stationary feature's disparity variance *is* the measurement noise, 63 tracks with >=15 observations): median **0.212 px**, p75 0.573, p90 0.957 -- not the 0.5 px assumed. | `.feat` `chobits_features`, 260812-hover at-rest session | **Weakens T12's depth-accuracy case by ~2x**: depth uncertainty is 7% at 10 m / 14% at 20 m / 21% at 30 m. **Caveat:** 0.212 px is an *at-rest floor*; in-flight matching is worse (RANSAC residual 4.4 mm at rest vs 23.4 mm armed), so the flight-time noise is unmeasured and these are optimistic. |
| **E30** | *[method]* **Capture-to-FC clock tie measured at ~2 ms stability (260814).** The coordinator wall clock **stepped +184.288 s** at monotonic 69.3 s; the coordinator journal dates the same step to monotonic **69.52 s** (`systemd-timesyncd`: *"Initial clock synchronization to Fri 2026-08-14 09:01:11 EDT"*, after three NTP timeouts against the public pool). Sidecar `wall_clock_unix` **before** the step is therefore 184 s early, which reads as a 185 s hole in the capture cadence -- `monotonic_ns` and `seq` are both continuous, so there is no hole. The same step appears independently in the FC log as a **-184.283 s** jump in `VISP.RTimeUS` at FC t=71.3. **After** the step, FC-UTC (from `GPS` week/ms) minus coordinator `RTimeUS` = **-0.077 s, sd 0.002 s** (n=2652). | 260814 capture sidecars + `.feat` + FC `.bin` (`GPS`, `VISP`) + `journal-260905.tar.gz` | **Method, not a theory.** Any capture-to-FC join should key on `monotonic_ns`, not `wall_clock_unix`. Speaks to [#167](https://github.com/symmatree/coordinator/issues/167) (recorded as "~5 s, untested") and complements E13 (drift, not offset). **Caveat: the 2 ms is *stability*, not accuracy** -- the -0.077 s absolute number carries the FC-to-UTC fit's own residual (9 ms rms, 46 ms max) and any FC-side GPS timestamp latency, neither of which is separated here. Whether this holds on other flights is untested; the step is a boot-time NTP event and its size will differ per boot. |
| **E31** | *[method + result across the corpus]* **Every `.feat` fixture recorded with the capture overlay running is missing 38-59% of the frames the camera produced.** Feature-frame **device** timestamps sit exactly on the 20 fps grid (grid residual median **0.0000 s** across all 11 fixtures on the NAS), so implied-vs-emitted frames are countable: 260705-handheld **0.0%**, 260705-vio-logged **0.0%**, 260812-hover/113912 **2.0%**, 260812-hover/114548 **1.5%** -- all four wrote no capture artifacts -- against 260812-hover/113948 **38.1%**, 260724-bench **43.8%**, 260712-crash **44.5%**, 260814-woods **48.9%**, 260812-maneuver **49.1%**, 260728-sunny **55.7%**, 260730-rubber **58.8%**, all of which did. No overlap between the groups; among the six flight sessions that wrote images, loss against images/second gives **r = 0.85**. On 260814 the loss is visible end-to-end: `VISP` carries exactly the same **2445** samples and the same 42 (>1 s) / 20 (>2 s) gap counts as the `.feat`, and all **101** `VISP` gaps >0.5 s coincide with a `.feat` gap within 0.5 s (median offset 0.05 s). | device-timestamp decode of every `.feat` on the NAS; 260814 FC `.bin` (`VISP`); `journal-260905.tar.gz`. Detail and method in [#156](https://github.com/symmatree/coordinator/issues/156) | **Methodology confounds** (see the new entry there) and [#156](https://github.com/symmatree/coordinator/issues/156). **No cause named.** The separation is observational, not a controlled A/B: 260705 (0.0%) predates the capture overlay *and* differs in onboard software and ArduPilot build (the same confound E23 flags) *and* its fixture came from the external `vio-ipc-record` rather than the in-tracker tee. The two 0-artifact 260812 sessions are disarmed and stationary. The nearest thing to a control is **inside 260812-hover** -- same day, same build, same hardware, three consecutive sessions at 2.0% / 38.1% / 1.5%. Arm-gating ([#88](https://github.com/symmatree/coordinator/issues/88), 2026-07-30) makes "wrote images" and "was armed" perfectly correlated in every later session, so capture load and motors/vibration are separated only by 260705-vio-logged (armed, motors on, hard-mounted, 0.0%), which carries the build confound above. What is *excluded* on 260814: power/thermal (`coord-throttle: 0x0 [clean]` at all ten journal samples through the flight) and kernel-visible USB faults (zero kernel messages after monotonic 30 s). **Which rows sit on which fixture:** E10/E11/E23 rest on the two **260705** fixtures (**0.0%** loss), E22 on a **260730** fixture (**58.8%**), E16/E17/E19 on **260712** (**44.5%**). Recorded as provenance, not as a challenge to any of them -- offline replays the same file, so online and offline see the same gaps, and E16's 6 mm agreement is unaffected. This is a data-collection bug ([#156](https://github.com/symmatree/coordinator/issues/156)), not a replay-fidelity question. |
| **E32** | *[flight datapoint, 260814]* **Under-canopy feature supply and scene depth, from the `.feat` (260814).** Airborne-gated (t 75.4-346.3 s, derived physically -- see the Methodology note). Stereo-matched features/frame: **median 26**, p05 14, min 5, against `setNumTargetFeatures(16*5)=80`; by regime **open lawn 34, entering woods 24, deep woods 18, exit 29**, with only **0.8%** of deep-woods frames below 10 features. Sub-pixel disparity over 58,268 airborne feature observations: p25 1.30 / **median 2.39** / p75 4.56 px, **41.4% under 2 px**; depth **median 12.6 m**, **58.2% beyond 10 m**; by regime median depth 19.0 m open, 11.3 m entering, **10.2 m deep in**, 7.5 m on exit. Independent cross-checks from the same stream: `fx` recovers to **400.49 px**, `cx` **311.47** (E29: 400.5 / 311.5), and rectified stereo rows agree to **0.39 px** median. | 260814 `.feat` `chobits_features`, direct triangulation; airborne window from FC `CTUN`/`XKF1` | **T12 / E27, T6, E19, E28.** Compare E27 (260812-maneuver, open field): 2.25 px median, 45% under 2 px, 14.7 m median, 64.5% beyond 10 m. Under canopy is **only marginally closer-in** than the open-field flight T12 was raised on. Feature supply shows a clear ~2x scene effect but **no E19-style collapse** (E19: ~10x collapse to ~4 features). Ground frames were excluded, and they are a different scene -- on-ground disparity median 20 px (Z ~1.5 m) against 2 px airborne, on-ground feature median 14 against 26 -- but on **this** flight they are only 3% of the capture set (arm-gating, [#88](https://github.com/symmatree/coordinator/issues/88)), and excluding them leaves the airborne medians unchanged to 2 decimal places. The gating matters for the pre-gating flights, not here. |
| **E33** | *[flight datapoint, 260814]* **The rectified-mono VIO input, saved for the first time, under canopy (260814).** 248 `mono_rect_left` frames ([#125](https://github.com/symmatree/coordinator/issues/125); no other capture session on the NAS has them). Mono auto-exposure ran **580 us in the open to a hard 10,000 us ceiling under canopy at ISO 100-112** -- minimum gain throughout, i.e. AE spent shutter and never traded to gain. `OAK_MONO_MAX_EXPOSURE_US` is `0` (uncapped) and `monoLeft->setFps(20)` would permit 50 ms, so **what pins it at exactly 10 ms is unexplained**. On inspection at 1:1 the frames are sharp with abundant texture, including the 9992 us deep-canopy frames; a var-Laplacian score over the 239 airborne frames correlates **+0.25** with exposure (i.e. weakly *positive*, not the negative a blur mechanism would give) and shows no relationship to `VIBE`. Separately, the colour-still exposure cap is live and working: **all 45 stills at exactly 4996 us** with ISO carrying the range 162 to a pinned 1600 -- the first hardware test of X15 / PR #105. | 260814 `mono_rect_left` PNGs + sidecars; still sidecars; `image-sharpness-vs-motion` scorer | **E28, T9, T6, [#158](https://github.com/symmatree/coordinator/issues/158).** E28 recorded that no flight with captures had ever seen low light on mono, so the dark case was *unmeasured, not excluded*, and predicted that an uncapped mono AE running long under canopy would blur the VIO input. The AE did run long (10 ms, 24x the open-field p50) and, on this flight, **the frames do not look blurred** -- but note the cameras are **global shutter and fixed focus** (`oak-d-mount.md:74`), and at 640x400 the angular sampling is 6.3x coarser than the 12 MP still, so the mono is structurally far less exposed to both jello and the still's blur mechanism. **No claim is made here about whether 10 ms would blur under harder motion**; this flight's speeds were <=1.7 m/s. |
| **E34** | *[method; one flight, cross-flight comparison confounded]* **Scene-cancelled still-banding measurement.** Two stills of the same scene are registered by phase correlation; the **ratio** of per-row gradient energy between the registered frames divides the scene out; a dense sinusoid fit over that ratio gives a band pitch. On **260814** (stills seq 4-8, during a stationary hover -- 0.02 m/s, 0.2 deg/s body rate, same 5 ms exposure and ISO 331) the ratio is a clean x0.2-x4 oscillation with the two frames' per-row sharpness near **anti-phase**, and the fit gives **895 rows, IQR 880-901** over the 4 pairs passing the quality filter. Applied to older flights it gives **1935 rows** on 260728-sunny (IQR 1817-1981, n=7) and an internally unstable **1841** on 260730-rubber (IQR 1210-1856, n=10), at hover rev lines within 2% of each other (114.5 / 116.9 / 116.0 Hz). | stills + `ESC` decode on 260814 / 260728 / 260730; pairs kept at phase-correlation peak >0.10 and sinusoid R^2 >0.60; 260812-maneuver produced no pair above threshold | **T11 / E24, T10, T3.** **The cross-flight comparison cannot be read as a test of the rev-line hypothesis, because the camera mount changed between all three flights** -- 260728 predates the rubber isolators, 260730 *is* the isolation trial, and 260814 flew isolated and possibly re-torqued (the isolator preload is not set to a known value, and the operator reports a slipping mode while tightening in which the rubber pillar twists and then relaxes, so preload is not repeatable across remounts). A changed mount predicts a changed frequency whatever the source, so this neither supports nor refutes band=rev. What survives: **within one flight and one mount state the pitch is tight and the structure is camera-side rather than scene** (the ratio cancels the scene), and E24's over-counting caveat reproduces (single-frame detectors on 260814 spread over 62-281 Hz and fire on ground frames). **New candidate this raises:** an isolated camera on a rigid airframe is a mass-spring system with its own natural frequency set by isolator stiffness and preload, so the bands may be the **mount resonance** rather than a motor order -- which predicts pitch tracks **preload**, not RPM. The discriminating experiments are (a) vary RPM within one flight at a fixed mount state, and (b) vary preload deliberately at a known torque. **Nothing here should be load-bearing for a hardware or flight-profile decision.** |
| **E35** | *[method + first result]* **Step A -- frame-to-frame relative pose direct from the tracker features, VINS out of the loop (260814).** Triangulate each stereo-matched point from the undistorted normalised coords, match to the previous frame by tracker feature ID, solve rigid pose with RANSAC (depth-adaptive inlier threshold, median 0.386 m) and, separately, rotation alone from unit **bearing** vectors. **2381/2444 pairs solve (97%)**, median 22 shared points, median 14 inliers, bearing-rotation angular residual **0.057 deg** per pair. Integrated and compared to FC `ATT`/`XKF1` per continuous segment, with `VISP` scored over the **identical windows**: on the longest segment (**47.4 s**, 582 poses) attitude drift is **3.59 deg local against 51.30 deg for `VISP`**; over all segments with >2 m true extent (n=8) the medians are **8.38 deg local against 24.22 deg `VISP`**. On position the local solve is **worse**: aligned ATE rmse **0.49 m local against 0.16 m `VISP`**, fit-scale **0.708 against 0.860**, both under-scaling. Over the stationary hover (92.5-124.6 s, 406 poses) where the vehicle drifted **0.0040 m/s** by EKF, the local solve drifts **0.0456 m/s** and `VISP` **0.0821 m/s**. | 260814 `.feat` + FC `.bin`; same method family as E25/E26 (`260812-hover/vio-local-vs-global.ipynb`), re-implemented; method and caveats in that flight's `derived/README.md` | **T8, T12, E25, E26, X23.** Independently reproduces the E25/E26 axis split on a second flight, under canopy, at **3x the solve rate and ~3x the inliers** of the 260812 armed hover (E25: 42%, median 5). The position under-scaling therefore does **not** appear to be a feature-supply artifact, and (given E31) is not obviously a frame-loss artifact either -- which bears on what fixing #156 would and would not buy. **Caveats:** the camera-to-body rotation is taken as an exact axis permutation and is **not calibrated**, so a fixed frame error appears as apparent drift and sets a floor on short segments (0.85 deg/s median over all segments against 0.076 deg/s on the 47 s one -- trust the long segment); segments are short (median true extent 3.6 m, best 13.6 m), so Umeyama is only moderately constrained -- less degenerate than the 1.2 m case E26 withdrew, but not strong; the RANSAC threshold is depth-adaptive, so residuals are **not** comparable to E25's fixed 5 cm; all numbers are post-alignment, under the doc's standing caveat. Note the hover result: solving locally **halves** the hover drift but leaves it at ~11x the true rate. |
| **E36** | *[flight datapoint, 260814]* **Onboard `VISP` behaviour on the first flight of the #138 package (260814).** Flown parameters: **`VISO_TYPE = 2`** (T265 backend, the [#155](https://github.com/symmatree/coordinator/issues/155) candidate), `VISO_POS_X/Y/Z = 0.072 / -0.0375 / -0.116` (the 2026-07-31 measured lever arm including the left-imager Y offset), `VISO_DELAY_MS = 100` (from E16's measured latency, not the 10 ms default); `EK3_SRC1` GPS-primary with ExtNav in source set 2. The FC logged `VisOdom: yaw shifted 328 to 328 deg` at t=41. `VISP` position stayed within ~4 m to t~130, then advanced in a staircase of velocity spikes -- 19 m at 150 s, 44 m at 210, ~187 m through 290, **megametres from ~305 s**, then froze. The reported **`PErr` ramps monotonically 0.30 -> 100 and saturates**; `Rst` stays 0 and `Ign` 0 throughout. On the vertical channel, over the stationary hover (85-120 s) where the EKF is flat to 0.005 m/s, raw `VISP` drifts **+0.065 m/s vertically and +0.059 m/s horizontally** -- comparable in absolute terms. | 260814 FC `.bin` (`VISP`, `VISV`, `XKF1`, `PARM`, `MSG`) | **[#124](https://github.com/symmatree/coordinator/issues/124), [#138](https://github.com/symmatree/coordinator/issues/138), [#155](https://github.com/symmatree/coordinator/issues/155), E12, E15, E17, E21.** Recorded as an observation only. Two things differ from prior flights and are worth having on the record: the covariance is **not pinned** the way E15 (0.2 constant through a megametre divergence) and E19 (0.2 throughout) describe -- here it tracks the collapse -- and the divergence is a **staircase of discrete velocity spikes** rather than a smooth runaway, which is the E12 jump mode rather than the E10 `imu:1` mode. **No claim is made that `VISO_TYPE=2` or the lever arm caused any of this**; this is a single flight with several parameters changed together and no matched control, and the vertical-versus-horizontal drift comparison is on the raw, unaligned pose. |

---

## Conclusions (distilled so far)

1. **The divergence is REAL and reproducible** — not a qemu/replay artifact (E9). T1/T7 disproven.
2. **Vision-only works; IMU-fusion is catastrophic** (E10). Stereo gives metric scale from the baseline,
   so the IMU is not needed for scale in this near-field use case; "need both" is a **monocular** premise
   we don't share. The break is **in the IMU/extrinsic path** — but *which* mechanism (extrinsic / IMU
   model / time-sync / vibration) is **not isolated**, and we should stop asserting any single one.
3. **The leading architectural read (T8):** we run a redundant, low-quality inertial fusion (bad IMU,
   given authority) in front of the FC's good-IMU EKF. Natural fix: stereo VO → FC EKF; IMU only as a
   properly-weighted relative velocity factor if at all ([#59](https://github.com/symmatree/coordinator/issues/59)).
4. **The safety threat is sudden jumps, not gradual drift** (E12) — and dropping the IMU removes the
   *catastrophic* runaway but **not** the rare 1–2 m jumps. Handle those with the **existing** EKF
   innovation gate (X10), not a new filter. The IMU-fusion failure is **fail-confident** (smooth 1076 m/s),
   which is the worst mode for "in doubt, land."
5. **Drift over ice-hole-length legs looks acceptable on the proxy** (E11): ~tens of cm rms out to
   10–20 m, ~0.6 m by 40 m — within the `canopy-ops.md` mapping budget. **Optimistic floor** (GPS-good,
   open, not canopy; rigid-fit proxy, which a batch solve should match or beat).
6. **Offline replay reproduces the LIVE estimator (E16, 260712) — the methodology anchor.** For a bounded
   flight, offline `vins_fusion_offline` on the teed `.feat` matches the flight's own online `VISP` to
   **6 mm / scale 1.000**. This is what lets the offline conclusions (E9–E12) speak to *live* behaviour,
   not just an offline harness — the joint capture the whole program was gated on. Open: the *divergent*
   (megameter) regime is not yet shown to replay faithfully (260709 had no `.feat`).

**Operational consequence:** the plan that fits the evidence is **VIO = local stability** (hover +
operator-commanded deltas), **operator = global navigation** (FPV via VTX), GPS at ice-hole endpoints,
and **retroactive** GPS-anchored batch reconstruction for the map ([#59](https://github.com/symmatree/coordinator/issues/59)).
Drift is tolerated (operator + post-hoc); jumps must be handled (they break even a hover). See
`Drones/rekon10/canopy-ops.md`.

---

## Methodology confounds

- **Resolved:** qemu solver starvation (T1) and non-deterministic replay (T7) — the offline harness is
  deterministic and un-starved (E9). Provenance — the tracked runner writes a sidecar (fixture/config/
  source SHAs); the old loose `*.vinspose.csv` (no provenance) are superseded and must not be used.
- **Open — does the offline config represent the real one?** X3/E9 fix the *offline deterministic* config
  (`num_threads=1`, no Ceres wall-clock cap, stepped time, whatever host); we treat its results as the
  base but haven't shown they transfer to the live/flight config. These are **open theses, not a program**
  — no single run settles any; collect *directional indicators* and resist premature certainty. And the
  list of factors below is itself a guess (the same completeness error as "trustworthy base for
  everything") — there is very likely one we have not named (thermal throttle, memory/disk pressure; LGTM
  metrics shrink that blind spot, they don't close it):
  - *free-threaded + wall-clock-capped runs may produce a **family** of results distributed around the
    synchronous one* — falsifiable; the E15 live blow-up (~1300 km) vs offline E10 (~42 km) is a hint,
    but confounded (different flight).
  - *x86 ≡ arm64 offline for a given signal* — **RESOLVED (E19, #85).** Native amd64 (in-cluster /
    daemonless rootfs, byte-identical to each other) vs arm64-under-qemu, same image + config:
    the diverge/stays-bounded verdict is **arch-invariant**, and where the run is bounded they agree
    to **sub-mm** (0.3 mm median, 2.3 mm max on the 260705 run). The two only split once the stereo-only
    pose runs away partway through 260712 (~88 s after takeoff, mid-flight -- ~100 s before the actual
    crash; tens of m), where neither arch is truth — so this bounds cross-arch fidelity without settling
    the divergent regime (same open caveat as the `offline ≡ live` megameter case).
  - *offline ≡ live on the same host* — **RESOLVED for a bounded flight (E16, 260712).** The joint
    capture (feat #78 + onboard `VISP` + `LOG_REPLAY`) is in hand, and offline replay reproduces the
    flight's own online pose to **6 mm median / scale 1.000** — so offline results represent the live
    estimator *where the live pose is bounded*. **Still open:** whether offline reproduces a live
    *megameter divergence* (260709 had no `.feat`) — i.e. is the chaotic-divergence regime as faithful
    as the tracking regime? (Threading/solve-cap/qemu diverge more the more chaotic the run.)
- **Open:** **GPS-good open proxy ≠ canopy** (optimistic floor); **hover untested** (all flights moving);
  **metric inflation** by singular errors (check local-vs-global before trusting a global ATE); the
  **rigid-fit K-sweep** (E11) is a proxy — the batch solve (#59) is the real measurement; **cause of the
  IMU-fusion failure not isolated** (T3/T4/T5 all open).
