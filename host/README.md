# Host provisioning

Ansible and a one-time shell entrypoint for the Rekon coordinator Pi (Docker, stack paths, `coord` CLI).

## First-time Pi setup

Full narrative (Imager, clone, reboot behavior): [docs/host-setup.md](../docs/host-setup.md).

After clone on the Pi:

```bash
./host/one_time.sh
```

That installs Ansible, runs [ansible/install-coordinator.yaml](ansible/install-coordinator.yaml) with `coordinator_sync_repo=true`, and reboots once if your user was newly added to the `docker` group.

Then bench the tracker: [docs/bench-tracker.md](../docs/bench-tracker.md).

## install-coordinator.yaml

Prepares a Pi 4B to pull and run the coordinator stack:

- Docker Engine + Compose plugin (Docker apt repo)
- `/opt/stacks/coordinator`, `/var/lib/coordinator/config`, `/var/lib/coordinator/ipc`
- Optional sync of `stacks/coordinator/` and `bin/coord` from this checkout (`coordinator_sync_repo=true`)

Manual run (without `one_time.sh`):

```bash
ansible-playbook host/ansible/install-coordinator.yaml \
  -e coordinator_sync_repo=true
```

GHCR images are public; `docker login ghcr.io` is not required for `coord pull`.

Not in this playbook yet: Dockge, chrony/PPS, USB gadget `br0` (see [docs/architecture.md](../docs/architecture.md)).
