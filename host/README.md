# Host provisioning

Ansible and a one-time shell entrypoint for Rekon devices (Docker, stack paths, `coord` CLI). One shared playbook serves both the **coordinator** (Pi 4B) and the **pod** (Pi Zero 2 W); `device_role` selects the device.

## First-time Pi setup

Full narratives: coordinator [docs/host-setup.md](../docs/host-setup.md), pod [docs/pi-zero-host-setup.md](../docs/pi-zero-host-setup.md).

After clone on the device:

```bash
./host/one_time.sh              # coordinator (Pi 4B), default
./host/one_time.sh pod          # pod (Pi Zero 2 W)
```

That installs Ansible, runs [ansible/site.yaml](ansible/site.yaml) with `sync_repo=true` and the chosen `device_role`, and reboots when kernel/firmware updates require it. Repeat until the script completes without rebooting.

Then bench: coordinator tracker [docs/bench-tracker.md](../docs/bench-tracker.md); pod phases [docs/pi-zero-bringup.md](../docs/pi-zero-bringup.md).

## site.yaml and roles

`site.yaml` layers a device role on top of the shared `docker-host` role:

| Role | Scope |
|------|-------|
| `docker-host` | **Shared** -- Docker Engine + Compose plugin, docker group, service, kernel/firmware reboot loop |
| `coord-stack` | **Shared** -- `/opt/stacks/<name>`, state dirs, sync stack + `coord` from checkout |
| `coordinator` | OAK-D udev rules; coordinator stack (`/var/lib/coordinator/{config,ipc}`) |
| `pod` | pod stack (`/var/lib/pod/{config,captures}`); Phase 3 adds `dwc2`/`g_ether` + PPS overlays |

Manual run (without `one_time.sh`):

```bash
ansible-playbook host/ansible/site.yaml -e device_role=coordinator -e sync_repo=true
ansible-playbook host/ansible/site.yaml -e device_role=pod -e sync_repo=true
```

GHCR images are public; `docker login ghcr.io` is not required for `coord pull`.

Not in these roles yet: Dockge, chrony/PPS, USB gadget `br0` (see [docs/architecture.md](../docs/architecture.md) and [docs/pi-zero-bringup.md](../docs/pi-zero-bringup.md)).
