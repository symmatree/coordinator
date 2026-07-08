# ArduPilot OAK-D VIO

Coordinator-side VIO feeding the TBS Lucid H7. Upstream build reference: [Luxonis OAK-D -- Copter](https://ardupilot.org/copter/docs/common-vio-oak-d.html). Process wiring and socket IPC: [vio-integration.md](vio-integration.md). Containers and bench profile: [architecture.md](architecture.md).

## Wiring (flight)

- OAK-D: Pi USB 3.0 (`vio-tracker` container).
- FC MAVLink: Pi primary UART -> FC **SERIAL4**. Pi side is `/dev/serial0` = `/dev/ttyAMA0` (PL011, via `enable_uart=1` + `disable-bt`; the coordinator Ansible role sets this): **GPIO14/TXD (header pin 8) -> FC RX, GPIO15/RXD (pin 10) -> FC TX, common GND (pin 6)**. MAVLink2 at 1.5 Mbaud -> FC `SERIAL4_PROTOCOL=2`, `SERIAL4_BAUD=1500000`. Not yet wired: `SERIAL4` is deliberately left at `None` (like the other unused ports) until the cable exists, so a disconnected port doesn't invite phantom-link troubleshooting. Set the protocol/baud when it's cabled.

## What the coordinator sends

The **coordinator MAVLink router** (`coordinator-mavlink`) publishes visual-odometry MAVLink to the FC: `ATT_POS_MOCAP` (position + covariance) and `VISION_SPEED_ESTIMATE` (dPos/dt velocity + covariance), plus a `TIMESYNC` reply. It is seeded from chobitsfan `mavlink_udp` but diverged -- design of record: [coordinator-mavlink.md](coordinator-mavlink.md).

The Pi supplies estimates; fusion is FC-side via `EK3_SRC*` when `VISO_TYPE` is enabled. FC-side covariance/gate mechanics: [ardupilot-extnav-fusion.md](ardupilot-extnav-fusion.md).

## Rekon context (not a param recipe)

Rekon uses **F9P + compass when RTK is good** and **VIO for bounded under-canopy legs** between ice-hole GPS resets ([rekon-design.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/rekon-design.md), [canopy-ops.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/canopy-ops.md)). The ArduPilot wiki OAK-D page describes a **VIO-primary** bench setup useful to prove the Pi pipeline -- not the same problem as dual-mode GPS+VIO operations.

Current FC export (`config/rekon10-methodi.param` in facts) is **pre-VIO**: `VISO_TYPE=0`, `EK3_SRC1` on GPS/compass/baro. Turning VIO on and choosing lane strategy is FC tuning work on the bench with you, not something this repo should pretend is settled.

Topics to work through when an FC is attached:

- When GPS and VIO are both active, which `EK3_SRC*` lane owns horizontal position and yaw?
- Whether wiki-style ExternalNav-primary is only an isolated proof profile.
- `VISO_DELAY_MS`, vertical axis trust, and flight modes under degraded estimates.

Record outcomes in a new param export in facts when there is something real to commit.

## Bench checks

**Vision only (no FC, `bench` profile):**

- `vio-tracker` and `vio-estimator` running; OAK-D enumerated on USB.
- Processes stay up; IMU/features on ipc sockets; pose on `/tmp/chobits_server` (tap or temporary router).

**With FC (`flight` profile):**

- `coordinator-mavlink` on configured UART.
- Mission Planner or logs show expected traffic before trusting fusion.

Images, `oak_d.yaml`, and compose profiles ship in a follow-up PR.
