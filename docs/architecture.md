# Architecture

Rekon payload coordinator software for the Pi 4B central hub. Design notes live in the private [facts](https://github.com/symmatree/fables) repo (`fables/Drones/rekon10/central-hub.md`, `fables/Drones/coordinator/virtualization-study.md`).

## Host vs container

| Responsibility | Where | Why |
|----------------|-------|-----|
| OAK-D pipeline (`feature_tracker`) | container | Fragile C++/depthai deps; USB device; rebuilds often |
| VINS estimator (`vins_fusion`) | container | Heavy native build; pin upstream; rebuild rarely |
| MAVLink router (coordinator-owned) | container | FC UART, Pi Zero relay, obstacle, pose ingress |
| Grafana Alloy (later) | container | Isolated observability |
| Pi Zero pod control API (later) | container or host | App logic; may share network with router |
| chrony + PPS discipline | **host** | GPIO `/dev/pps0`, `SYS_TIME` |
| USB gadget `br0` + DHCP | **host** | Dynamic `usb*` interfaces; not a Docker bridge problem |
| WiFi AP / station / off | **host** (or D-Bus-mounted utility container later) | NetworkManager integration |
| Docker Engine, Dockge | **host** | Generic runtime |

## Runtime paths (on Pi)

| Path | Contents |
|------|----------|
| `/opt/stacks/coordinator/` | `compose.yaml`, `.env` |
| `/var/lib/coordinator/config/` | VIO config (`oak_d.yaml`, etc.) mounted read-only |
| `/var/lib/coordinator/ipc/` | Shared Unix socket dir bind-mounted as `/tmp` in vision + mavlink containers |
| `/var/lib/coordinator/state/` | Runtime state (reserved; image logs, etc.) |
| `/opt/dockge/` | Dockge UI (upstream; shared across stacks) |

## Container layout (current plan)

Three services, one process each. Processes talk over **Unix domain datagram sockets** on a **shared bind mount** (not Docker bridge networking, not UDP). Socket paths and binary roles: [vio-integration.md](vio-integration.md).

```
USB OAK-D
  -> vio-tracker (feature_tracker)
       --/tmp/chobits_imu, /tmp/chobits_features-->
  -> vio-estimator (vins_fusion)
       --/tmp/chobits_server-->
  -> coordinator-mavlink (coordinator router; chobitsfan mavlink_udp is bring-up reference only)
       --UART--> FC
       (later: Pi Zero traffic, obstacle MAVLink, etc.)
```

| Service | Binary (hypothesis) | Devices / mounts |
|---------|---------------------|------------------|
| `vio-tracker` | `feature_tracker` | USB; `${COORDINATOR_IPC_DIR}:/tmp`; config ro |
| `vio-estimator` | `vins_fusion` | `${COORDINATOR_IPC_DIR}:/tmp`; config ro |
| `coordinator-mavlink` | coordinator MAVLink router | `${COORDINATOR_IPC_DIR}:/tmp`; FC serial; host network for Pi Zeros |

### Why three containers

| Benefit | Notes |
|---------|-------|
| Isolated logs | `coord logs vio-tracker` vs untangling one supervisord stream |
| Isolated rebuilds | Tracker image changes on depthai / pipeline / recording; estimator stays on a pinned chobitsfan SHA; router rebuilds during FC integration |
| Isolated restarts | MAVLink router crash does not kill the camera pipeline |
| Bench without FC | `tracker` profile: OAK-D only; `bench`: tracker + estimator; no serial |

### IPC volume

Set `COORDINATOR_IPC_DIR=/var/lib/coordinator/ipc` on the host. Each participating service mounts it at `/tmp` so hardcoded chobitsfan paths (`/tmp/chobits_imu`, etc.) work without source patches.

Operational notes:

- Create the directory before `coord start`; entrypoints should unlink stale `chobits_*` socket files after crashes.
- Run participating containers as the same UID, or make the ipc dir group-writable with a shared group.
- Replacing container `/tmp` is acceptable for these minimal single-purpose images.

### Compose profiles

| Profile | Services | Use |
|---------|----------|-----|
| `tracker` (default) | `vio-tracker` | First iteration: OAK-D feature extraction ([bench-tracker.md](bench-tracker.md)) |
| `bench` | `vio-tracker`, `vio-estimator` | Desk: full vision chain, no FC ([bench-estimator.md](bench-estimator.md)) |
| `flight` | above + `coordinator-mavlink` | FC serial mounted |

Startup order is not enforced between tracker and estimator: the tracker tolerates an absent listener (drops packets) and the estimator picks up the continuous stream whenever it binds. (A tracker->estimator `depends_on` is not usable -- the estimator is absent in the `tracker` profile, so it would error "depends on undefined service".) `coordinator-mavlink` consumes pose once the estimator publishes (flight profile).

## Rekon goals beyond wiki VIO

The ArduPilot OAK-D wiki stack proves pose to FC. Coordinator also targets goals from Rekon design docs. Which binary owns each item is spelled out in [vio-integration.md](vio-integration.md).

| Goal | In stock chobitsfan? |
|------|----------------------|
| Bounded canopy VIO pose | Partially (estimator + bridge pattern) |
| OAK-D image capture to disk (review / fly-through) | No -- likely `feature_tracker` / pipeline extension |
| Obstacle distance from depth | No -- disparity already computed in tracker |
| Pi Zero command / telemetry via USB bridge | No -- coordinator MAVLink router |

Pi Zero relay and obstacle MAVLink can ship after vision bench if complexity warrants deferral; the router process is still the long-term home for FC-facing integration.

## Bench without FC

**Tracker only (current):** see [bench-tracker.md](bench-tracker.md).

**Full vision (later):**

1. `COMPOSE_PROFILES=bench` and start tracker + estimator.
2. Confirm pose on `/tmp/chobits_server`.

Do not mount FC serial or start `coordinator-mavlink` until an FC is wired.

## Stack services

`stacks/coordinator/compose.yaml` defines three services. **`vio-tracker`** and **`vio-estimator`** images and Dockerfiles ship in `containers/`; the `coordinator-mavlink` image is the remaining follow-up.

ROS in a container remains optional later; the host never installs ROS for the flight path.

## FC / EKF

Exploratory notes only: [ardupilot-vio.md](ardupilot-vio.md). Not a settled param recipe or fusion policy.
