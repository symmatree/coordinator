# coordinator

On-vehicle companion for Rekon: OAK-D VIO to MAVLink, Pi Zero USB bridging, and time sync. Standalone stack for the Raspberry Pi 4B payload computer ([symmatree/coordinator](https://github.com/symmatree/coordinator)).

## Stack layout

| Path | Role |
|------|------|
| `stacks/coordinator/compose.yaml` | Three-service stack (installed to `/opt/stacks/coordinator/` on the Pi) |
| `stacks/coordinator/.env` | Image tags, ipc dir, serial device, config paths |
| `bin/coord` | Operator CLI over `docker compose` |
| `docs/` | Architecture, VIO integration, ArduPilot notes, references |

Host-side provisioning (Docker, Dockge, chrony, USB bridge) will live under `host/` in a follow-up PR.

## Vision stack (planned)

| Service | Role |
|---------|------|
| `vio-tracker` | OAK-D `feature_tracker` (USB) |
| `vio-estimator` | `vins_fusion` |
| `coordinator-mavlink` | Coordinator MAVLink router to FC (flight profile) |

Processes share Unix sockets via `${COORDINATOR_IPC_DIR}` mounted at `/tmp`. Details: [docs/vio-integration.md](docs/vio-integration.md).

## Operator CLI

Install `bin/coord` to `/usr/local/bin/coord` on the Pi, or run from a checkout:

```bash
export COORD_COMPOSE_FILE=/path/to/coordinator/stacks/coordinator/compose.yaml
./bin/coord status
```

Commands: `pull`, `start`, `stop`, `restart`, `status`, `logs [service]`, `shell [service]`.

Default compose file: `/opt/stacks/coordinator/compose.yaml`. Override with `COORD_COMPOSE_FILE`.

Per-service logs: `coord logs vio-tracker`, `coord logs vio-estimator`, `coord logs coordinator-mavlink`.

## Quick start (Pi with Docker)

1. Copy `stacks/coordinator/` to `/opt/stacks/coordinator/`.
2. Install `bin/coord` to `/usr/local/bin/coord`.
3. Create `/var/lib/coordinator/config` and `/var/lib/coordinator/ipc`.
4. `coord pull` then `coord start` (images ship in a follow-up PR).
5. Register the stack in Dockge pointing at `/opt/stacks/coordinator` (optional UI; `coord` is enough).

See [docs/architecture.md](docs/architecture.md), [docs/vio-integration.md](docs/vio-integration.md), and [docs/ardupilot-vio.md](docs/ardupilot-vio.md).

## Status

- **This PR:** repo skeleton -- compose stack, `coord` CLI, architecture docs.
- **Next:** container images (`vio-tracker`, `vio-estimator`, `coordinator-mavlink`); chobitsfan pins for vision; coordinator MAVLink router; compose `bench` / `flight` profiles; host Ansible + Dockge bootstrap.

Bench use: Pi + OAK-D only, no FC -- [architecture.md](docs/architecture.md#bench-without-fc).
