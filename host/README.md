# Host provisioning

Ansible playbooks for the Rekon coordinator Pi (Docker, stack paths, `coord` CLI).

## install-coordinator.yaml

Prepares a Pi 4B to pull and run the coordinator stack:

- Docker Engine + Compose plugin (Docker apt repo)
- `/opt/stacks/coordinator`, `/var/lib/coordinator/config`, `/var/lib/coordinator/ipc`
- Optional sync of `stacks/coordinator/` and `bin/coord` from this checkout

### Run on the Pi

Clone or copy this repo, then:

```bash
ansible-playbook host/ansible/install-coordinator.yaml \
  -e coordinator_sync_repo=true
```

Log out and back in (or `newgrp docker`) so group membership applies.

Set `VIO_TRACKER_VERSION` in `/opt/stacks/coordinator/.env` to a published tag (`main`, commit SHA, etc.), then:

```bash
coord pull
coord start
coord logs -f vio-tracker
```

Bench checklist: [docs/bench-tracker.md](../docs/bench-tracker.md).

Not in this playbook yet: Dockge, chrony/PPS, USB gadget `br0` (see [docs/architecture.md](../docs/architecture.md)).
