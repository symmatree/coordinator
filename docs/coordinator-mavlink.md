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

Reads pose datagrams from the AF_UNIX socket `/tmp/chobits_server`, length-detected
across two contract versions (the `vins_fusion` output contract):

- **v1** `float[10]`: quat `w,x,y,z` + pos `x,y,z` + vel `x,y,z`.
- **v2** `float[12]`: v1 + `reset_counter` + `feature_count` (appended, so a v1 reader
  still finds quat/pos/vel at the same offsets). Emitted by the coordinator estimator
  overlay (`pubOdometry` / `main_offline.cpp`).

Per pose it sends the FC over UART as MAVLink2:

- `VISION_POSITION_ESTIMATE` -- position `(x, -y, -z)`, euler attitude, **growing**
  position covariance, and the `reset_counter`. Position moved here from
  `ATT_POS_MOCAP` (which hard-codes `reset_counter=0` at `GCS_Common.cpp:4139`) so a
  VINS re-init drives a clean EKF `ResetPositionNE` instead of being fought as a
  glitch (#67). Attitude is euler and fusion-inert under our config
  (`EK3_SRC_YAW=compass`); see the frame note.
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

- **Position: per-sample, honest, and now GROWING.** The FC consumes
  `VISION_POSITION_ESTIMATE.covariance` (`posErr = sqrt(cov[0]+cov[6]+cov[11])`,
  `GCS_Common.cpp:4134`), floored at `VISO_POS_M_NSE` (0.2 m) and capped at 100 m,
  and uses it as the position observation noise -- **not** the `EK3_*_M_NSE` params
  (those are GPS-only). VIO's integrated position uncertainty grows without bound
  under canopy, so the router sends `posErr = base + drift_k * path_len` (metres
  travelled since the last reset/anchor), zeroed on each `reset_counter` bump. 4.7's
  clamp widening (10 m -> 100 m) is what lets this honest growth reach the EKF
  instead of saturating. Per-sample health input `feature_count` is available but its
  covariance term is off by default (`MAVLINK_POS_FEAT_K=0`) -- the
  feature-count->error link is unproven (#124).
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
| `MAVLINK_POS_NSE_BASE` | 0.30 m | Position 1sigma at a fresh anchor/reset -- conservative placeholder, above the 0.2 m floor so it binds. (`MAVLINK_POS_NSE` is accepted as a back-compat alias.) |
| `MAVLINK_POS_DRIFT_K` | 0.02 | Position 1sigma growth per metre travelled since reset (~2%); seeded from the E11 drift-vs-distance K-sweep -- refine from flight residuals (#62 Part 2, #64). |
| `MAVLINK_POS_NSE_MAX` | 100.0 m | Cap on position 1sigma; the FC honours up to 100 m on 4.7. |
| `MAVLINK_POS_FEAT_K` | 0.0 | Feature-starvation inflation gain; **disabled** by default (causal link unproven, #124). When >0: `posErr += k / max(feature_count, 1)`. |

Keep `VISO_POS_M_NSE` below `MAVLINK_POS_NSE_BASE` or the floor clobbers the base.

**Position encoding (the one the FC uses).** `VISION_POSITION_ESTIMATE.covariance`
is the 21-element row-major upper triangle of the 6x6 pose covariance (states
x,y,z,roll,pitch,yaw) -- same layout as `ATT_POS_MOCAP`. The router fills the
position variances on the x/y/z diagonal (indices 0/6/11); attitude entries stay 0
(VIO yaw is unused with `EK3_SRC_YAW=compass`). The FC collapses the diagonal to
`posErr = sqrt(cov[0]+cov[6]+cov[11])`, so to make the effective per-axis noise equal
the modelled `posErr` (`base + drift_k * path_len`) the router puts `posErr^2 / 3` on
each entry -- **not** `posErr^2` (which yields `sqrt(3)*posErr`).

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

Every exchange is appended as one JSON object to `COORD_TIMESYNC_LOG`
(`/tmp/timesync.jsonl` by default; the compose stack points it at
`/captures/timesync.jsonl` so a flight pull carries it):

| field | what |
|-------|------|
| `fc_ts1_ns` | the FC's own clock, echoed back from its request |
| `tc1_realtime_ns` | the `CLOCK_REALTIME` value we replied with |
| `monotonic_ns` | `CLOCK_MONOTONIC`, read next to the reply |

`monotonic_ns` is the load-bearing field. The OAK-D capture sidecars stamp each
frame with `monotonic_ns`, and every container runs `network_mode: host` so they
share the host kernel's clock -- so these lines are what places a still on the FC
log's timeline. Without them the two clocks are only relatable through wall time,
which is where the ~5 s capture/flight-timeline discrepancy of
[#167](https://github.com/symmatree/coordinator/issues/167) lives. The file is
append-only across sessions; slice it by `monotonic_ns` for a given flight.

## Not done here -- deliberate follow-ups

- **VINS reset-counter propagation -- DONE (contract v2, #67).** A VINS re-init now
  bumps `reset_counter` in the estimator (`chobits_reset_counter`, on the failure
  re-init), the datagram carries it (v2), and the router forwards it on
  `VISION_POSITION_ESTIMATE` -> clean EKF `posReset -> ResetPositionNE` instead of a
  fought glitch. This required the two upstream changes noted before: widening the
  IPC datagram (`float[10]` -> `float[12]`) and moving position off `ATT_POS_MOCAP`
  (which hard-codes `reset_counter=0`) onto `VISION_POSITION_ESTIMATE`. The reset also
  zeroes the growing-covariance path accumulator and suppresses the spurious jump
  velocity. Behaviour under the FC EKF is still to be validated in SITL (#64/#68).
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
| `MAVLINK_POS_NSE_BASE` / `--pos-nse-base` | 0.30 | position 1sigma at a fresh anchor/reset (m); `MAVLINK_POS_NSE` is a back-compat alias |
| `MAVLINK_POS_DRIFT_K` / `--pos-drift-k` | 0.02 | position 1sigma growth per metre travelled since reset |
| `MAVLINK_POS_NSE_MAX` / `--pos-nse-max` | 100.0 | cap on position 1sigma (m) |
| `MAVLINK_POS_FEAT_K` / `--pos-feat-k` | 0.0 | feature-starvation inflation gain (0 = off) |

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
