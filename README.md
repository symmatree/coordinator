# coordinator

On-vehicle companion for Rekon: OAK-D VIO to MAVLink, Pi Zero USB bridging, and time sync. Hosts the stacks for both the Raspberry Pi 4B payload computer (**coordinator**) and the Pi Zero 2 W camera pods (**pod**) ([symmatree/coordinator](https://github.com/symmatree/coordinator)). The two devices share one bootstrap and CLI; see [docs/pi-zero-bringup.md](docs/pi-zero-bringup.md).

## Stack layout

| Path | Role |
|------|------|
| `stacks/coordinator/` | Coordinator compose stack (installed to `/opt/stacks/coordinator/` on the Pi 4B) |
| `stacks/pod/` | Pod compose stack (installed to `/opt/stacks/pod/` on each Pi Zero) |
| `containers/vio-tracker/` | OAK-D `feature_tracker` image (arm64) |
| `containers/pod-camera/` | Pod capture image (arm64; Phase 2, placeholder) |
| `bin/coord` | Shared operator CLI; auto-detects the device's stack under `/opt/stacks/*` |
| `host/ansible/` | Shared bootstrap: `site.yaml` + roles (`docker-host`, `coord-stack`, `coordinator`, `pod`) |
| `host/one_time.sh` | One-time apt + Ansible entrypoint; `one_time.sh [coordinator\|pod]` |
| `docs/` | Architecture, bench runbooks, pod bringup, references |

## Vision stack

| Service | Role | Status |
|---------|------|--------|
| `vio-tracker` | OAK-D `feature_tracker` (USB) | Image + CI in progress |
| `vio-estimator` | `vins_fusion` | Planned |
| `coordinator-mavlink` | Coordinator MAVLink router to FC | Planned |

Processes share Unix sockets via `${COORDINATOR_IPC_DIR}` mounted at `/tmp`. Details: [docs/vio-integration.md](docs/vio-integration.md).

## Operator CLI

Install `bin/coord` to `/usr/local/bin/coord` on the Pi, or run from a checkout:

```bash
export COORD_COMPOSE_FILE=/path/to/coordinator/stacks/coordinator/compose.yaml
./bin/coord status
```

Commands: `pull`, `start`, `stop`, `restart`, `status`, `logs [service]`, `shell [service]`.

Per-service logs: `coord logs vio-tracker`, etc.

## Quick start (Pi + OAK-D)

1. One-time host setup: [docs/host-setup.md](docs/host-setup.md) (`./host/one_time.sh` after clone).
2. Attach OAK-D, then `coord pull`, `coord start` (default `tracker` profile).
3. `coord logs -f vio-tracker` -- expect USB speed, `imu ok`, `N features`.

Runbook: [docs/bench-tracker.md](docs/bench-tracker.md).

## Status

- **Shipped:** compose stack, `coord` CLI, architecture docs, `vio-tracker` Dockerfile + GHCR workflow, host Ansible bootstrap.
- **Next:** prove OAK-D on bench Pi; `vio-estimator` image; coordinator MAVLink router; Dockge + chrony/`br0` on host.
