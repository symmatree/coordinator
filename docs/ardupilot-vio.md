# ArduPilot OAK-D VIO

Coordinator-side VIO feeding the TBS Lucid H7. Upstream build reference: [Luxonis OAK-D -- Copter](https://ardupilot.org/copter/docs/common-vio-oak-d.html). Process wiring and socket IPC: [vio-integration.md](vio-integration.md). Containers and bench profile: [architecture.md](architecture.md).

## Wiring (flight)

- OAK-D: Pi USB 3.0 (`vio-tracker` container).
- FC MAVLink: Pi primary UART -> FC **SERIAL4**. Pi side is `/dev/serial0` = `/dev/ttyAMA0` (PL011, via `enable_uart=1` + `disable-bt`; the coordinator Ansible role sets this): **GPIO14/TXD (header pin 8) -> FC RX, GPIO15/RXD (pin 10) -> FC TX, common GND (pin 6)**. MAVLink2 at 1.5 Mbaud -> FC `SERIAL4_PROTOCOL=2`, `SERIAL4_BAUD=1500000`. Not yet wired: `SERIAL4` is deliberately left at `None` (like the other unused ports) until the cable exists, so a disconnected port doesn't invite phantom-link troubleshooting. Set the protocol/baud when it's cabled.

## What the coordinator sends

The **coordinator MAVLink router** (`coordinator-mavlink`) publishes visual-odometry MAVLink to the FC: `ATT_POS_MOCAP` (position + covariance) and `VISION_SPEED_ESTIMATE` (dPos/dt velocity + covariance), plus a `TIMESYNC` reply. It is seeded from chobitsfan `mavlink_udp` but diverged -- design of record: [coordinator-mavlink.md](coordinator-mavlink.md).

The Pi supplies estimates; fusion is FC-side via `EK3_SRC*` when `VISO_TYPE` is enabled. FC-side covariance/gate mechanics: [ardupilot-extnav-fusion.md](ardupilot-extnav-fusion.md).

## Rekon context (not a param recipe)

Rekon uses **F9P + compass when RTK is good** and **VIO for bounded under-canopy legs** between ice-hole GPS resets ([rekon-design.md](rekon10/rekon-design.md), [canopy-ops.md](rekon10/canopy-ops.md)). The ArduPilot wiki OAK-D page describes a **VIO-primary** bench setup useful to prove the Pi pipeline -- not the same problem as dual-mode GPS+VIO operations.

Current FC export (`config/rekon10-methodi.param` in facts) is **pre-VIO**: `VISO_TYPE=0`, `EK3_SRC1` on GPS/compass/baro. Turning VIO on and choosing lane strategy is FC tuning work on the bench with you, not something this repo should pretend is settled.

Topics to work through when an FC is attached:

- When GPS and VIO are both active, which `EK3_SRC*` lane owns horizontal position and yaw?
- Whether wiki-style ExternalNav-primary is only an isolated proof profile.
- `VISO_DELAY_MS`, vertical axis trust, and flight modes under degraded estimates.

Record outcomes in a new param export in facts when there is something real to commit.

## Measured VISO values

Held here rather than in `ardupilot/inputs/ekf-vio.param`. At `VISO_TYPE=0` these seven
params **do not exist in the FC's param set** -- the export drops from 1293 to 1283 params
and `verify.py` reports them `[missing]` -- so the fragment cannot pin them. They are
measurements, not tuning guesses, and re-deriving the lever arm costs a bench session.

Restore them to the fragment when `VISO_TYPE` goes non-zero and the params come back.

| param | value | provenance |
|---|---|---|
| `VISO_DELAY_MS` | `100` | Measured VINS->FC transport latency ~100 ms. ArduPilot's default is 10 ms, which is not this link. |
| `VISO_POS_X` | `0.072` | OAK-D -> FC lever arm, metres FRD. |
| `VISO_POS_Y` | `-0.0375` | Same; Y carries a half-baseline offset because VINS reports in the cam0/left-imager frame. Coupled to the camera extrinsics in `oak_d.yaml`. |
| `VISO_POS_Z` | `-0.116` | Same. |
| `VISO_POS_M_NSE` | `0.2` | Position noise floor. Floors the per-sample covariance the router sends; keep it below `MAVLINK_POS_NSE_BASE` or it clobbers the base. |
| `VISO_VEL_M_NSE` | `0.1` | Velocity noise. The FC ignores `VISION_SPEED_ESTIMATE.covariance` and fuses at this param, so a router velocity covariance only takes effect if mirrored here. |
| `VISO_YAW_M_NSE` | `0.2` | Yaw noise. Inert under `EK3_SRC_YAW=compass`. |

Fusion mechanics for these: [ardupilot-extnav-fusion.md](ardupilot-extnav-fusion.md). Router
side: [coordinator-mavlink.md](coordinator-mavlink.md).

## Bench checks

**Vision only (no FC -- `coord start vio-tracker vio-estimator`):**

- `vio-tracker` and `vio-estimator` running; OAK-D enumerated on USB.
- Processes stay up; IMU/features on ipc sockets; pose on `/tmp/chobits_server` (tap or temporary router).

**With FC (the full operational set -- `coord start`):**

- `coordinator-mavlink` on configured UART.
- Mission Planner or logs show expected traffic before trusting fusion.

Images, `oak_d.yaml`, and compose profiles ship in a follow-up PR.
