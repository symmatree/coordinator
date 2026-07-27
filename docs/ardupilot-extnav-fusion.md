# ArduPilot EKF3: how it fuses ExtNav (VIO) velocity vs position

Reference for feeding VINS stereo VIO to the FC over MAVLink (`ATT_POS_MOCAP` +
`VISION_SPEED_ESTIMATE`) for GPS-denied under-canopy legs (#42). Source analysis of
`libraries/AP_NavEKF3/` + `libraries/AP_VisualOdom/`.

> **Version note.** Originally analyzed against ArduPilot **master** (2026-07-08) with the
> vehicle on **Copter-4.6.3**. Re-verified against the **Copter-4.7.0** (`1511f271`) tag
> after the FC upgrade (coordinator #80); line numbers below are 4.7.0. Two things the
> earlier master analysis got wrong are corrected inline: the FC does **not** consume the
> velocity covariance, and the position `posErr` formula changed from a broken cbrt to
> `sqrt` (4.6.3 -> 4.7). See the covariance section.

## TL;DR (the load-bearing findings)

1. **Position uses the per-sample covariance you put in `ATT_POS_MOCAP`** (floored by
   `VISO_POS_M_NSE` 0.2 m, capped at 100 m), **not** the `EK3_*_M_NSE` params (those are
   GPS-only). **Velocity does NOT** -- the FC ignores `VISION_SPEED_ESTIMATE.covariance` and
   fuses at the `VISO_VEL_M_NSE` param regardless. So position is the only per-sample honest
   channel; velocity noise is a fixed FC param.
2. **Velocity-only ExtNav is unsupported** (issue #23485 open). You **must** send position to
   get an aiding lock; velocity alone leaves the filter in `AID_NONE` -> EKF position failsafe.
   Position + velocity together is supported and recommended.
3. **Velocity spikes are gate-and-dropped** (robust to single samples); **position jumps** are
   bounded/reset by `EK3_GLITCH_RAD`.
4. **`VISO_TYPE` must be non-zero** (1 = MAV) or the messages are dropped entirely.

## Data path (MAVLink -> EKF), two separate channels

- `ATT_POS_MOCAP` -> `GCS_Common.cpp:4133` -> `AP_VisualOdom_MAV::handle_pose_estimate`
  (`AP_VisualOdom_MAV.cpp:28`) -> `writeExtNavData` (`AP_NavEKF3_Measurements.cpp:1083`).
- `VISION_SPEED_ESTIMATE` -> `GCS_Common.cpp:4157` -> `handle_vision_speed_estimate`
  (`AP_VisualOdom_MAV.cpp:71`) -> `writeExtNavVelData` (`Measurements.cpp:1137`, sets
  `useExtNavVel=true` `:1150`).

Both require the `AP_VisualOdom` backend, created only when **`VISO_TYPE != 0`** (1 = MAV).
Pulled at the fusion horizon in `SelectVelPosFusion` (`AP_NavEKF3_PosVelFusion.cpp:519`), both
fuse through `FuseVelPosNED` (`:762`) over `velPosObs[] = {velN,velE,velD,posN,posE,posD}`.

## Gates (shared with GPS names)

- **Position:** `EK3_POS_I_GATE` (`_gpsPosInnovGate`, default 500 = 5 sigma,
  `AP_NavEKF3.cpp:35,212`). Test `:918-922`; rejected -> `fusePosData=false` (`:970`), state
  coasts on IMU. `EK3_GLITCH_RAD<=0` inflates variance instead of rejecting (`:925-934`).
- **Velocity:** `EK3_VEL_I_GATE` (`_gpsVelInnovGate`, default 500, `:34,195`). Test `:994-996`;
  rejected -> `fuseVelData=false` (`:1026`).
- **Height:** `EK3_HGT_I_GATE` (`:1037`). There is **no ExtNav-specific gate**.

## Where the measurement covariance comes from (the key finding)

**ExtNav velocity** (`FuseVelPosNED`): `R_OBS[0]/[2] = sq(constrain(extNavVelDelayed.err, 0.05, 50))`
(`PosVelFusion.cpp:732`, 4.7.0; the clamp was `[0.05, 5]` in 4.6.3). But `err` is **not** derived
from the message: `handle_vision_speed_estimate` forwards no covariance and the MAV backend calls
`writeExtNavVelData(vel, get_vel_noise(), ...)` (`AP_VisualOdom_MAV.cpp:79`), so
`err == VISO_VEL_M_NSE` (0.1 m/s default). **`VISION_SPEED_ESTIMATE.covariance` is ignored**
(confirmed by upstream [PR #14516](https://github.com/ardupilot/ardupilot/pull/14516): "I do not
send covariance from mavlink msg"). The GPS noise (`EK3_VELNE_M_NSE`/`EK3_VELD_M_NSE`) is the
`else` branch and never runs for VIO.

**ExtNav position** (`PosVelFusion.cpp:748`, 4.7.0):
`R_OBS[3]/[4] = sq(constrain(posErr, 0.01, 100))` -- the clamp was `[0.01, 10]` in 4.6.3, and 4.7
widening it 10x is what lets an honest *growing* posErr through. `posErr =
sqrt(ATT_POS_MOCAP.covariance[0]+[6]+[11])` (`GCS_Common.cpp:4134`), then floored at
`VISO_POS_M_NSE` (0.2 m) and capped at 100 in the backend (`AP_VisualOdom_MAV.cpp:42`).
**Formula change:** 4.6.3 used `cbrtf(sq(cov[0])+sq(cov[6])+sq(cov[11]))` -- dimensionally broken
(m^4/3); 4.7 is the `sqrt` above. A router sending `sigma^2` per diagonal gets `posErr ~= sigma`
on 4.6.3 but `sqrt(3)*sigma` on 4.7, so send `sigma^2 / 3` per diagonal for `posErr == sigma`.

**Bottom line:** POSITION covariance drives the Kalman gain per sample (floored by
`VISO_POS_M_NSE`, capped 100 m) -- the honest, per-sample channel. VELOCITY noise is the fixed
`VISO_VEL_M_NSE` param regardless of what the message carries. The `EK3_*_M_NSE` params do not
affect VIO.

**As implemented (`coordinator-mavlink`, #62 Part 1 + the 4.7 fix):** the router sends a
per-sample position covariance and a (currently FC-ignored) velocity covariance. Position
defaults to `MAVLINK_POS_NSE=0.30` m (conservative placeholder, above the 0.2 floor, pending
SITL/flight tuning); velocity to `MAVLINK_VEL_NSE=0.15` m/s (measured; dPos/dt vs FC EKF) -- but
because the FC ignores the velocity covariance, that value must be mirrored into `VISO_VEL_M_NSE`
on the FC to take effect. Both covariances put `sigma^2 / 3` on each diagonal so that under the
FC's `sqrt(sum-of-variances)` collapse the effective per-axis noise equals the intended `sigma`
(not `sqrt(3)*sigma`); `ATT_POS_MOCAP.covariance` (row-major upper-triangle, states
x,y,z,roll,pitch,yaw) carries the position variances on indices 0/6/11.

## Velocity-only ExtNav: not supported standalone (#23485 OPEN)

To fuse velocity as a real aid the filter must enter an aiding mode; `readyToUseExtNav()`
(`Control.cpp:611-618`) requires `getPosXYSource == EXTNAV`. With `POSXY=None, VELXY=ExtNav`
the filter never leaves `AID_NONE`, the velocity slot is overwritten by synthetic zero-velocity
(`SelectVelPosFusion:692-712`), and Copter throws an EKF position failsafe. ExtNav velocity is
NED-frame so it does not qualify via the body-frame `readyToUseBodyOdm` path either.
**Practical: send position (`ATT_POS_MOCAP`); optionally + velocity (`POSXY=ExtNav` &&
`VELXY=ExtNav`), which is the supported/recommended combination.**

## Glitch/jump handling: position vs velocity

- **Position:** `EK3_GLITCH_RAD` (default 25 m). A sustained offset inflates position variance;
  past `sq(EK3_GLITCH_RAD)` (`:942`) the filter **snaps to the sensor** (`ResetPosition`, `:945`)
  and reseeds covariance (`:952-953`). Larger radius -> tolerate bigger jumps but drift further
  before correcting.
- **Velocity:** no glitch-radius. A **transient single-sample spike** fails `EK3_VEL_I_GATE`,
  is dropped (`:1026`), state coasts, next in-bounds sample fuses -- one bad sample has almost
  no effect. A **sustained** bias eventually trips `velTimeout` -> `ResetVelocity` (`:1016`).

So velocity naturally rides out isolated VIO spikes; position needs `EK3_GLITCH_RAD` tuning.

## Recommended params + VINS re-init handling

- Enable: **`VISO_TYPE=1`** (MAV). Tune `VISO_POS_M_NSE`, `VISO_VEL_M_NSE` (the covariance
  floors), and **`VISO_DELAY_MS`** = true VINS->FC latency (default 10 ms; measure ours).
- Sources: `EK3_SRCn_POSXY=6` (ExtNav) required; add `VELXY=6` when a real velocity exists;
  `POSZ`, `YAW` per environment. `EK3_POS_I_GATE`/`EK3_VEL_I_GATE`/`EK3_GLITCH_RAD` are the
  consistency/reset knobs (shared across sources).
- **Reset counter:** propagate the VINS reset counter -> `posReset` ->
  `ResetPositionNE` (`PosVelFusion.cpp:651-658`). A VINS re-init (each ice-hole leg) then
  triggers a **clean** EKF position reset instead of being fought as a glitch. The current
  `coordinator-mavlink` router does **not** send it yet -- and it needs two upstream changes,
  not just a router tweak: (1) the `float[10]` `chobits_server` datagram carries no reset
  counter, so the estimator/tap must plumb it through; (2) **`ATT_POS_MOCAP` has no
  `reset_counter` field** in the MAVLink dialect (pymavlink 2.4.49) -- only
  `VISION_POSITION_ESTIMATE` does, so position-reset propagation means switching that message
  (`VISION_SPEED_ESTIMATE` does carry `reset_counter`, but that resets velocity, not position).
  A worthwhile follow-up for the interrupted-GPS model.
- Constants (not params): `extNavVelVarAccScale=0.05` (`AP_NavEKF3.h:497`),
  `extNavIntervalMin_ms=20` (50 Hz cap, `:523`).

## Prior art

The ExtNav path targets Intel **T265** and ModalAI **VOXL** (named in the library and #23485)
plus the SITL simulated-Vicon rig (`SIM_VICON_TMASK`). Those users run continuous-VIO-as-primary
and lean on tight `VISO_*_M_NSE` + reset-counter handling; our interrupted-GPS model is more
forgiving because GPS re-acquisition provides an independent absolute reset, but the fusion
mechanics are identical.
