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

## VISO values held from the VIO attempt

**No VIO run ever met the bar.** These are the last values that were on the FC when
`VISO_TYPE` went to 0 ([#313](https://github.com/symmatree/coordinator/issues/313)), kept so
the work behind them is not repeated -- not a configuration to restore and trust. Two of the
things that would have to be true for them to be good are still open:
[#138](https://github.com/symmatree/coordinator/issues/138) (the extrinsics were never
calibrated) and [#156](https://github.com/symmatree/coordinator/issues/156) (output stalls
blocked VIO-as-position regardless of tuning).

They live here rather than in `ardupilot/inputs/ekf-vio.param` because at `VISO_TYPE=0` a
**param download cannot enumerate them**: `VISO_TYPE` is declared with `AP_PARAM_FLAG_ENABLE`
(`AP_VisualOdom.cpp:37`), and `AP_Param::next()` skips the rest of the subtree while an
enable param reads 0 (`AP_Param.cpp:1813-1818`). So the export drops from 1293 to 1283
params, the flight log's `PARM` block likewise carries only `VISO_TYPE`, and `verify.py`
reports the other seven `[missing]`. A fragment cannot round-trip against an export that is
structurally unable to contain it.

**They are not gone from the FC.** Enumeration hides them; storage keeps them, and a direct
`PARAM_REQUEST_READ` by name still answers. Measured on the vehicle 2026-09-23 with
`VISO_TYPE=0`: `VISO_POS_X` returned `0.072` and `VISO_DELAY_MS` returned `100`. They are
written down here because a doc survives a reflash, a defaults reset or a swapped board --
not because the values were at risk of vanishing the moment VIO went off.

### Grounded in something

| param | value | what it came from |
|---|---|---|
| `VISO_DELAY_MS` | `100` | VINS->FC transport latency measured on the link. ArduPilot's default is 10 ms, which is not this link. Re-measure if the path changes. |
| `VISO_POS_X` | `0.072` | OAK-D -> FC lever arm, metres FRD, taken off the mount geometry. |
| `VISO_POS_Y` | `-0.0375` | Same. Y carries a half-baseline offset because VINS reports in the cam0/left-imager frame; coupled to the camera extrinsics in `oak_d.yaml`. |
| `VISO_POS_Z` | `-0.116` | Same. |

The lever arm is mount geometry, not a calibration result -- calibrating it is the open work
in #138, and it is only valid for the OAK-D mount it was taken from.

### Not validated

| param | value | |
|---|---|---|
| `VISO_POS_M_NSE` | `0.2` | Floors the per-sample position covariance the router sends. Chosen to sit below the router's `MAVLINK_POS_NSE_BASE` (0.30) so the floor does not clobber the base -- a consistency constraint, not a measurement. |
| `VISO_VEL_M_NSE` | `0.1` | The FC ignores `VISION_SPEED_ESTIMATE.covariance` and fuses velocity at this param, so a router velocity covariance only takes effect if mirrored here. The router's measured 0.15 m/s was never mirrored into it. |
| `VISO_YAW_M_NSE` | `0.2` | Inert under `EK3_SRC_YAW=compass`. |

If VIO is revisited, the first block saves re-deriving; the second carries no evidence and
should be set deliberately rather than inherited.

Fusion mechanics: [ardupilot-extnav-fusion.md](ardupilot-extnav-fusion.md). Router side:
[coordinator-mavlink.md](coordinator-mavlink.md).

## Bench checks

**Vision only (no FC -- `coord start vio-tracker vio-estimator`):**

- `vio-tracker` and `vio-estimator` running; OAK-D enumerated on USB.
- Processes stay up; IMU/features on ipc sockets; pose on `/tmp/chobits_server` (tap or temporary router).

**With FC (the full operational set -- `coord start`):**

- `coordinator-mavlink` on configured UART.
- Mission Planner or logs show expected traffic before trusting fusion.

Images, `oak_d.yaml`, and compose profiles ship in a follow-up PR.
