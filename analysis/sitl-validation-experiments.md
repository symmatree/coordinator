# Experiments: SITL scenarios and validation (does silicon match the vehicle?)

*Single place to track the claims, scenarios, and evidence for **using ArduPilot SITL (and the
FC-in-the-loop) on Linux as an instrument for the rekon10 flight stack** -- and, crucially, for
knowing **which conclusions from silicon we are allowed to carry to the vehicle, and vice versa.**
This doc is the **FC / EKF / dynamics** side; the estimator side lives in
`analysis/vio-quality-experiments.md`.*

**How this doc works** (same model as `vio-quality-experiments.md` — see its "How this doc works"): it is a **map of
belief, not a project plan**. The **Claims (A/B/C)** are the spine; each **lemma (`LA#`/`LB#`/`LC#`)** is
a specific instance a claim rests on, owning its regime, real-side anchor, and status; **scenarios
(`S#`)** are candidate probes (a backlog, uncommitted), and **evidence** is cited by ID (`E#` here or in
`vio-quality-experiments.md`). A single run rarely settles a lemma — collect directional indicators — and anchor to the
real system rather than self-certifying on synthetic input. Structure generalizes
`fables/Datasets/experiments-house-model.md` from a map to a flight stack.

**Origin:** coordinator [#64](https://github.com/symmatree/coordinator/issues/64) (SITL bench),
[#68](https://github.com/symmatree/coordinator/issues/68) (gate/GLITCH_RAD audit),
[#67](https://github.com/symmatree/coordinator/issues/67) (reset-counter),
[#65](https://github.com/symmatree/coordinator/issues/65) (GPS-anchored co-estimation),
[#63](https://github.com/symmatree/coordinator/issues/63) (Ceres tuning). Refs
[#42](https://github.com/symmatree/coordinator/issues/42).

> **UPDATE 2026-07-09 -- first live onboard flight; caveat partially retired.** `260709-vio-first-light`
> is the first VIO-in-the-loop flight: `VISO_TYPE=1`, the router's `ATT_POS_MOCAP`+`VISION_SPEED_ESTIMATE`
> reached and were **logged by the FC** (`VISP`=8420 / `VISV`=8419), the estimator ran live (~27 ms/solve),
> on a real GPS-degraded woods traverse, captured **replay-grade** (`LOG_REPLAY=1`). So **Claim C has its
> first data point and LA1 is now CONFIRMED** -- Replay reproduces the flight's own EKF to ~1 um (below).
> Remaining caveat: `EK3_SRC1=GPS` this
> flight, so VIO was received/logged but the EKF stayed **GPS-primary** -- it did not yet *depend* on VIO
> for position. The general discipline still holds: do not launder a silicon result into a vehicle claim
> without the anchor.
>
> **Original caveat (pre-2026-07-09), kept as the baseline this flight moved off:** we had **never observed
> live onboard VIO/EKF behavior** (`vio-quality-experiments.md`; commit `33cb9f9`) -- every observation was offline replay,
> so Claim C had zero evidence and Claim A could only anchor on recorded logs.
>
> **UPDATE 2026-07-30 -- the SITL ExtNav bench is built; the sim/offline cells are largely filled; the investigation is near resolution.** The ExtNav-into-SITL path is settled and working (`harness/sitl_extnav/`, [#153](https://github.com/symmatree/coordinator/pull/153)): **not** the `SIM_VICON` rig (the open question in the old next-steps) but the **real `coordinator-mavlink` router feeding `VISION_POSITION_ESTIMATE` over a SITL MAVLink serial** -- `EK3_SRC1_POSXY=6`, GPS off, `SET_GPS_GLOBAL_ORIGIN` for the datum, `--speedup 1` (SITL exits if a GCS connects under speedup). On that bench:
> - **S6 CONFIRMED (sim):** the router's output moves a real EKF3 -- injected ExtNav is fused and `LOCAL_POSITION_NED` tracks it (`probe_extnav.py`). The "does coordinator output move an EKF" milestone is met (sim only; never live).
> - **S8 CONFIRMED (sim):** reset-counter -> **one clean `ResetPositionNE`** (settles 0.052 s) vs the fought glitch when withheld (EKF still 2.1 m short of a 3 m datum jump at t+12 s) -- #67 mechanism demonstrated end-to-end through the merged router ([#149](https://github.com/symmatree/coordinator/pull/149)/#153).
> - **Yaw-align mechanism characterized ([#154](https://github.com/symmatree/coordinator/pull/154)):** stereo-only VINS has **no heading reference**, so its world-frame yaw is arbitrary per init (`vio_orientation.py`: +11.5 deg on 260728, non-cardinal) -- a static `VISO_ORIENT` cannot fix it, and the FC's GPS-anchor (`align_position_to_ahrs`) is **translation-only**. The yaw-align that rotates the VIO frame onto AHRS lives **only in the IntelT265 backend** (`VISO_TYPE=2`), not the MAV backend (`=1`) we run; SITL shows `=2` emits `VisOdom: yaw shifted ... deg`, `=1` nothing. This is a **mechanism (Claim B)** result -- correctness on our real IMU-less attitude is a **Claim-C flight**.
> - **Counterfactual-via-Replay: prerequisites confirmed, and a load-bearing matchability limit found.** `REPH`/`REVH` (5052/5051 on 260728) are present, so `Tools/Replay --parm EK3_SRC1_POSXY=6` yields the **VIO-primary counterfactual on real data** (the "next" LA1 flagged, below). **But Replay feeds `REPH` straight to `ekf3.writeExtNavData`, bypassing the VisualOdom backend -- so `VISO_TYPE` is inert in Replay.** Replay gives the *unaligned* (`=1`) arm faithfully; the *aligned* (`=2`) arm needs the backend -> SITL-live (sim-IMU confound) or a flight. **New matchability entry: the yaw-align is NOT Replay-validatable.**
> - **New real-side failure fingerprint ([#156](https://github.com/symmatree/coordinator/issues/156)):** VIO **output stalls 1-4 s in flight, ~every 5 s** (260728: 47 gaps >1 s, 6 >3 s in the armed window; corroborated by the FC's own 300 ms `VisOdom: not healthy`). A continuity failure that blocks VIO-as-position **regardless of accuracy or frame** -- and an LC3 target.
>
> **Net:** SITL is validated as an instrument (Claim A, LA1) and the ExtNav bench + sim-side mechanisms (S6, S8, yaw-align) are done in silicon. The remaining opens are **Claim-C hardware confirmations** -- flip to VIO in-frame ([#155](https://github.com/symmatree/coordinator/issues/155)), the output gaps (#156), parallel counterfactual logging ([#160](https://github.com/symmatree/coordinator/issues/160)) -- which are execution tracked in issues, not open sim investigations. The map has largely done its job.

---

## The three claims (first-order theorems)

Each is a **directional sufficiency** claim -- "sufficient **for a purpose**", never "identical". The
scenarios in the catalog are the **lemmas**; evidence fills the boxes under each.

**Read each claim as an operational assertion** (2026-07-28; the *metric x region x decision* schema from
`vio-quality-experiments.md`): *metric* -- the observable that shows silicon =~ vehicle; *region* -- the
**matchability regime** (below) that says at what level a quantity can be matched *at all*; *decision* --
what silicon result we are thereby licensed to carry to the vehicle. Two structures already in this doc
**are** that schema: the **matchability regimes** section is the region axis, and the **status vocabulary**
(Blocked / Ready / Open theory / Confirmed) is the **gap taxonomy** -- it already answers *why* a lemma
isn't nailed down: **Blocked = needs an anchor/probe**, **Ready = needs the sim built**, **Open theory =
the output is still ill-defined** (we don't yet know what "match" would even mean). So not every lemma can
be pinned now, and that is fine -- the status names *which kind of gap* each has.

- **Claim A -- Linux behaves like onboard (sufficiently).**
  SITL / Replay on x86 reproduces the real FC's EKF estimates, health, and closed-loop dynamics closely
  enough to trust a silicon result about the vehicle. *Direction: silicon -> license to conclude about
  the vehicle.* Anchored by matching **recorded** onboard behavior.
  > **Claim --** *metric:* silicon-vs-recorded EKF state/health/dynamics error (LA1: ~1 um position on real logs) · *region:* closed-loop-deterministic quantities (point-matchable); chaotic ones ensemble-only · *decision:* license to carry a silicon **EKF / gate** result to the vehicle. **Anchored (LA1 confirmed, 260709+260712).**

- **Claim B -- We can generate or derive realistic synthetic data.**
  The inputs we feed (recorded `.feat` replays, derived ExtNav with honest covariance, injected
  pathologies, a parameterized airframe model, idealized feature tracks) are faithful enough that
  conclusions drawn on them transfer. *Direction: constructed inputs -> valid conclusions.*
  > **Claim --** *metric:* does a conclusion drawn on constructed input still hold on real input? · *region:* input-realism tier -- recorded > derived-honest > synthetic-pathology > idealized (lower tiers support **mechanism/wiring only**, the self-certification trap) · *decision:* which conclusions transfer vs which only test our own reading of the code.

- **Claim C -- Onboard behaves like Linux (sufficiently, for particular use cases).**
  A thing **predicted or tuned in silicon** produces the predicted effect on the vehicle, for a named
  use case. *Direction: silicon prediction -> confirmed on hardware.* This is the payoff direction and
  today has **no evidence at all** (see the caveat).
  > **Claim --** *metric:* a silicon **prediction** confirmed on hardware (fixed before the run, falsifiable) · *region:* the use case (U1-U5) at its required fidelity · *decision:* trust a silicon-tuned change on the vehicle. **Gap = needs-confirm:** P1 (4.7 upgrade neutral for GPS-primary) is the first prediction on the record; no confirming flight yet.

Claims A and C are **not** the same statement run backwards. A licenses "silicon says X about the
vehicle"; C licenses "we changed the vehicle based on silicon and it did what silicon predicted." A can
be anchored on data we already have (recorded logs); C requires a deliberate predict-then-confirm loop
on hardware.

---

## What we want SITL for (use cases -> which claim each needs)

"Sufficiently" is meaningless without a purpose. The use cases, and the claim/fidelity each requires:

| Use case | Needs | Fidelity required |
|----------|-------|-------------------|
| U1 -- EKF gate / `GLITCH_RAD` audit ([#68](https://github.com/symmatree/coordinator/issues/68)) | A (EKF fidelity) + B (realistic ExtNav) | high: quantitative gate behavior must match |
| U2 -- Reset-counter mechanism ([#67](https://github.com/symmatree/coordinator/issues/67)) | wiring/mechanism only | low: "does one reset fire" is a causal-wiring check, realism irrelevant |
| U3 -- GPS-anchored co-estimation dev ([#65](https://github.com/symmatree/coordinator/issues/65)) | A + B | medium: integration correctness, then C to trust onboard |
| U4 -- Autotune / PID de-risking | A (dynamic twin) | medium: bifurcation threshold + hover, ensemble oscillation |
| U5 -- Failure-mode reproduction + mitigation | A + C | high: must reproduce a real failure, then confirm the fix flies |

Note the spread: U2 needs almost nothing (a mechanism check), U1/U5 need the strong form of A. Do not
quote a U2-grade result as if it validated U1.

---

## Matchability regimes (the principle that scores every lemma)

*Distilled truth, house-doc "Conclusions" style. Match each quantity at the level its physics allows;
quoting a chaotic quantity as point-matched, or a real-vehicle property as sim-predictable, is the
classic error.*

1. **Bifurcation thresholds -- point-matchable, the sweet spot.** The *location* of a regime boundary
   (the gain where control breaks into oscillation) is a robust, low-dimensional feature even when
   everything past it is chaotic. It is also a pure closed-loop property, so SITL can predict it from
   gains + inertia without any real-vehicle nuisance physics. This is why "crank the PID until it breaks"
   is the best first target.
2. **Chaotic trajectories -- ensemble-matchable only.** The full oscillation waveform is a double
   pendulum: sensitive dependence, no point match. Match the *distribution* -- dominant frequency band,
   amplitude envelope, RMS -- never the time series.
3. **Convergent estimator outputs -- split this one, it is a trap.** Two kinds, only one predictable:
   - **Closed-loop given known inputs** (steady-state EKF innovation level, notch-tracks-RPM, wind
     estimate): genuinely predictable; SITL should hit it.
   - **Property of the real physical vehicle** (mag hard/soft-iron offsets; hover throttle -- battery
     sag, mass, thermal, wind): SITL has none of that iron and no sag by default, so it **cannot predict
     the real number.** It can only validate that the calibration algorithm *converges* if you inject a
     known truth into SIM. Reproducible-as-a-process, not a reality-prediction.

**Corollary -- the self-certification trap.** A scenario whose input **and** expected output are both
authored by us (pure synthetic, no external referent) can only confirm wiring or our reading of the
code -- it supports **B (mechanism)** and nothing about onboard behavior. A "pass" may just mean the
input was too clean; a "fail" may just mean we synthesized a pathology reality never produces. Synthetic
alone never supports A or C.

**Corollary -- getting the invariant right is itself the work.** A naive whole-log FFT of an autotune log
peaks at the *maneuver / twitch-cadence envelope* (~0.4-3 Hz), not the closed-loop oscillation; the real
fingerprint only appears after **segmenting to the twitch event and band-limiting above the maneuver
band.** (`260613-vertical-bounce` is a fast vertical *climb*, not an oscillation -- an easy mislabel.)
Same lesson as the estimator replay (`vio-quality-experiments.md` T7 / the `--fast` arrival-ratio bug):
timing and windowing are load-bearing; a plausible number computed the easy way answers a different
question than you think.

---

## Validation strategy (how a lemma actually gets confirmed)

- **Anchor, then perturb by a minimal delta.** Establish an **overlap regime** where silicon and the
  vehicle are driven identically and demonstrably agree (Replay fidelity; or a live-FC bench A/B on the
  same ExtNav), *then* extend one delta out (add ExtNav; change one gate). A discrepancy is then
  attributable to the delta, not to "SITL is a different animal." Without the anchor, no silicon result
  means anything.
- **Predict-then-confirm for Claim C.** Fix the prediction (peak location, curve shape, magnitude)
  **before** the confirming runs, so the vehicle can falsify it. A confirmed prediction is stronger than
  any retrodictive fit -- it shows silicon has predictive power, not just echo. (Discipline from `vio-quality-experiments.md`:
  hold reruns able to falsify; do not launder an ad-hoc fit through a rigor process.)
- **Retro-confirm for free where the vehicle already flew the test.** Autotune already walked the
  vehicle to its stability boundary; predicting that boundary in silicon and comparing needs **zero new
  flights**. Prefer these before spending airframe risk.

---

## Scenario / lemma catalog

Grouped under the three claims. Each lemma: statement, matchability regime, real-side anchor, status.
Status vocabulary: **Blocked** (missing an anchor), **Ready** (anchor in hand, sim not yet built),
**Open theory**, **Confirmed**, **Excluded** (belongs out of this claim).

### Claim A -- Linux behaves like onboard (sufficiently)

- **LA1 -- EKF Replay fidelity (the anchor).** SITL/Replay EKF3, fed a flight's **logged** sensors,
  reproduces that flight's own logged `XKF*` position/velocity/innovations/variances. Validates that our
  bench build + params + frame conventions + analysis pipeline **are** the aircraft (not the EKF
  algorithm -- same code both sides). *Regime:* closed-loop-deterministic. *Anchor:* a `LOG_REPLAY=1`
  flight log. *Status:* **CONFIRMED (2026-07-10).** `260709-vio-first-light` is `LOG_REPLAY=1` with the
  full replay suite (`RFRH`/`RFRF` ~369k, `RISI` 737k, `RGPI`/`RGPJ`, `RMGI`, `RBRI`, and `REPH`/`REVH`/
  `RVOH` -- the ExtNav in replay form). **Fidelity result:** our x86 4.6.3 `Tools/Replay` reproduces the
  **H7-flown** 4.6.3 EKF's `XKF1` position to **~1 um over the 55 m trajectory** (rms ~0, max 0.001 mm;
  velocity to um/s; **1.7e-8 relative**) -- the bench *is* the aircraft's EKF to float precision, **across
  ARM->x86**. (`check_replay` flags only last-bit FP mismatches in the yaw-GSF/quaternion/innovation
  fields -- expected cross-arch, not divergence.) This is the first **Confirmed** lemma and the anchor the
  rest of Claim A hangs on. Note it carries the ExtNav (`VISP`=8420/`VISV`=8419) but `EK3_SRC1=GPS`
  (GPS-primary), so a Replay forcing `EK3_SRC=ExtNav` yields the VIO-primary counterfactual on the same
  real data (next).

- **LA2 -- Instability-onset gain (bifurcation threshold; first target).** SITL, parameterized to the
  airframe, goes unstable at ~the gain the real vehicle did. *Regime:* bifurcation threshold
  (point-matchable). *Anchor:* the autotune boundary in `260613-autotune-1` -- converged
  `ATC_RAT_RLL_P 0.085 / RLL_D 0.0041`, `PIT_P 0.101 / PIT_D 0.0035` at `AUTOTUNE_AGGR 0.075`, from a
  pre-tune `0.135/0.135`. *Status:* **Ready** -- retro-confirmable, zero new flights.

- **LA3 -- Stable-hover floor.** With the tuned gains, SITL holds a bounded hover; attitude RMS in the
  real ballpark. *Regime:* convergent/qualitative + coarse magnitude. *Anchor:* a low-command `ATT`
  segment from a flight (RMS to extract). *Status:* **Ready** (real-side RMS not yet extracted).

- **LA4 -- Oscillation waveform (ensemble only).** When unstable, SITL's oscillation frequency band
  overlaps the real **autotune-twitch** response band. *Regime:* chaotic -> ensemble-match only (frequency
  band + amplitude envelope, never the time series). *Anchor:* **segmented** autotune-twitch spectra
  (per-`ATDE` window). NB `260613-vertical-bounce` is a **fast vertical climb, not an oscillation** -- not
  an LA4 source. *Status:* **Ready**, pending segmented extraction.

- **LA5 -- Convergent closed-loop quantities.** Steady-state EKF innovation levels; notch tracks RPM
  (`INS_HNTCH_FREQ 58.8 Hz` at hover); wind estimate -- sim matches real given known inputs. *Regime:*
  convergent-predictable. *Status:* **Open theory**.

- **LA6 -- All-up offline reconstruction (regenerated ExtNav -> Replay EKF).** Replay a flight's captured
  `.feat` ([#78](https://github.com/symmatree/coordinator/issues/78)) through offboard VINS to
  **regenerate** ExtNav, feed it via the router (`coordinator-mavlink`) into `Tools/Replay` of the same
  flight's `LOG_REPLAY` log. This is the **join** of the two halves we have only run separately: S1/LA1
  replays the *recorded* ExtNav (CONFIRMED ~1 um); LB1/S6 regenerates pose offline; LA6 substitutes the
  regenerated ExtNav for the recorded one end-to-end -- the difference from LA1 is that ExtNav is
  *re-estimated*, not read back. *Regime:* inherits the estimator's -- so at today's signal quality
  (megameter divergence, `vio-quality-experiments.md` E15) the honest first target is **failure reproduction** (does the
  offline all-up diverge the way the flight did -> LC3), **not** fidelity; fidelity is a target only once
  an onboard run is bounded. *Anchor:* a flight captured with `.feat` **and** `LOG_REPLAY=1` **and**
  onboard pose together -- **`260712-crash` (2026-07-12) is that flight** (see "the joint capture", below).
  *Status:* **Ready (2026-07-12)** -- anchor in hand; the all-up sim (regen ExtNav -> router -> `Tools/Replay`)
  is **not yet built**. The estimator-reproducibility half is already **CONFIRMED** for the bounded window
  (`vio-quality-experiments.md` E16: offline `.feat` replay reproduces the flight's onboard VISP to **6 mm
  median / 42 mm RMSE, scale 1.000** over the first ~88 s); the post-88 s `imu:1` runaway tail is the LC3
  failure-reproduction target. `260712` also carries `VISO_TYPE=1`, `EK3_SRC1_POSXY=3`, `LOG_REPLAY=1`.

- **LA-X -- Excluded from Claim A (honesty box).** Hover throttle (`MOT_THST_HOVER` ~0.41-0.49 across
  credible runs; 0.149 in `1-notch` discarded as unconverged; it **hunts** with battery sag/mass/wind)
  and mag-cal offsets are **real-vehicle properties SITL cannot predict**. Use as coarse sanity bands /
  algorithm-convergence checks only; **do not** count a hover-throttle match as evidence for Claim A.

### Claim B -- We can generate or derive realistic synthetic data

- **LB1 -- Recorded `.feat` replay is faithful (with a timing caveat).** Replaying a recorded fixture
  through the real `vins_fusion` reproduces production behavior. *Status:* **Confirmed with caveat** --
  the deterministic offline runner is byte-reproducible (`vio-quality-experiments.md` E9/X3), but only because timing was
  removed; the earlier live path proved arrival-timing is load-bearing (`--fast` diverges; `vio-quality-experiments.md` T7).
  So "recorded replay" is faithful **only at correct pacing**.

- **LB2 -- Derived ExtNav is realistic.** Recorded VINS pose + honest covariance
  ([#66](https://github.com/symmatree/coordinator/issues/66)) + stable clock offset (`vio-quality-experiments.md` E13:
  NCC 0.95, offset std 16 ms / ~160 ppm) is representative of what the live router would emit. *Status:*
  **Ready** -- credible, not yet exercised into a real EKF.

- **LB3 -- Synthetic pathologies (the Tier-0 ceiling).** Injected 1-2 m position jumps (`vio-quality-experiments.md` E12:
  max 128 cm armed / 239 cm handheld; 99% < 10 cm), ~29 m/s dPos/dt velocity spikes (0.2%), drift, and
  reset events match measured statistics. *Regime / status:* synthetic -> supports **mechanism/wiring
  only** (self-certification trap). Good enough for U2 (reset fires) and for dialing gate arithmetic;
  **not** behavioral evidence for U1's audit conclusion. Note the FC handles the velocity spikes **by
  design** (gate-dropped, no glitch radius), so the injected-pathology work centers on **position** jumps
  (`EK3_GLITCH_RAD`), not velocity. Ceiling: only as good as the measured stats, blind to any
  timing-coupling we did not reproduce.

- **LB4 -- Synthetic airframe dynamics.** SITL's airframe model parameterized to rekon10 (mass ~2 kg,
  thrust curve from dyno data, arm length, motor/prop) is realistic. *Validated by* LA2/LA3, not on its
  own. *Key unknown:* **rotational inertia** (needs a CAD estimate or bifilar-pendulum measurement);
  hover-throttle match tests the mass/thrust half, oscillation match tests the inertia half. *Status:*
  **Open** -- model not yet built.

- **LB5 -- Idealized / counterfactual input (estimator ceiling).** Bundle-adjusted or simulated-perfect
  feature tracks characterize the estimator's ceiling decoupled from the live front end. *Status:*
  overlaps `vio-quality-experiments.md` **X8** (synthetic VI ablation, known-world simulator) and
  [#35](https://github.com/symmatree/coordinator/issues/35). Purpose is ceiling, not realism.

### Claim C -- Onboard behaves like Linux (sufficiently, for particular use cases)

*Updated 2026-07-10: no longer all empty. `260709` gave the first onboard observation, and we now have
the **first prediction on record** (below). Still: no *working* onboard VIO pose, and no *confirmed*
predict-then-confirm yet -- the flash is pending.*

> **PREDICTION P1 (2026-07-10, [#80](https://github.com/symmatree/coordinator/issues/80)) -- the 4.7
> upgrade is neutral for GPS-primary flight.** *Silicon:* `Tools/Replay` shows 4.7-beta7's EKF3 ==
> the flight's own 4.6.3 EKF3 to **1 mm / 0.01 deg** on real GPS-primary data (`260709`), on top of a
> ~1 um cross-arch fidelity floor. *Claim:* flashing 4.7-beta7 with the params transitioned (#80)
> **preserves GPS-primary flight behavior.** *Confirm:* the flash + one GPS-primary flight. *Scope:*
> mainline (GPS-primary) **only** -- the ExtNav path is **not** predicted (it wasn't exercised;
> `EK3_SRC1=GPS`). First Claim-C predict-then-confirm on the record; **falsified** if GPS-primary flight
> misbehaves post-flash. (Not a bold prediction -- a minor upgrade neutral in the mainline case -- but a
> legit one: silicon on the record before hardware.)

- **LC1 -- Param-tuning transfer.** A param swept in SITL yields the predicted effect on hardware (peak
  location, falloff shape, magnitude) over a couple of confirming runs. *First target:* the PID stability
  margin (predict boundary in SITL, confirm against fresh runs -- after LA2 retro-check passes).
  *Status:* **Open, no evidence.**

- **LC2 -- Gate / EKF safety transfer ([#68](https://github.com/symmatree/coordinator/issues/68)).**
  `GLITCH_RAD` / `POS_I_GATE` / `VEL_I_GATE` settings found safe in SITL are safe onboard, across VIO
  drift+jumps and GPS-recovery. **Narrowed by the FC mechanics** (`docs/ardupilot-extnav-fusion.md`):
  velocity has *no* glitch radius -- a single-sample spike just fails `EK3_VEL_I_GATE`, is dropped, and
  the state coasts, so the velocity-spike question is "confirm the design holds"; the **real tuning
  surface is position jumps via `EK3_GLITCH_RAD`** (past `sq(GLITCH_RAD)` -> snap/`ResetPosition`). Hard
  constraint: **velocity-only ExtNav is unsupported** ([#23485](https://github.com/ArduPilot/ardupilot/issues/23485))
  -> position must be sent (`EK3_SRC1_POSXY=6`); velocity is an optional add. *Status:* **Open, no evidence.**

- **LC3 -- Failure reproduction -> mitigation transfer.** A failure seen (or that would be seen) onboard
  -- the fail-confident IMU-fusion runaway (`vio-quality-experiments.md` E10/E12: 41.9 km, 1076 m/s), VIO divergence on
  aggressive rotation, or the `260709` "Bad Vision Position" pre-arm -- reproduced in SITL, its mitigation
  validated in SITL, and confirmed to fly. *Status:* **Open, no evidence.**

- **LC4 -- Co-estimation loop transfer ([#65](https://github.com/symmatree/coordinator/issues/65)).**
  The `globalOpt` GPS-anchored ExtNav pose behaves onboard as it did in SITL. *Status:* **Open, no
  evidence.**

---

## Scenarios and events (experiments-log analog)

One row per scenario. Fill boxes as data arrives. "Sim status" and "Onboard status" are separate columns
on purpose -- the whole discipline is not to conflate them.

| # | Scenario / event | Claim.Lemma | Regime | Real-side anchor (have?) | Sim status | Onboard status |
|---|------------------|-------------|--------|--------------------------|-----------|----------------|
| S1 | Replay a real flight's sensors through EKF3, match logged `XKF*` | A.LA1 | closed-loop | **yes** -- `260709` `LOG_REPLAY=1`+ExtNav | **CONFIRMED** (~1um) | flew (260709) |
| S2 | Crank PID gain until control breaks; compare onset to autotune boundary | A.LA2 / C.LC1 | bifurcation | **yes** -- autotune-1 boundary | not built | flew (autotune) |
| S3 | Hold a stable hover; compare attitude RMS | A.LA3 | convergent/qual | partial -- extract from `ATT` | not built | flew |
| S4 | Reproduce an oscillation band (autotune twitch) | A.LA4 | chaotic/ensemble | partial -- needs segmented spectra | not built | flew |
| S5 | Notch tracks RPM at hover (58.8 Hz) | A.LA5 | convergent-pred | **yes** -- `INS_HNTCH` + RCOU | not built | flew |
| S6 | Feed ExtNav into EKF3 via the real router; does it fuse? | B.LB2 | -- | **yes** -- tracked pose + #66 | **CONFIRMED (sim)** -- router->SITL, EKF fuses, `LOCAL_POSITION_NED` tracks (`sitl_extnav`, #153) | never live |
| S7a | Velocity spike (29 m/s) rides out `EK3_VEL_I_GATE` -- confirm the by-design single-sample drop holds | B.LB3 / C.LC2 | mechanism | **yes** -- E12 | not built | never live |
| S7b | Position jump (1-2 m): `EK3_GLITCH_RAD` snap-vs-hold -- **the real audit surface** | B.LB3 / C.LC2 | synthetic->mech | **yes** -- E12 | not built | never live |
| S8 | Reset-counter -> one clean `ResetPositionNE` | B.LB3 (mech) | mechanism | n/a (wiring) | **CONFIRMED (sim)** -- clean reset 0.052 s vs fought glitch (#153) | never live |
| S9 | globalOpt GPS-anchored pose loop | B.LB5 / C.LC4 | -- | partial -- GPS in `.bin` | not built | never live |
| S10 | IMU-fusion fail-confident runaway reproduced + mitigated | C.LC3 | -- | **yes** -- E10/E12 | not built | recorded only |
| S11 | All-up: feat -> offboard VINS regen ExtNav -> router -> Replay EKF | A.LA6 / C.LC3 | inherits estimator | **yes** -- `260712` joint capture | not built | joint capture in hand (260712) |
| S12 | `VISO_TYPE=2` (IntelT265) yaw-aligns the ExtNav frame to AHRS; `=1` (MAV) does not | B (mech) -> C | mechanism | **yes** -- 260728 `VISP` (+11.5 deg unaligned) | **mechanism CONFIRMED (sim, #154)**; **NOT Replay-testable** (backend bypassed) | never live |
| S13 | VIO-primary counterfactual: force `EK3_SRC=ExtNav` on a real `LOG_REPLAY` flight | A.LA1 extension | closed-loop | **yes** -- `REPH`/`REVH` on 260728 (5052/5051) | **Ready** (prereqs confirmed; unaligned arm only) | recorded only |

---

## Data inventory and real-side fingerprints

Grounded numbers we can define targets from **now**, from data on the NAS (`datasets/flights/rekon10/`),
no hardware:

- **Autotune boundary** (`260613-autotune-1`): converged `RLL_P 0.085 / RLL_D 0.0041`,
  `PIT_P 0.101 / PIT_D 0.0035`, `AUTOTUNE_AGGR 0.075`; pre-tune `0.135/0.135`. `ATUN` (184) + `ATDE`
  (20500) mark the twitches; `RATE`/`PIDR/P/Y` at ~230k samples each carry the response.
- **Notch:** `INS_HNTCH_FREQ 58.8 Hz` (motor fundamental at hover).
- **Hover-throttle band (sanity only):** ~0.41-0.49 (0.149 discarded); hunts within/among flights.
- **Log rate:** IMU ~400 Hz (instance 0), `GYR` faster, `VIBE` present -- rich enough for segmented
  spectra.
- **Replay anchor (`260709-vio-first-light`, 2026-07-09):** `LOG_REPLAY=1`, full replay suite +
  **ExtNav** (`VISP`=8420 / `VISV`=8419, replay-form `REPH`/`REVH`/`RVOH`); GPS status spans RTK-fixed(6)
  -> float(5) -> DGPS(4) -> 3D(3) = real under-canopy degradation; `EK3_SRC1=GPS` (GPS-primary this
  flight). The earlier 260705 logs are `LOG_REPLAY=0` (not Replay-able).
- **ExtNav ingredients:** tracked VINS pose (`*.vinspose.csv`, provenance sidecar), honest covariance
  (#66), stable clock offset (E13). `EK3_SRC1_POSXY=3` (GPS), `VISO_TYPE=0`, `AHRS_EKF_TYPE=3` in the
  flown configs.
- **SITL ExtNav config** (from `docs/ardupilot-extnav-fusion.md`): `VISO_TYPE=1`, `EK3_SRC1_POSXY=6`
  (+`VELXY=6` for velocity). The FC takes covariance from the MAVLink message, **floored** by
  `VISO_POS_M_NSE`/`VISO_VEL_M_NSE` -- keep those floors **below** the honest 0.30/0.15 or they clobber
  it. Velocity-only is unsupported (must send position). `VISO_DELAY_MS` = measured VINS->FC latency;
  MAVLink2 required; 50 Hz ExtNav cap (`extNavIntervalMin_ms=20`).
- **Bench host:** x86_64, 32 cores, ~26 TB free. ArduPilot at `/home/jovyan/ardupilot` is now at **4.7.0**
  (`1511f27194`, detached); **SITL and Replay are built** (`build/sitl/bin/arducopter`,
  `build/sitl/tool/Replay`, 2026-07-09/10). Residual: bake `MAVProxy`/`empy`/`future` into the tiles image
  per `~/AGENTS.md`.
- **Firmware target -- adopt 4.7 (decision, #64).** The stable channel dead-ends at 4.6.3 (the
  `ArduPilot-4.6` branch is +1 commit, **zero** EKF/VIO changes); the live options are **4.7.0-beta7**
  (+71 EKF/VIO commits) and master (4.8-dev). 4.7 reworked our exact path -- notably `EKF3 uses the
  **correct** extnav variances for posvel fusion` (4.6.3 clamps vel/pos err at `5`/`10`; 4.7 at `50`/`100`
  -- and `docs/ardupilot-extnav-fusion.md`, read against master, already documents the 4.7 values, so it
  becomes *correct* for the vehicle once we move), GPS-anchored VisualOdom (overlaps #65), and
  @chobitsfan's covariance-consumption fix on `ATT_POS_MOCAP` (#62/#66). With **zero on-vehicle VIO
  investment** (VIO has only ever blocked arming), there's nothing to hold back: **move the vehicle to
  4.7 and anchor SITL to 4.7-beta7** (tracking 4.7.0 stable). Bonuses for this airframe: the **Benewake
  TFS20L** rangefinder gains a driver (`RNGFND_TYPE=46`, I2C -- absent at 4.6.3, which is why it never
  worked), and `TBS_LUCID_H7` MPU6000 board-rev support is purely additive (our board is unaffected).
  Caveat: 4.7.0 is still beta7; re-verify params after flashing (new `EK3_OPTIONS`/`EK3_PRIMARY`, changed
  `FuseAllVelocities` default) and expect EKF/nav arming-gate behavior to change. **Validated offline
  (2026-07-10):** Replay on `260709` shows 4.7 EKF == 4.6.3 EKF to **1 mm / 0.01 deg** for GPS-primary --
  the upgrade is neutral for the mainline regime (Prediction P1 above; upgrade tracked in #80).

**The joint capture -- landed 2026-07-12 (`260712-crash`).** Anchoring the *estimator* half needs one flight
that captured, together: the estimator **input** (`.feat`, [#78](https://github.com/symmatree/coordinator/issues/78)),
the estimator **onboard output** (VINS pose, logged FC-side as `VISP`), and `LOG_REPLAY=1`. For a while we
had none: one `pos_log.txt` from a primitive manual log (no `.feat`); two `.feat`-captured flights (260705)
with **no onboard pose**; one all-up flight (260709) with onboard pose that **diverged to ~Mm in seconds**
(`vio-quality-experiments.md` E15) but **no `.feat`** (the in-flight tee, #78, hadn't merged yet).
**`260712-crash` closed the gap**: the tracker's `.feat` tee (#78/#83), the onboard `VISP` in the `.bin`, and
`LOG_REPLAY=1` are all present on the one run (verified from the log). We can now ask "does offboard replay
of this flight's `.feat` reproduce its onboard pose" -- and the answer, for the bounded first ~88 s, is
**yes to 6 mm median / 42 mm RMSE, scale 1.000** (`vio-quality-experiments.md` E16). So LA6/S11 move from
Blocked to Ready (the all-up regen -> router -> Replay chain is the remaining build), and the
estimator-reproducibility questions ([VIO-quality methodology confounds](vio-quality-experiments.md#methodology-confounds))
are now **anchorable**, not directional-only. The remaining forensic target is the post-88 s `imu:1` runaway
(LC3 failure reproduction). Future joint captures come for free -- both captures now ride any flight with the
estimator running (`.feat` continuous from boot, `LOG_REPLAY=1` in the recipe).

**Not yet extracted (needs the segmented-fingerprint tool):** oscillation frequency/amplitude for the
autotune twitches (band-limited above the maneuver envelope); stable-hover attitude RMS. A naive whole-log
FFT gives the maneuver/twitch-cadence envelope, not the closed-loop oscillation -- see the matchability
corollary. (`260613-vertical-bounce` is a fast vertical *climb*, not an oscillation.)

---

## Next steps (prioritized)

- [x] **DONE (2026-07-09/07-12) -- `LOG_REPLAY=1` (with `LOG_DISARMED`) in the capture recipe.** Both
  `260709` and `260712` flew `LOG_REPLAY=1`; LA1 confirmed and S1/S10 anchored.
- [ ] **Build the segmented-fingerprint tool** (`analysis/`, beside `ardupilot_log.py`): per-`ATDE`
  twitch windows, band-limited spectra, `ATUN` response amplitudes, stable-hover RMS. Produces the
  LA2/LA3/LA4 real-side targets. **Fixes the targets before fitting the sim.**
- [x] **DONE (2026-07-09/10) -- SITL + Replay built.** ArduPilot at `/home/jovyan/ardupilot` is now at
  **4.7.0** (`1511f27194`); `build/sitl/bin/arducopter` and `build/sitl/tool/Replay` are both built. (Bake
  the runtime deps into the tiles image is the only residual.)
- [ ] **Vehicle upgrade prep (#64):** generate a 4.6.3 -> 4.7 param diff (new/renamed/changed-default),
  fold in the `TFS20L` `RNGFND` setup (`RNGFND_TYPE=46`, I2C) so the on-vehicle flash is a known quantity.
- [ ] **S2 retro-confirm (LA2/LC1):** predict the instability-onset gain in SITL, compare to the autotune
  boundary. Zero new flights. First real predict-then-confirm.
- [x] **DONE (2026-07-30) -- ExtNav injection mechanism resolved.** Not `SIM_VICON`: the real
  `coordinator-mavlink` router feeds `VISION_POSITION_ESTIMATE` over a SITL MAVLink serial (`--serial5`),
  `EK3_SRC1_POSXY=6`, GPS off, `SET_GPS_GLOBAL_ORIGIN` datum, `--speedup 1`. `harness/sitl_extnav/` (#153).
- [x] **DONE (2026-07-30) -- S6 confirmed (sim).** The router's output moves a real EKF3: injected ExtNav
  fuses, `LOCAL_POSITION_NED` tracks (`probe_extnav.py`, #153). The coordinator-output-moves-an-EKF
  milestone is met; S8 (clean reset) confirmed on the same bench.
- [ ] **Run the S13 VIO-primary counterfactual (offline, no flight).** `Tools/Replay --parm
  EK3_SRC1_POSXY=6` on 260728 (`REPH`/`REVH` present) -> the VIO-driven EKF on real IMU+VIO; score vs the
  GPS-driven `XKF1` (truth). Expect **fail** (unaligned frame + #156 gaps -> "don't fly") -- the faithful
  "don't fly" baseline. NB `VISO_TYPE` is inert in Replay (S12), so this is the **unaligned** arm only.
- [ ] **VISO_TYPE=2 flight (Claim-C, the align payoff).** The aligned arm can't be replayed (S12); it needs
  the backend on real data -> a `VISO_TYPE=2` flight (GPS-primary, VIO logged; #155), or a SITL-live
  approximation (real VIO feed, sim-IMU confound). Predict: `=2` lands `VISP`->GPS rotation ~0 (vs +11.5
  deg at `=1`), no post-hoc fit.
- [ ] **Live-FC bench A/B (Claim A, no re-fly):** feed identical derived ExtNav into the real H7 and
  SITL, compare EKFs -- but **measure the delivery-timing skew** (serial vs UDP), do not assume it away.
- [ ] **Joint-capture flight (the estimator anchor).** One flight capturing `.feat` (#78) + onboard VINS
  pose (#30) + `LOG_REPLAY=1` together -- unblocks LA6/S11 and turns the estimator-reproducibility
  questions ([VIO-quality methodology confounds](vio-quality-experiments.md#methodology-confounds)) from
  directional-only into anchorable. De-risk now: it is enabling both captures on the same run, no new tooling.
- [x] **Cheap directional indicator (no new flight) -- DONE (E19, #85).** Ran the offline estimator
  x86-native vs arm64-under-qemu (same stereo-only `imu:0` config, `num_threads=1`) on the 260705 (bounded)
  and 260712 (crash) fixtures. Result: the *diverge / stays-bounded* verdict is **arch-invariant**, and
  where bounded the two agree to **sub-mm** (0.3 mm median, 2.3 mm max); they only split (to tens of m)
  once the stereo-only pose runs away partway through the flight (on 260712 ~88 s after takeoff, ~100 s
  before the actual crash). So x86 offline is a faithful stand-in for arm64 in the tracking regime.
  Native is ~14x faster (no qemu). Detail in
  [VIO-quality E19](vio-quality-experiments.md#methodology-confounds); native path per
  [`docs/vio-offline-replay.md`](../docs/vio-offline-replay.md) Options B/C.
- [ ] **Matrix-build the router (`coordinator-mavlink`).** It sits *in* the all-up loop (regenerated pose
  -> ExtNav -> FC) and is currently arm64-only, so an x86 all-up replay (LA6/S11) needs an amd64 router.
  (The tracker / oak-still-capture / pod-camera are front-end only, **not** in the replay loop -> amd64
  is dev-parity nice-to-have, not load-bearing for replay.)

---

## Cross-links

- Estimator tracker: `analysis/vio-quality-experiments.md` (T#/E#/X# ledger; X8 and X10 are the
  synthetic-simulator and EKF-gate scenarios referenced here).
- `docs/vio-offline-replay.md` -- regenerating pose from a fixture (the input to S6/S10).
- `docs/ardupilot-extnav-fusion.md` -- FC-side ExtNav fusion + covariance floors (`VISO_*_M_NSE`).
- `docs/vins-stereo-only.md` -- why the stereo-only pose drifts (no global datum).
- `harness/README.md` -- router half (`fake_fc.py`, `pose_replayer.py`) that SITL replaces the far end of.
- fables `Drones/rekon10/canopy-ops.md` -- the ice-hole error budget the whole thing serves.
