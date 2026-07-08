# ArduPilot EKF3: how it fuses ExtNav (VIO) velocity vs position

Reference for feeding VINS stereo VIO to the FC over MAVLink (`ATT_POS_MOCAP` +
`VISION_SPEED_ESTIMATE`) for GPS-denied under-canopy legs (#42). Source analysis of
`libraries/AP_NavEKF3/` + `libraries/AP_VisualOdom/`.

> **Version note.** Analyzed against ArduPilot **master** (2026-07-08). The vehicle runs
> **Copter-4.6.3** (`92b0cd78`); a local clone is at `scratchpad/ardupilot-4.6.3`. The param
> behaviour and message paths are expected to match, but **verify the line numbers against
> the 4.6.3 tag** before relying on them.

## TL;DR (the load-bearing findings)

1. **The EKF uses the per-sample covariance you put in the MAVLink message** -- for both
   velocity and position -- floored by `VISO_VEL_M_NSE` (0.1 m/s) / `VISO_POS_M_NSE` (0.2 m),
   **not** the `EK3_*_M_NSE` params (those are GPS-only). Omitting covariance -> NaN -> floored
   to the `VISO_*` value.
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

**ExtNav velocity** (`FuseVelPosNED`, `:827-831`):
`R_OBS = sq(constrain(extNavVelDelayed.err, 0.05, 50))`. `err` traces to
`GCS_Common.cpp:4167` `sqrt(VISION_SPEED_ESTIMATE.covariance[0]+[4]+[8])`, floored at
`VISO_VEL_M_NSE` (default 0.1 m/s, `AP_VisualOdom.cpp:91`), clamped `[0.05,50]`. The GPS
noise (`EK3_VELNE_M_NSE`/`EK3_VELD_M_NSE`) is the `else` branch (`:833-841`) and never runs for
VIO.

**ExtNav position** (`:844-847`): `R_OBS = sq(constrain(extNavDataDelayed.posErr, 0.01, 100))`.
`posErr` from `ATT_POS_MOCAP.covariance` (`GCS_Common.cpp:4148`), floored at `VISO_POS_M_NSE`
(default 0.2 m). Vertical ExtNav noise from `posErr` too (`:1380`), **not** `EK3_ALT_M_NSE`.

**Bottom line:** send honest covariances and they drive the Kalman gain, provided
`VISO_VEL_M_NSE`/`VISO_POS_M_NSE` are low enough not to clobber them (they are floors). The
`EK3_*_M_NSE` params do not affect VIO.

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
- **Reset counter:** propagate the VINS reset counter through
  `ATT_POS_MOCAP`/`VISION_POSITION_ESTIMATE.reset_counter` -> `posReset` ->
  `ResetPositionNE` (`PosVelFusion.cpp:651-658`). A VINS re-init (each ice-hole leg) then
  triggers a **clean** EKF position reset instead of being fought as a glitch. The current
  `coordinator-mavlink` router does not send it yet -- a worthwhile follow-up for the
  interrupted-GPS model.
- Constants (not params): `extNavVelVarAccScale=0.05` (`AP_NavEKF3.h:497`),
  `extNavIntervalMin_ms=20` (50 Hz cap, `:523`).

## Prior art

The ExtNav path targets Intel **T265** and ModalAI **VOXL** (named in the library and #23485)
plus the SITL simulated-Vicon rig (`SIM_VICON_TMASK`). Those users run continuous-VIO-as-primary
and lean on tight `VISO_*_M_NSE` + reset-counter handling; our interrupted-GPS model is more
forgiving because GPS re-acquisition provides an independent absolute reset, but the fusion
mechanics are identical.
