# ArduPilot OAK-D VIO

Coordinator-side VIO feeding the TBS Lucid H7. Upstream build and wiring: [Luxonis OAK-D -- Copter](https://ardupilot.org/copter/docs/common-vio-oak-d.html). Rekon mission context: [rekon-design.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/rekon-design.md), [canopy-ops.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/canopy-ops.md).

## Wiring

- OAK-D: Pi USB 3.0 (blue port).
- FC MAVLink: Pi UART to a FC serial port configured for MAVLink2 (Rekon bench: confirm alias -- often `/dev/serial0` or `/dev/ttyAMA0`). Match `SERIALn_*` on the FC to that physical port.

## What the coordinator sends

The `mavlink_udp` process publishes `VISION_POSITION_ESTIMATE` (and related visual-odometry messages per the chobitsfan stack) over MAVLink at high baud. ArduPilot treats this as **ExternalNav** input when `VISO_TYPE` is enabled and the EKF is configured to consume it.

The Pi does not replace the FC EKF. It supplies position/velocity (and effectively yaw, if configured) estimates; the FC's EKF3 fuses those with IMU, baro, GPS, compass, etc. according to `EK3_SRC*`.

## Rekon navigation design (why EKF params are not a copy-paste)

Rekon is **not** a GPS-denied-only vehicle. The intended sensor roles:

| Sensor | Role |
|--------|------|
| **F9P on the mast** | Primary global position when RTK Fixed (or usable 3D fix) |
| **F9P-integrated compass** | Yaw when GNSS lane is healthy |
| **OAK-D VIO** | Dead-reckoning for **bounded** under-canopy legs (~60-90 s) between ice-hole GPS resets |
| **Baro** | Vertical reference (especially before trusting VIO on Z) |

The [ice-hole pattern](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/canopy-ops.md) assumes the EKF **returns to the GPS lane** after each breakout, not that VIO permanently substitutes for GPS. [canopy-ops.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/canopy-ops.md) also states that sudden VIO jumps are a flight-safety risk -- tuning is about **when** to trust VIO and **how hard** the FC corrects, not only "enable ExternalNav."

### Current FC export (GPS-primary lane)

The committed param export (`config/rekon10-methodi.param` in facts) reflects **open-sky / pre-VIO** bring-up:

| Parameter | Export value | Meaning |
|-----------|--------------|---------|
| `VISO_TYPE` | 0 | Visual odometry disabled |
| `EK3_SRC1_POSXY` | 3 | GPS |
| `EK3_SRC1_VELXY` | 3 | GPS |
| `EK3_SRC1_YAW` | 1 | Compass |
| `EK3_SRC1_POSZ` | 1 | Baro |
| `EK3_SRC1_VELZ` | 3 | GPS |
| `COMPASS_USE` | 1 | Mast compass active |

That is the right baseline for hover and GPS tuning. Enabling VIO is a **deliberate migration**, not a one-shot table from the wiki.

### What the ArduPilot wiki recipe assumes

The [OAK-D wiki page](https://ardupilot.org/copter/docs/common-vio-oak-d.html) documents a **VIO-as-primary-navigation** bench setup: position, velocity, and yaw from ExternalNav, compasses off, baro still used for Z on first flights. That matches flying Loiter/RTL **without GPS**, which is useful for proving the Pi pipeline.

It does **not** by itself describe Rekon's **dual-mode** operation (RTK when available, VIO only for degraded-GPS intervals). Copying wiki `EK3_SRC1_*` values into a GPS-flying Rekon without analysis would:

- Discard mast compass yaw whenever ExternalNav yaw is selected, even in open sky where RTK+compass is better.
- Force the EKF to treat VIO as the primary horizontal source even when RTK Fixed is available, unless additional source/lane logic is configured.
- Increase exposure to VIO yaw errors near metal, motors, and current-heavy wiring (documented compass pain points elsewhere in Rekon notes).

Treat the wiki values as a **bench profile** ("prove VIO messages fuse at all"), not as production under-canopy policy.

## EKF tuning axes (work to do before trusting flight)

These are the decisions to make on the bench and in short canopy-adjacent flights, with params recorded in a new export after each stage.

### 1. Transport: get messages on the wire

Enable the visual-odometry driver and match the UART the Pi uses:

| Parameter | Bench starting value | Notes |
|-----------|---------------------|-------|
| `SERIALn_PROTOCOL` | 2 | MAVLink2 on the Pi-facing port |
| `SERIALn_BAUD` | 1500 | 1500000 baud (wiki and chobitsfan stack) |
| `VISO_TYPE` | 1 | Enable consumption of visual odometry |

Confirm in Mission Planner: `VISION_POSITION_ESTIMATE` arriving at expected rate; no framing errors at baud.

### 2. Lane strategy: GPS-primary vs VIO-primary

**Open question for Rekon:** keep `EK3_SRC1` on GPS/compass for open sky and rely on ArduPilot's GPS glitch / timeout behavior to coast on inertial + recent state when GPS drops, **or** switch lanes when entering canopy, **or** run a VIO-primary bench profile separate from mission params.

Factors:

- How cleanly the F9P reports "no fix" vs misleading float under canopy (affects when GPS should drop out of the fusion).
- Whether `EK3_SRC_OPTIONS` (and related GPS check params) give acceptable handoff without re-flashing params between mission phases.
- Ice-hole resets: after RTK Fixed returns, does the EKF reconverge with compass+yaw from GPS velocity, or does VIO yaw leave a heading offset? [canopy-ops.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/canopy-ops.md) calls out watching `EKF_STATUS_REPORT` during the hold.

**Wiki bench profile** (VIO-primary, GPS not trusted for horizontal nav):

| Parameter | Wiki value | Rekon note |
|-----------|------------|------------|
| `EK3_SRC1_POSXY` | 6 (ExternalNav) | Only for isolated VIO proof flights |
| `EK3_SRC1_VELXY` | 6 | Same |
| `EK3_SRC1_YAW` | 6 | Conflicts with mast compass strategy; compass must be off if yaw is ExternalNav |
| `EK3_SRC1_POSZ` | 1 (Baro) | Reasonable first-flight conservatism |
| `EK3_SRC1_VELZ` | 0, later 6 | Wiki: start with baro/zero vertical velocity trust; promote after stable bench |
| `COMPASS_USE*` | 0 | Required for wiki yaw mode; **wrong for open-sky RTK missions** if left permanently |

### 3. Vertical axis

Baro for `POSZ` while learning VIO is deliberate: VIO Z is often weaker than horizontal, and a baro blunder is easier to recover from than trusting a bad vertical ExternalNav estimate near the ground under canopy.

Promoting `VELZ` / `POSZ` to ExternalNav is a **second-stage** decision after horizontal track quality is logged.

### 4. Delay and dynamics

| Parameter | Wiki value | Role |
|-----------|------------|------|
| `VISO_DELAY_MS` | 50 | Accounts for pipeline latency (camera + Pi + UART). Wrong delay shows as horizontal lag or EKF innovations; tune from logs, not assumed final. |

### 5. Flight-mode interaction

Under-canopy doctrine prefers **AltHold or Stabilize** over Loiter when position estimates are degraded ([canopy-ops.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/canopy-ops.md)). Loiter with shaky ExternalNav commands lateral corrections toward trees. Mode choice is part of EKF/VIO policy, not separable from params.

## Staged bring-up (proposed)

1. **Transport only** -- `VISO_TYPE=1`, UART configured; EKF still GPS-primary (`EK3_SRC1` unchanged). Verify messages and no serial overload.
2. **Wiki bench profile** -- props off / tethered / open area: ExternalNav-primary, compasses off, prove fusion and delay tuning.
3. **GPS handoff tests** -- open sky with RTK Fixed, then walk/drive into denial (or simulate loss); log when GPS innovations fail and how EKF behaves with mixed sources. Decide lane strategy.
4. **Short canopy-adjacent legs** -- slow speed, ice-hole resets mandatory, compare compass+yaw vs ExternalNav yaw on recovery.
5. **Export and commit** -- new `rekon10-*.param` in facts with a short changelog in [ardupilot.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/ardupilot.md).

## Container processes

The `vio` service runs three binaries (no `roscore`):

1. `feature_tracker`
2. `vins_fusion` with `oak_d.yaml` from `/var/lib/coordinator/config`
3. `mavlink_udp`

## Bench checks (coordinator hardware)

- `coord status` -- `coordinator_vio` running.
- USB stable under `/dev/bus/usb`.
- MAVLink heartbeat and `VISION_POSITION_ESTIMATE` on the configured FC port.

Image build and `oak_d.yaml` layout ship in a follow-up PR (`containers/vio/`).
