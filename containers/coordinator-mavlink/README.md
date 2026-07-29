# coordinator-mavlink

Forwards `vins_fusion` pose to the flight controller over UART as MAVLink2. Our own router (Python + pymavlink), seeded from [chobitsfan/mavlink-udp-proxy](https://github.com/chobitsfan/mavlink-udp-proxy) (`apm_wiki`) but now **diverged** from it: dPos/dt velocity, honest covariance, and Rekon's dual-mode GPS+VIO framing rather than the wiki's VIO-primary bench profile.

> **Design of record: [docs/coordinator-mavlink.md](../../docs/coordinator-mavlink.md)** — rationale, the divergence from the reference, the velocity/covariance model, and follow-ups. This README is the operational quick-reference.

## What it does

- Binds `/tmp/chobits_server` (the shared ipc pose socket) and reads the pose contract, length-detected: **v1** `float[10]` = quat(w,x,y,z) + pos(x,y,z) + vel(x,y,z), or **v2** `float[12]` = v1 + `reset_counter` + `feature_count`.
- Per pose, sends to the FC at `${MAVLINK_BAUD}` on `${MAVLINK_DEVICE}` (MAVLink2):
  - `VISION_POSITION_ESTIMATE` — position `(x, -y, -z)`, euler attitude, **growing** position covariance, and the `reset_counter` ([#67](https://github.com/symmatree/coordinator/issues/67): a clean EKF `ResetPositionNE` instead of a fought glitch). Position moved here from `ATT_POS_MOCAP`, which hard-codes `reset_counter=0`.
  - `VISION_SPEED_ESTIMATE` — **dPos/dt** velocity `(x, -y, -z)`, velocity covariance
  - (the `(x, -y, -z)` flip is the ENU/FLU→NED/FRD convention from the reference)
- Replies to FC `TIMESYNC` requests — a cooperative time-sync endpoint (foundation for the time-alignment work).

The velocity is **derived from the position delta / dt** ([#62](https://github.com/symmatree/coordinator/issues/62) Part 1) because the estimator's velocity field is identically zero in stereo-only mode. Position covariance is **honest and growing** — `posErr = base + drift_k * path_len` (metres travelled since the last reset/anchor), zeroed on each `reset_counter` bump; 4.7's clamp widening (10→100 m) is what makes that expressible. Velocity covariance is FC-*ignored* (fused at `VISO_VEL_M_NSE`; mirror `MAVLINK_VEL_NSE` there). Tunables `MAVLINK_VEL_NSE` (0.15 m/s, measured), `MAVLINK_POS_NSE_BASE` (0.30 m), `MAVLINK_POS_DRIFT_K` (0.02 ≈ 2%/m, seeded from E11), `MAVLINK_POS_FEAT_K` (0 = feature inflation off). No bridge-side signal filtering — ordinary spikes are the FC gate's job; a known reset suppresses only the spurious jump velocity. Full detail and the FC covariance semantics: [docs/coordinator-mavlink.md](../../docs/coordinator-mavlink.md) and [docs/ardupilot-extnav-fusion.md](../../docs/ardupilot-extnav-fusion.md).

## Proven in isolation

`test_router.py` drives the router end-to-end through a pty — feeds three spaced v2 poses (two in one reset epoch, one that bumps the reset counter), decodes the emitted bytes, and asserts the exact messages: position flip, the derived dPos/dt velocity with its flip, the growing position covariance zeroed at the reset, the forwarded `reset_counter`, velocity suppressed on the reset sample, and the TIMESYNC reply. **It runs at image build time** (`RUN python3 test_router.py`), so a wrong proxy fails the build. Run it directly with `python3 test_router.py` (needs pymavlink). This makes the proxy a known constant at the FC bench — the only remaining unknowns there are wiring and FC params. Wider end-to-end coverage (real router → fake FC over UDP): [harness/](../../harness/README.md).

## Host prerequisite

The FC link needs the Pi's primary UART freed and at high baud — the coordinator Ansible role sets `enable_uart=1` + `dtoverlay=disable-bt` (maps `/dev/serial0`→`/dev/ttyAMA0`, PL011, stable at 1.5 Mbaud) and disables the serial console/getty. Confirm the device alias on the bench. The FC side needs `SERIALn_PROTOCOL=2` and matching baud.

## CI / GHCR

`.github/workflows/build-coordinator-mavlink.yaml` builds natively on `ubuntu-24.04-arm` (running the isolation test) and pushes to `ghcr.io/symmatree/coordinator-mavlink` on push to `main`.

## Compose

Service `coordinator-mavlink` in `stacks/coordinator/compose.yaml` (`flight` profile): `network_mode: host`, mounts the ipc dir and the serial device, `depends_on: vio-estimator`. FC params and EKF lanes: [docs/ardupilot-vio.md](../../docs/ardupilot-vio.md).
