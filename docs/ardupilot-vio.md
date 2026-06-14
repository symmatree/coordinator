# ArduPilot OAK-D VIO

Coordinator-side VIO feeding the TBS Lucid H7. Upstream build and wiring: [Luxonis OAK-D -- Copter](https://ardupilot.org/copter/docs/common-vio-oak-d.html).

Container layout and bench-without-FC: [architecture.md](architecture.md).

## Wiring (flight)

- OAK-D: Pi USB 3.0.
- FC MAVLink: Pi UART to a FC serial port with MAVLink2 (`SERIALn_PROTOCOL=2`, `SERIALn_BAUD=1500000`). Confirm device alias on bench (`/dev/serial0`, `/dev/ttyAMA0`, etc.).

## What the coordinator sends

`mavlink_udp` publishes visual-odometry MAVLink (e.g. `VISION_POSITION_ESTIMATE`) to the FC. ArduPilot consumes that when `VISO_TYPE` is enabled. The Pi does not replace the FC EKF -- it supplies estimates; fusion is FC-side via `EK3_SRC*`.

## Rekon context (not a param recipe)

Rekon uses **F9P + compass when RTK is good** and **VIO for bounded under-canopy legs** between ice-hole GPS resets ([rekon-design.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/rekon-design.md), [canopy-ops.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/canopy-ops.md)). The ArduPilot wiki OAK-D page describes a **VIO-primary** bench setup (ExternalNav for XY/vel/yaw, compasses off) useful to prove the Pi pipeline -- not the same problem as dual-mode GPS+VIO operations.

Current FC export (`config/rekon10-methodi.param` in facts) is **pre-VIO**: `VISO_TYPE=0`, `EK3_SRC1` on GPS/compass/baro. Turning VIO on and choosing lane strategy is FC tuning work with you on the bench, not something this repo should pretend is settled.

Topics to work through when an FC is attached:

- When GPS and VIO are both active, which `EK3_SRC*` lane owns horizontal position and yaw?
- Whether wiki-style ExternalNav-primary is only an isolated proof profile.
- `VISO_DELAY_MS`, vertical axis trust, and flight modes under degraded estimates.

Record outcomes in a new param export in facts when there is something real to commit.

## Bench checks

**Vision only (no FC):**

- `vio-vision` (or bench profile) running; OAK-D enumerated on USB.
- Tracker and estimator stay up; pose output visible in logs (exact check TBD with image).

**With FC:**

- MAVLink messages on the configured UART.
- Mission Planner or logs show expected traffic before trusting fusion.

Image build, `oak_d.yaml`, and compose profiles ship in a follow-up PR.
