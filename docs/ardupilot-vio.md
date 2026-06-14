# ArduPilot OAK-D VIO

Runbook for Rekon coordinator VIO feeding the TBS Lucid H7. Canonical upstream: [Luxonis OAK-D -- Copter](https://ardupilot.org/copter/docs/common-vio-oak-d.html).

## Wiring

- OAK-D: Pi USB 3.0 (blue port).
- FC MAVLink: Pi UART to FC Telem (Rekon: confirm device alias on bench -- often `/dev/serial0` or `/dev/ttyAMA0`).

## FC parameters (starting set)

Set on the flight controller before trusting ExternalNav. Values from the ArduPilot wiki; verify against your param export before flight.

| Parameter | Value | Notes |
|-----------|-------|-------|
| `SERIAL1_PROTOCOL` | 2 | MAVLink2 on the VIO UART |
| `SERIAL1_BAUD` | 1500 | 1500000 baud |
| `VISO_TYPE` | 1 | |
| `VISO_DELAY_MS` | 50 | |
| `EK3_SRC1_POSXY` | 6 | ExternalNav |
| `EK3_SRC1_VELXY` | 6 | ExternalNav |
| `EK3_SRC1_POSZ` | 1 | Baro (safer for first flights) |
| `EK3_SRC1_VELZ` | 0 | Can move to 6 after bench confidence |
| `EK3_SRC1_YAW` | 6 | ExternalNav |
| `COMPASS_USE` / `USE2` / `USE3` | 0 | Disable compasses when using ExternalNav yaw |

Rekon param export: [rekon10-ardupilot.param](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/config/rekon10-ardupilot.param) (merge VIO params deliberately; do not blind-copy).

## Container processes

The `vio` service runs three binaries (no `roscore`):

1. `feature_tracker`
2. `vins_fusion` with `oak_d.yaml` from `/var/lib/coordinator/config`
3. `mavlink_udp`

## Bench checks

- `coord status` -- `coordinator_vio` running.
- Mission Planner: ExternalNav / `VISION_POSITION_ESTIMATE` active with documented params.
- No repeated USB disconnects under `/dev/bus/usb`.

Image build and config file layout ship in the next PR (`containers/vio/`).
