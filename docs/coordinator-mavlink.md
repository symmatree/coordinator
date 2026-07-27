# coordinator-mavlink -- design

The coordinator's MAVLink router: it takes the `vins_fusion` pose off the local
IPC socket and feeds the ArduPilot flight controller a visual-odometry estimate
it can fuse. Source: [`containers/coordinator-mavlink/`](../containers/coordinator-mavlink/).
FC-side fusion mechanics (EKF3 sources, covariance handling, gates): [ardupilot-extnav-fusion.md](ardupilot-extnav-fusion.md).
Where it sits in the process graph: [vio-integration.md](vio-integration.md).

## Why this is no longer "the normal chobits deployment"

The router began as a faithful minimal port of
[chobitsfan/mavlink-udp-proxy](https://github.com/chobitsfan/mavlink-udp-proxy)
(`apm_wiki`) -- the path the ArduPilot OAK-D wiki documents as working. That
reference targets a **VIO-primary bench profile**: one estimator, feeding the FC
an absolute pose, with the FC configured to trust it.

Rekon's problem is different. VIO is a **bounded GPS-denied fallback** for
under-canopy legs between "ice-hole" GPS resets, running *alongside* GPS, with the
OAK-D in **stereo-only** mode (IMU fusion runs away to km; stereo-only tracks
~1 m ATE -- see [vins-stereo-only.md](vins-stereo-only.md)). That reframing drives
concrete divergences from the reference, each of which is a deliberate design
decision rather than a port artifact:

| Concern | chobits reference | coordinator-mavlink |
|---------|-------------------|---------------------|
| Velocity | forwards estimator velocity | **derives dPos/dt** (estimator velocity is 0 in stereo-only) |
| Covariance | omitted (FC floors it) | **honest per-sample covariance** on both messages |
| GPS origin | `SET_GPS_GLOBAL_ORIGIN` (hardcoded coords) | dropped -- flying GPS-primary, the FC already has an origin |
| Planner / LAND | command path present | dropped -- not our control model |
| Wire version | v1/v2 as built | **MAVLink2 required** (covariance/reset are v2 extensions) |

The router stays a thin, stateless-ish bridge (one small piece of state: the
previous pose, for differencing). It does **no signal filtering** -- spikes are the
FC's job to gate. But it is now a component we own and reason about, not a vendored
proxy, and this doc is the design of record.

## What it does

Reads `float[10]` pose datagrams from the AF_UNIX socket `/tmp/chobits_server`
(quat `w,x,y,z` + pos `x,y,z` + vel `x,y,z`, the `vins_fusion` output contract) and,
per pose, sends the FC over UART as MAVLink2:

- `ATT_POS_MOCAP` -- quaternion as-is, position `(x, -y, -z)`, position covariance.
- `VISION_SPEED_ESTIMATE` -- dPos/dt velocity `(x, -y, -z)`, velocity covariance.

It also answers FC `TIMESYNC` requests, so the link is a cooperative time-sync
endpoint (foundation for the pose/GPS time-alignment work, #65).

### Frame convention

The `(x, -y, -z)` flip is the ENU/FLU -> NED/FRD convention inherited from the
reference. Velocity is differenced in the estimator (ENU) frame and then given the
**same** flip as position, so the two messages are consistent by construction
(`d(flip(pos))/dt == flip(d(pos)/dt)`).

## Velocity: dPos/dt (#62 Part 1)

The estimator's velocity field is **identically zero** in the recommended
stereo-only config, so forwarding it is useless. Instead the router computes
velocity from the position delta between consecutive poses. The bridge owns this:
it is a pure function of the positions we already send, so it cannot disagree with
the position stream.

Validation (analysis in #62): dPos/dt tracks the FC EKF velocity to **~0.15 m/s
1sigma** (median 8 cm/s), and the error is **stationary** -- differencing removes
the VIO position drift, so there is no time/distance trend and a **fixed**
covariance is defensible. A small fraction (~0.2%) of single-to-few-sample spikes
reach ~29 m/s; these are passed through deliberately for the FC innovation gate to
reject (a transient spike fails `EK3_VEL_I_GATE`, is dropped, and the state coasts
-- see [ardupilot-extnav-fusion.md](ardupilot-extnav-fusion.md)).

**dt source.** The pose datagram carries no timestamp, so dt is measured from the
router's monotonic receipt clock (`time.monotonic()`), not wall time (immune to NTP
steps). The MAVLink message timestamp stays wall-clock `time_usec` (what the FC
expects for `VISO_DELAY_MS` alignment).

**Guards (numerical, not signal).** The first pose emits no velocity (no prior).
A dt below `MIN_DT` (1 ms) skips velocity for that sample -- differencing two
near-simultaneous poses amplifies position noise into a bogus velocity. At real
camera cadence (tens of ms) this never triggers; it only fires on duplicate/burst
samples. Large-dt gaps need no guard: a large dt makes the velocity *small*, not a
spike, so it is self-limiting.

## Covariance: honest, and floored not clobbered

The two channels are **asymmetric** -- verified against the Copter-4.7.0 tag:

- **Position: per-sample, honest.** The FC consumes `ATT_POS_MOCAP.covariance`
  (`posErr = sqrt(cov[0]+cov[6]+cov[11])`, `GCS_Common.cpp:4134`), floored at
  `VISO_POS_M_NSE` (0.2 m) and capped at 100 m, and uses it as the position
  observation noise -- **not** the `EK3_*_M_NSE` params (those are GPS-only). This is
  a real per-sample channel and the natural home for VIO's growing integrated
  uncertainty.
- **Velocity: covariance IGNORED.** The FC does not read
  `VISION_SPEED_ESTIMATE.covariance`; `handle_vision_speed_estimate` forwards no
  covariance and the MAV backend fuses at the FC param `VISO_VEL_M_NSE`
  (`writeExtNavVelData`, `AP_VisualOdom_MAV.cpp:79`), confirmed by upstream
  [PR #14516](https://github.com/ardupilot/ardupilot/pull/14516) ("I do not send
  covariance from mavlink msg"). So the velocity noise the EKF uses is
  `VISO_VEL_M_NSE`, and to change it you set that FC param -- what the router sends is
  advisory/logged only.

Full FC-side derivation: [ardupilot-extnav-fusion.md](ardupilot-extnav-fusion.md).

Two knobs, with honest provenance:

| Env | Default | Provenance |
|-----|---------|------------|
| `MAVLINK_VEL_NSE` | 0.15 m/s | **Measured** (dPos/dt vs FC EKF velocity, stationary error). FC-ignored (see above) -- mirror it into `VISO_VEL_M_NSE` on the FC for it to take effect. |
| `MAVLINK_POS_NSE` | 0.30 m | **Conservative placeholder** -- not independently measured; kept above the 0.2 m floor so it binds, pending SITL/flight tuning (#62 Part 2, #64) |

Keep `VISO_POS_M_NSE` below `MAVLINK_POS_NSE` or the floor clobbers it.

**Position encoding (the one the FC uses).** `ATT_POS_MOCAP.covariance` is the
21-element row-major upper triangle of the 6x6 pose covariance (states
x,y,z,roll,pitch,yaw). The router fills the position variances on the x/y/z diagonal
(indices 0/6/11); attitude entries stay 0 (mocap yaw is unused with
`EK3_SRC_YAW=compass`). The FC collapses the diagonal to `posErr =
sqrt(cov[0]+cov[6]+cov[11])`, so to make the effective per-axis noise equal
`MAVLINK_POS_NSE` the router puts `sigma^2 / 3` on each entry -- **not** `sigma^2`
(which yields `sqrt(3)*sigma`).

> **4.6.3 -> 4.7 formula change.** 4.6.3 computed `posErr` as
> `cbrtf(sq(cov[0])+sq(cov[6])+sq(cov[11]))` -- dimensionally broken (m^4/3), under
> which the old `sigma^2`-per-axis encoding landed near `sigma` by luck. 4.7 fixed it
> to `sqrt(sum-of-variances)`; under 4.7 the old encoding gives `sqrt(3)*sigma`, so
> the `sigma^2 / 3` split above is required to hit the intended value.

**Velocity encoding.** The router still fills `VISION_SPEED_ESTIMATE.covariance`
(9-element 3x3, `sigma^2 / 3` per diagonal) on the same convention, but the FC
ignores it (above) -- it is advisory/logged only until an FC that consumes it ships.

## Time sync

The router replies to FC-initiated `TIMESYNC` requests (`tc1 == 0`) with its own
nanosecond clock, echoing `ts1`. This makes the coordinator a passive time-sync
peer now; the active side (disciplining a shared epoch across GPS/VINS/FC for the
GPS-anchored co-estimation in #65) is future work.

## Not done here -- deliberate follow-ups

- **VINS reset-counter propagation.** A VINS re-init (each ice-hole leg) should
  trigger a *clean* EKF position reset (`posReset -> ResetPositionNE`) instead of
  being fought as a glitch. Two upstream changes are needed, so it is out of scope
  for a router-only change: (1) the `float[10]` datagram carries no reset counter,
  so the estimator/tap must plumb it through; (2) **`ATT_POS_MOCAP` has no
  `reset_counter` field** in the dialect -- position-reset propagation means moving
  to `VISION_POSITION_ESTIMATE` (`VISION_SPEED_ESTIMATE` carries `reset_counter`,
  but that resets velocity, not position). Detail in
  [ardupilot-extnav-fusion.md](ardupilot-extnav-fusion.md).
- **GPS-anchored co-estimation feed** (#65): fuse GPS on our side (port `globalOpt`)
  and hand the FC a non-drifting, pre-anchored pose, so intermittent RTK "bubbles"
  bound VINS drift. Needs the shared-clock work above.
- **`SYSTEM_TIME` -> chrony** feed, in-flight pose logging (#30), Pi Zero pod
  relay + obstacle-distance MAVLink, GPS-denied origin handshake.

## Configuration

| Env / arg | Default | Meaning |
|-----------|---------|---------|
| `MAVLINK_DEVICE` / `--device` | `/dev/ttyAMA0` | FC UART (or `udpout:host:port` for the harness). Deployed stack uses the **real node `/dev/ttyAMA0`**, not the `/dev/serial0` symlink -- Docker `devices:` does not follow symlinks, so a `serial0` mapping never appears in the container and the router crash-loops on ENOENT (bench 2026-07-09). |
| `MAVLINK_BAUD` / `--baud` | 1500000 | UART baud (FC `SERIAL4_BAUD=1500`) |
| `MAVLINK_POSE_SOCKET` / `--socket` | `/tmp/chobits_server` | pose IPC datagram socket |
| `MAVLINK_SRC_SYSTEM` / `--source-system` | 1 | MAVLink source system id |
| `MAVLINK_SRC_COMPONENT` / `--source-component` | `MAV_COMP_ID_VISUAL_INERTIAL_ODOMETRY` | MAVLink source component |
| `MAVLINK_VEL_NSE` / `--vel-nse` | 0.15 | velocity 1sigma sent to FC (m/s) |
| `MAVLINK_POS_NSE` / `--pos-nse` | 0.30 | position 1sigma sent to FC (m) |

`MAVLINK20=1` is set in-process before the `pymavlink` import (pinned 2.4.49) --
required to expose the covariance/reset_counter extension fields *and* to put v2
frames on the wire so the extensions actually serialize.

Host prerequisite (Pi UART freed + high baud) and FC wiring: [ardupilot-vio.md](ardupilot-vio.md).

## Testing (hardware-free)

Two levels, both runnable with no FC and no OAK-D:

- **Isolation test** -- `containers/coordinator-mavlink/test_router.py`. Spawns the
  real router over a pty, drives two spaced poses, and asserts the decoded
  `ATT_POS_MOCAP` / `VISION_SPEED_ESTIMATE`: position flip + quaternion, the derived
  dPos/dt velocity with its flip, the covariances (position `= pos_nse^2`, velocity
  FC-scalar `= vel_nse`), and the TIMESYNC reply. **Runs at image build time**
  (`RUN python3 test_router.py`), so a broken bridge fails the build.
- **Seam harness** -- `harness/test_router_stack.py` drives the real router over
  `udpout` into a fake FC (`harness/fake_fc.py`), exercising the two seams the
  router owns (the pose byte-contract and the outgoing MAVLink) end to end. Runs in
  CI (`.github/workflows/stack-smoke.yaml`). See [harness/README.md](../harness/README.md).

## Related

- [ardupilot-extnav-fusion.md](ardupilot-extnav-fusion.md) -- FC-side EKF3 fusion, covariance, gates.
- [ardupilot-vio.md](ardupilot-vio.md) -- wiring, UART, Rekon dual-mode context.
- [vio-integration.md](vio-integration.md) -- process graph and IPC sockets.
- [vins-stereo-only.md](vins-stereo-only.md) -- why stereo-only, and why velocity is 0.
- Issues: #62 (this work), #64 (SITL bench), #65 (GPS-anchored co-estimation), #10 (MVP/first FC run).
