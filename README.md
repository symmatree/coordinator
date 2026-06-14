# coordinator

On-vehicle companion for Rekon: OAK-D VIO to MAVLink, Pi Zero USB bridging, and time sync. Standalone stack for the Raspberry Pi 4B payload computer ([symmatree/coordinator](https://github.com/symmatree/coordinator)).

## Stack layout

| Path | Role |
|------|------|
| `stacks/coordinator/compose.yaml` | Container stack (installed to `/opt/stacks/coordinator/` on the Pi) |
| `stacks/coordinator/.env` | Image tags and runtime defaults (edit for your unit) |
| `bin/coord` | Operator CLI over `docker compose` |
| `docs/` | Architecture, ArduPilot VIO runbook, external references |

Host-side provisioning (Docker, Dockge, chrony, USB bridge) will live under `host/` in a follow-up PR.

## Operator CLI

Install `bin/coord` to `/usr/local/bin/coord` on the Pi, or run from a checkout:

```bash
export COORD_COMPOSE_FILE=/path/to/coordinator/stacks/coordinator/compose.yaml
./bin/coord status
```

Commands: `pull`, `start`, `stop`, `restart`, `status`, `logs [service]`, `shell [service]`.

Default compose file: `/opt/stacks/coordinator/compose.yaml`. Override with `COORD_COMPOSE_FILE`.

## Quick start (Pi with Docker)

1. Copy `stacks/coordinator/` to `/opt/stacks/coordinator/`.
2. Install `bin/coord` to `/usr/local/bin/coord`.
3. Create `/var/lib/coordinator/config` for mounted VIO config (when the image ships).
4. `coord pull` then `coord start`.
5. Register the stack in Dockge pointing at `/opt/stacks/coordinator` (optional UI; `coord` is enough).

See [docs/architecture.md](docs/architecture.md) and [docs/ardupilot-vio.md](docs/ardupilot-vio.md).

## Status

- **This PR:** repo skeleton -- compose stack, `coord` CLI, architecture docs.
- **Next:** `containers/vio` image; compose profiles for bench (vision only) vs flight (+ MAVLink bridge); host Ansible + Dockge bootstrap.

Bench use: Pi + OAK-D only, no FC -- see [docs/architecture.md](docs/architecture.md#bench-without-fc).
