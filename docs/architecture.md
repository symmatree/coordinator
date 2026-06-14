# Architecture

Rekon payload coordinator software for the Pi 4B central hub. Design notes live in the private [facts](https://github.com/symmatree/fables) repo (`fables/Drones/rekon10/central-hub.md`, `fables/Drones/coordinator/virtualization-study.md`).

## Host vs container

| Responsibility | Where | Why |
|----------------|-------|-----|
| OAK-D VIO pipeline | container(s) | Fragile C++/OpenCV/depthai deps; immutable image |
| MAVLink bridge to FC | container (likely separate from vision) | Optional on bench; serial device only when needed |
| Grafana Alloy (later) | container | Isolated observability |
| Pi Zero pod control API (later) | container | App logic |
| chrony + PPS discipline | **host** | GPIO `/dev/pps0`, `SYS_TIME` |
| USB gadget `br0` + DHCP | **host** | Dynamic `usb*` interfaces; not a Docker bridge problem |
| WiFi AP / station / off | **host** (or D-Bus-mounted utility container later) | NetworkManager integration |
| Docker Engine, Dockge | **host** | Generic runtime |

## Runtime paths (on Pi)

| Path | Contents |
|------|----------|
| `/opt/stacks/coordinator/` | `compose.yaml`, `.env` |
| `/var/lib/coordinator/config/` | VIO config (`oak_d.yaml`, etc.) mounted read-only |
| `/var/lib/coordinator/state/` | Runtime state (reserved) |
| `/opt/dockge/` | Dockge UI (upstream; shared across stacks) |

## VIO processes (no ROS)

The ArduPilot OAK-D stack is three native binaries ([upstream doc](https://ardupilot.org/copter/docs/common-vio-oak-d.html)):

| Process | Role | Needs OAK-D USB | Needs FC serial |
|---------|------|-----------------|-----------------|
| `feature_tracker` | OAK-D features -> VINS input | yes | no |
| `vins_fusion` | Visual-inertial pose estimate | no (reads tracker output) | no |
| `mavlink_udp` | Pose -> MAVLink to FC | no | yes (today's chobitsfan bridge) |

`feature_tracker` and `vins_fusion` talk to each other over localhost (UDP/ports in `oak_d.yaml` -- exact wiring confirmed when the image lands). `mavlink_udp` sits on the end of that chain.

FC / EKF notes: [ardupilot-vio.md](ardupilot-vio.md) (exploratory; not a tuned param set).

## Bench without FC

You should be able to validate the coordinator on a desk with only Pi + OAK-D:

- Run **vision only** (`feature_tracker` + `vins_fusion`): confirms USB, depthai, tracking, and pose output in logs or a recorded stream.
- **Do not** start `mavlink_udp` (or do not mount a serial device) until an FC is wired.
- Optional: point `mavlink_udp` at a UDP MAVLink listener on the host (QGroundControl, `mavlink-router`, etc.) instead of UART -- only if the chobitsfan binary supports that path; otherwise vision-only bench is the default.

Compose should express this with **profiles** or separate services so `coord start` on a bench machine does not require `/dev/serial0` to exist. Flight profile adds the MAVLink bridge.

## Container layout: options (not decided)

The skeleton compose file has a single `vio` service as a placeholder. When the image exists, pick among:

### A. One container, all three processes

One image; entrypoint starts tracker, then estimator, then mavlink (supervisord or a small wrapper).

| Pros | Cons |
|------|------|
| Simplest compose and ops (`coord pull`, one log stream) | Bench without FC needs entrypoint mode flags anyway |
| Matches upstream "open three terminals" as one unit | Restart / crash of one process often takes down the group |
| Blueos-oakd-vins precedent | Serial device mount required even for vision-only unless entrypoint skips mavlink |

### B. Two containers: vision + mavlink (leaning here)

| Service | Processes | Devices |
|---------|-----------|---------|
| `vio-vision` | `feature_tracker`, `vins_fusion` | USB only |
| `vio-mavlink` | `mavlink_udp` | USB not required; serial (or UDP-out if supported) |

Both use `network_mode: host` so localhost UDP between vision and mavlink works without extra Docker networking.

| Pros | Cons |
|------|------|
| Bench profile starts only `vio-vision` -- no FC, no serial | Startup order: vision before mavlink (`depends_on` + healthcheck or retry) |
| FC serial mount isolated to the small bridge container | Two services to monitor (still one image, two commands, is fine) |
| Restart MAVLink bridge without killing the camera pipeline | Slightly more compose YAML |

### C. Three containers (one process each)

Maximum isolation. Probably not worth it on a Pi 4B unless we hit a concrete restart or dep issue -- the processes are already lightweight compared to VINS compute.

### D. One container, compose profiles control entrypoint

Single service; `COORD_PROFILE=bench|flight` selects `vision-only` vs `full` inside the entrypoint. Same image as A, but compose does not need two services -- only env var and no serial mount in bench profile.

| Pros | Cons |
|------|------|
| One service in compose | Still one failure domain; profile logic lives in entrypoint |
| Clean bench (no serial in bench env) | Less obvious in `docker ps` what is running |

**Recommendation for next image PR:** target **B** or **D** so bench-without-FC is a first-class compose profile, not an afterthought. **B** is more explicit in Dockge/`coord status`; **D** is fewer moving parts if entrypoint modes stay simple.

## Stack services (current)

`compose.yaml` still has a single `vio` placeholder until the image PR splits or profiles land.

ROS in a container remains optional later; the host never installs ROS.
