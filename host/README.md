# Host provisioning

Ansible for Rekon devices (Docker, stack paths, `coord` CLI). One shared playbook serves both the **coordinator** (Pi 4B) and the **campod** (Pi Zero 2 W); `device_role` selects the device.

**Driven from another machine, over SSH.** There is no bootstrap script on the device and nothing to install there first: `python3`, `sudo` with passwordless and sshd all ship in the image, so a freshly flashed card is manageable as-is. `host/one_time.sh` and `host/lib/usr-rw.sh` are gone -- they existed only because a device bootstrapping *itself* cannot use ansible to install ansible, so the remount-then-apt ordering had to be survived in bash first.

## First-time setup

Full narratives: coordinator [docs/host-setup.md](../docs/host-setup.md), campod [docs/campod-software.md](../docs/campod-software.md).

```bash
ansible-playbook host/ansible/site.yaml -i '<addr>,' -u pi \
  -e device_role=coordinator -e manage_checkout=true
```

A bare `'<addr>,'` is a valid inventory, so no inventory file and no DNS are needed for one device; pass a real `-i` for more. `manage_checkout=true` creates the on-device clone that `/opt/stacks/<role>` symlinks into -- leave it off against a device someone is editing on, or it resets their working tree.

The play remounts `/usr` read-write (it ships read-only), installs prerequisites, converges config, and **reboots and waits** if a kernel/firmware change or the `/usr` hatch requires it. Driving from outside is what makes that possible: [#113](https://github.com/symmatree/coordinator/issues/113) had to remove auto-reboot because the play ran locally, where ansible refuses to reboot its own control node.

It is a **config-only** deploy -- it does not `dist-upgrade`. That is a separate deliberate playbook, [ansible/os-upgrade.yaml](ansible/os-upgrade.yaml) ([#48](https://github.com/symmatree/coordinator/issues/48)).

Then bench: coordinator tracker [docs/bench-tracker.md](../docs/bench-tracker.md); campod [docs/campod-software.md](../docs/campod-software.md).

## site.yaml and roles

`site.yaml` layers a device role on top of the shared `docker-host` role:

| Role | Scope |
|------|-------|
| `bootstrap` | **Shared** -- the `/usr` read-write hatch, prerequisites (`git`), and optionally the on-device checkout |
| `docker-host` | **Shared** -- Docker Engine + Compose plugin, docker group, service |
| `coord-stack` | **Shared** -- symlinks `/opt/stacks/<name>` to the checkout (`git pull` is the deploy, [#48](https://github.com/symmatree/coordinator/issues/48)), state dirs, installs `coord` |
| `coordinator` | OAK-D udev rules; coordinator stack (`/var/lib/coordinator/{config,ipc}`) |
| `campod` | campod stack (`/var/lib/campod/{config,captures}`); Phase 3 adds `dwc2`/`g_ether` + PPS overlays |

Both roles, and the in-place OS upgrade:

```bash
ansible-playbook host/ansible/site.yaml -i '<addr>,' -u pi -e device_role=coordinator
ansible-playbook host/ansible/site.yaml -i '<addr>,' -u pi -e device_role=campod
ansible-playbook host/ansible/os-upgrade.yaml -i '<addr>,' -u pi
```

GHCR images are public; `docker login ghcr.io` is not required for `coord pull`.

Not in these roles yet: chrony/PPS, USB gadget `br0` (see [docs/architecture.md](../docs/architecture.md) and [docs/campod.md](../docs/campod.md)). Dockge was considered and **dropped** (see [docs/deployment-model.md](../docs/deployment-model.md)).

`/opt/stacks/<name>` is a **symlink** to the checkout's `stacks/<name>`, so `git pull` is the deploy -- no copy, no drift, deployed `compose.yaml` == repo `compose.yaml` by construction ([#48](https://github.com/symmatree/coordinator/issues/48)). The appliance deploy & config model is in [docs/deployment-model.md](../docs/deployment-model.md).
