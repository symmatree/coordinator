# coordinator

On-vehicle companion for Rekon: OAK-D VIO to MAVLink, Pi Zero USB bridging, and time sync. Standalone stack for the Raspberry Pi 4B payload computer ([symmatree/coordinator](https://github.com/symmatree/coordinator)).

## Stack layout

| Path | Role |
|------|------|
| `stacks/coordinator/compose.yaml` | Compose stack (installed to `/opt/stacks/coordinator/` on the Pi) |
| `stacks/coordinator/.env` | Image tags, ipc dir, compose profiles |
| `containers/vio-tracker/` | OAK-D `feature_tracker` image (arm64) |
| `bin/coord` | Operator CLI over `docker compose` |
| `host/ansible/` | Pi bootstrap (Docker, stack paths, `coord`) |
| `host/one_time.sh` | One-time apt + Ansible entrypoint on the Pi |
| `docs/` | Architecture, bench runbooks, references |

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
