# coordinator-mavlink

Forwards `vins_fusion` pose to the flight controller over UART as MAVLink2. Our own router (Python + pymavlink) — a **faithful minimal port** of [chobitsfan/mavlink-udp-proxy](https://github.com/chobitsfan/mavlink-udp-proxy) (`apm_wiki`), the path documented to work on the ArduPilot OAK-D wiki.

## What it does (MVP)

- Binds `/tmp/chobits_server` (the shared ipc pose socket), reads `float[10]` = quat(w,x,y,z) + pos(x,y,z) + vel(x,y,z).
- Per pose, sends to the FC at `${MAVLINK_BAUD}` on `${MAVLINK_DEVICE}`:
  - `ATT_POS_MOCAP` — quaternion as-is, position `(x, -y, -z)`
  - `VISION_SPEED_ESTIMATE` — velocity `(x, -y, -z)`
  - (the `(x, -y, -z)` flip is the ENU/FLU→NED/FRD convention from the reference)
- Replies to FC `TIMESYNC` requests — a cooperative time-sync endpoint (foundation for the time-alignment work).

## Faithful port, not a redesign

Identical message-level behavior to the proven reference, with the parts that are **wrong for us removed**: the reference's `SET_GPS_GLOBAL_ORIGIN` (hardcoded foreign coordinates — and flying GPS-primary the FC already has an origin) and its planner/`LAND` command path. Deliberate follow-ups: the `SYSTEM_TIME`→chrony feed, in-flight pose logging ([#30](https://github.com/symmatree/coordinator/issues/30)), and a GPS-denied origin handshake.

## Proven in isolation

`test_router.py` drives the router end-to-end through a pty — feeds a known pose, decodes the emitted bytes, asserts the exact messages, values, and axis flip, and checks the TIMESYNC reply. **It runs at image build time** (`RUN python3 test_router.py`), so a wrong proxy fails the build. Run it directly with `python3 test_router.py` (needs pymavlink). This makes the proxy a known constant at the FC bench — the only remaining unknowns there are wiring and FC params.

## Host prerequisite

The FC link needs the Pi's primary UART freed and at high baud — the coordinator Ansible role sets `enable_uart=1` + `dtoverlay=disable-bt` (maps `/dev/serial0`→`/dev/ttyAMA0`, PL011, stable at 1.5 Mbaud) and disables the serial console/getty. Confirm the device alias on the bench. The FC side needs `SERIALn_PROTOCOL=2` and matching baud.

## CI / GHCR

`.github/workflows/build-coordinator-mavlink.yaml` builds natively on `ubuntu-24.04-arm` (running the isolation test) and pushes to `ghcr.io/symmatree/coordinator-mavlink` on push to `main`.

## Compose

Service `coordinator-mavlink` in `stacks/coordinator/compose.yaml` (`flight` profile): `network_mode: host`, mounts the ipc dir and the serial device, `depends_on: vio-estimator`. FC params and EKF lanes: [docs/ardupilot-vio.md](../../docs/ardupilot-vio.md).
