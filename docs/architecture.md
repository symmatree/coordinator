# Architecture

Rekon payload coordinator software for the Pi 4B central hub. Design notes live in the private [facts](https://github.com/symmatree/fables) repo (`fables/Drones/rekon10/central-hub.md`, `fables/Drones/coordinator/virtualization-study.md`).

## Standalone product

This repository is **not** a fork of OpenMower or OpenMowerOS. It uses the same *class* of edge tooling (Docker Compose, Dockge, `/opt/stacks/`, thin CLI) for operator familiarity. External influences are listed in [references.md](references.md) only.

## Host vs container

| Responsibility | Where | Why |
|----------------|-------|-----|
| OAK-D VIO + MAVLink proxy | `vio` container | Fragile C++/OpenCV/depthai deps; immutable image |
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
| `/var/lib/coordinator/config/` | VIO config (`oak_d.yaml`, etc.) mounted read-only into `vio` |
| `/var/lib/coordinator/state/` | Runtime state (reserved) |
| `/opt/dockge/` | Dockge UI (upstream; shared across stacks) |

## VIO payload

ArduPilot OAK-D VIO uses **three native C++ binaries** (no ROS): `feature_tracker`, `vins_fusion`, `mavlink_udp`. See [ardupilot-vio.md](ardupilot-vio.md).

ROS in a container remains an optional later path if a service needs nodes; the host never installs ROS.

## Stack services (current)

`compose.yaml` defines `vio` only until the image and follow-on services land.
