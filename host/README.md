# Host provisioning

Ansible and a one-time shell entrypoint for Rekon devices (Docker, stack paths, `coord` CLI). One shared playbook serves both the **coordinator** (Pi 4B) and the **pod** (Pi Zero 2 W); `device_role` selects the device.

## First-time Pi setup

Full narratives: coordinator [docs/host-setup.md](../docs/host-setup.md), pod [docs/pi-zero-host-setup.md](../docs/pi-zero-host-setup.md).

After clone on the device:

```bash
./host/one_time.sh              # coordinator (Pi 4B), default
./host/one_time.sh pod          # pod (Pi Zero 2 W)
```

That installs Ansible and runs [ansible/site.yaml](ansible/site.yaml) with `sync_repo=true` and the chosen `device_role` to converge config. It is a **config-only** deploy -- it does **not** `dist-upgrade` the OS (run [os_upgrade.sh](os_upgrade.sh) for that; [#48](https://github.com/symmatree/coordinator/issues/48)). Ansible still reboots if a kernel/firmware/module change it installs requires it; repeat until the script completes without rebooting.

Then bench: coordinator tracker [docs/bench-tracker.md](../docs/bench-tracker.md); pod phases [docs/pi-zero-bringup.md](../docs/pi-zero-bringup.md).

## site.yaml and roles

`site.yaml` layers a device role on top of the shared `docker-host` role:

| Role | Scope |
|------|-------|
| `docker-host` | **Shared** -- Docker Engine + Compose plugin, docker group, service, kernel/firmware reboot loop |
| `coord-stack` | **Shared** -- symlinks `/opt/stacks/<name>` to the checkout (`git pull` is the deploy, [#48](https://github.com/symmatree/coordinator/issues/48)), state dirs, installs `coord` |
| `coordinator` | OAK-D udev rules; coordinator stack (`/var/lib/coordinator/{config,ipc}`) |
| `pod` | pod stack (`/var/lib/pod/{config,captures}`); Phase 3 adds `dwc2`/`g_ether` + PPS overlays |

Manual run (without `one_time.sh`):

```bash
ansible-playbook host/ansible/site.yaml -e device_role=coordinator -e sync_repo=true
ansible-playbook host/ansible/site.yaml -e device_role=pod -e sync_repo=true
```

GHCR images are public; `docker login ghcr.io` is not required for `coord pull`.

Not in these roles yet: chrony/PPS, USB gadget `br0` (see [docs/architecture.md](../docs/architecture.md) and [docs/pi-zero-bringup.md](../docs/pi-zero-bringup.md)). Dockge was considered and **dropped** (see [docs/deployment-model.md](../docs/deployment-model.md)).

`/opt/stacks/<name>` is a **symlink** to the checkout's `stacks/<name>`, so `git pull` is the deploy -- no copy, no drift, deployed `.env` == repo `.env` by construction ([#48](https://github.com/symmatree/coordinator/issues/48)). The appliance deploy & config model is in [docs/deployment-model.md](../docs/deployment-model.md).
