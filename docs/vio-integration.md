# VIO integration

How the vision stack is wired today (chobitsfan `apm_wiki` hypothesis) and where Rekon-specific work lands. This is a **working plan**, not a frozen spec.

Upstream reference: [ArduPilot Luxonis OAK-D](https://ardupilot.org/copter/docs/common-vio-oak-d.html). Pin SHAs on the three chobitsfan repos when images build; fork only when bench proves it.

## Process chain

```
feature_tracker  --UDS-->  vins_fusion  --UDS-->  coordinator MAVLink router  --UART-->  FC
     |                          |
  OAK-D USB                 oak_d.yaml
  depthai pipeline          (mounted /config)
```

No ROS on the flight path.

## Unix domain sockets (chobitsfan `apm_wiki`)

All paths are under `/tmp`. Containers share them by bind-mounting `${COORDINATOR_IPC_DIR}` to `/tmp` (see [architecture.md](architecture.md#ipc-volume)).

| Socket path | Direction | Payload (approx.) |
|-------------|-----------|-------------------|
| `/tmp/chobits_imu` | tracker -> estimator | IMU packets (7 doubles: time + acc/gyro) |
| `/tmp/chobits_features` | tracker -> estimator | Feature bundles (count + up to ~118 features x 13 doubles) |
| `/tmp/chobits_2222` | tracker bind address | Local bind for tracker outbound dgrams |
| `/tmp/chobits_server` | estimator -> mavlink | 10 floats: attitude + position + velocity |

Sources: [oak_d_vins_cpp `feature_tracker.cpp`](https://github.com/chobitsfan/oak_d_vins_cpp/blob/apm_wiki/feature_tracker.cpp), [mavlink-udp-proxy `my_mavlink_udp.cpp`](https://github.com/chobitsfan/mavlink-udp-proxy/blob/apm_wiki/my_mavlink_udp.cpp).

BlueOS `mavlink2restForwarder.py` listens on the same `/tmp/chobits_server` contract with different egress -- useful as a read-only reference, not the Rekon FC path.

## Binary-by-binary plan

| Binary | Repo | Container | Plan |
|--------|------|-----------|------|
| `feature_tracker` | [oak_d_vins_cpp](https://github.com/chobitsfan/oak_d_vins_cpp) `apm_wiki` | `vio-tracker` | Use as-is for bench VIO. Fork or extend when we need **image recording** or **obstacle distance** -- both need the same depthai pipeline that already owns USB. |
| `vins_fusion` | [VINS-Fusion](https://github.com/chobitsfan/VINS-Fusion) `apm_wiki` | `vio-estimator` | Use as-is (hardest part; smallest reason to touch early). Mount calibration and `oak_d.yaml` live here. |
| `mavlink_udp` | [mavlink-udp-proxy](https://github.com/chobitsfan/mavlink-udp-proxy) `apm_wiki` | -- | **Not** the shipping bridge. Read for timesync / message shapes; replace with **coordinator MAVLink router** in `coordinator-mavlink`. |

### `feature_tracker` owns the camera

One USB device, one `dai::Device`, one pipeline in `feature_tracker` today: mono L/R @ 640x400, on-device feature tracking, stereo disparity, IMU.

- **Two processes cannot each open the OAK-D** for independent pipelines in the usual Luxonis model.
- **Extra outputs** (JPEG/H264 stills, logged disparity, obstacle sampling) are added as `XLinkOut` branches on the **same** pipeline in the process that already holds the device -- not a sidecar with its own USB open.

Disparity frames already exist in-process for stereo matching; they are not written to disk today.

### Coordinator MAVLink router (`coordinator-mavlink`)

Long-term home for Rekon FC integration:

- Read `/tmp/chobits_server` (same pose contract as chobitsfan initially -- keeps `vins_fusion` untouched).
- UART to FC (1.5 Mbaud MAVLink2 per current wiring).
- Later: Pi Zero pod messages (host `br0` / USB gadget network), obstacle MAVLink, operator-facing status.

Defer Pi Zero relay and obstacle messages until after vision bench if that sharply reduces early complexity; do not defer having **some** coordinator-owned router for flight.

## Images and build isolation

| Image | Rebuild when |
|-------|----------------|
| `coordinator-vio-tracker` | depthai version, pipeline, recording, obstacle logic |
| `coordinator-vio-estimator` | rare -- chobitsfan pin, `oak_d.yaml` tooling |
| `coordinator-mavlink` | FC integration, Pi Zero protocol, MAVLink features |

Separate images keep CI/cache scoped to the component that changed.

## Optional: configurable socket dir

Hardcoded `/tmp/chobits_*` paths work with a shared ipc mount and no source changes. If we outgrow mounting all of `/tmp`, a small patch (env var for socket prefix) in tracker + estimator + router is the alternative to host-wide `/tmp` sharing.

## Related docs

- Container layout and profiles: [architecture.md](architecture.md)
- How `oak_d.yaml` calibration is produced, validated, and stored: [calibration.md](calibration.md)
- FC wiring and tuning (exploratory): [ardupilot-vio.md](ardupilot-vio.md)
- Rekon design context: [central-hub.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/central-hub.md), [oak-d-mount.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/oak-d-mount.md), [arm-pods.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/arm-pods.md)
