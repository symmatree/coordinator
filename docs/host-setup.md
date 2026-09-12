# Coordinator node setup and maintenance

Blank SD card to a running coordinator (Pi 4B), and the commands for every update after
that. The campod (Pi Zero 2 W) counterpart is [campod.md](campod.md); the two devices share
one bootstrap, one CLI, and most of this shape.

OAK-D bench bring-up is separate: [bench-tracker.md](bench-tracker.md).

## Constraints that shape everything below

- **The image owns `/boot/firmware`; Ansible does not write it.** The FC UART, the
  Bluetooth trade, and the front-panel I2C bus all arrive in the flashed image
  ([#234](https://github.com/symmatree/coordinator/issues/234),
  [dotfiles-symm#38](https://github.com/symmatree/dotfiles-symm/pull/38)). A change to any
  of them is a **reflash**, not a playbook run.
- **There is no serial console.** The FC owns the primary UART (GPIO14/15) at 1.5 Mbaud, so
  the image strips `console=serial0,*` from `cmdline.txt` rather than sharing that line.
  **HDMI is the diagnostic** for a boot that does not come up. (The campod is the opposite:
  serial is its primary console.)
- **`/opt/stacks/coordinator` is a symlink into the checkout.** `git pull` *is* the config
  deploy -- no copy step, no on-box edit
  ([#48](https://github.com/symmatree/coordinator/issues/48)). Never hand-edit the deployed
  `compose.yaml`; change it in git and pull.
- **Passwordless sudo works, and bootstrap can be driven non-interactively.** The pinned
  Bookworm base ships `/etc/sudoers.d/010_pi-nopasswd` (`pi ALL=(ALL) NOPASSWD: ALL`, mode
  0440, root-owned) and `pi` is in both `adm` and `sudo`. The image build rsyncs the vendor
  rootfs verbatim (`-aHAX`), so mode and ownership carry, and `firstrun.sh`'s `userconf` call
  is a no-op when the account is not being renamed. The Pi OS change that removed this
  default is on the **Trixie** side of the split and does not reach this pin -- current
  Bookworm `raspberrypi-sys-mods` still ships the file. Relevant to
  [#236](https://github.com/symmatree/coordinator/issues/236): no sudoers work is needed.

## One-time: blank card to a running node

### 1. Flash

The images are generic and secret-free -- no login, no host keys, no WiFi. Identity is
injected at flash time, touching only the FAT partition. Full mechanics, including the
`fleet.env` secrets file and the WSL/UNC-path invocation:
`dotfiles-symm/pi-image/provision/README.md`.

Get the image from the `build-pi-image` run's artifacts and **extract the zip** -- GitHub
wraps every artifact, so point the flasher at the inner `.img.xz`, not the `.zip`.

```powershell
Get-Disk | Format-Table Number, FriendlyName, Size, BusType
.\Flash-Card.ps1 -Hostname coordinator -Disk 2 -Image $HOME\Downloads\coordinator-pi-<YYYYMMDD>.img.xz
```

### 2. First boot

Power on. `firstrun.sh` sets the hostname, renames the account, installs the SSH key,
writes the WiFi connection, then deletes itself and reboots. Expect two boots.

> **A failed first boot powers the board off.** Imager's generated unit carries
> `FailureAction=exit`, and for PID 1 that is a shutdown -- so a failure looks exactly like a
> hang: dark ACT LED, nothing on the network. If the node never appears, that is one of the
> three possibilities (still booting / wrong WiFi / first boot failed and powered off), and
> HDMI is how you tell them apart.

```bash
ssh pi@coordinator.local
uname -m            # aarch64
cat /etc/fleet-image # IMAGE / ROLE / SOURCE / BASE -- which image this card came from
findmnt -no FSTYPE,OPTIONS /   # btrfs ... subvol=/@
```

### 3. Clone and bootstrap

The clone is load-bearing: `/opt/stacks/coordinator` points into it, so nothing works until
it exists. Its absence presents confusingly -- `coord` reports *no stack*, not *no checkout*.

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/symmatree/coordinator.git
cd coordinator
./host/one_time.sh              # 'coordinator' is the default role
```

Re-run it after any reboot it asks for, until it exits clean. It is idempotent.

### 4. Check the host

```bash
newgrp docker          # once, if `docker ps` says permission denied
coord status           # resolves the sole stack under /opt/stacks
ls /dev/i2c-1          # front-panel bus (from the image)
ls -l /dev/serial0     # -> ttyAMA0, the PL011 (disable-bt, from the image)
```

## Every update

```bash
cd ~/coordinator
git pull
coord pull                    # new container images
coord start
```

| What changed | What to run |
|---|---|
| `stacks/coordinator/compose.yaml` (config lives in it -- there is no `.env`) | `git pull && coord start` |
| A container image (new build on `main`) | `coord pull` |
| An Ansible role, `bin/coord`, udev, or the boot unit | `./host/one_time.sh`, reboot if asked, re-run |
| Anything in `config.txt` / `cmdline.txt` | **reflash** -- the image owns it |
| OS packages | `./host/os_upgrade.sh` -- deliberate, never part of a config deploy |

`coord pull` runs `compose down` first, so it is a full stop of the stack, not a rolling
update. Fine on the bench; not something to do on a hot vehicle.

**Never build on the Pi.** CI builds arm64 and the Pi pulls.

## What comes from where

| Concern | Source |
|---|---|
| `enable_uart=1`, `dtoverlay=disable-bt`, `dtparam=i2c_arm=on`, `console=serial0` removal | **image** (`pi-image/roles/coordinator/config.append.txt`) |
| btrfs subvolume layout, `/etc/fleet-image` | **image** |
| Docker, `coord`, `/opt/stacks` symlink, `/var/lib/coordinator/{config,ipc,captures}` | Ansible (`docker-host` + `coord-stack`) |
| OAK-D udev rules, `oak_d.yaml` seed, host VIO tools, i2c-tools | Ansible (`coordinator` role) |
| Serial getty disable on the FC UART | Ansible (a unit, not a boot file) |
| Auto-start on boot, persistent journald | Ansible (`power-resilience.yml`) |
| `br0` campod bridge, gadget interface enslavement | Ansible (`coordinator` role) |
| `compose.yaml` (values and container tags included) | git, through the symlink |

## Troubleshooting

| Symptom | Check |
|---|---|
| Never appears on the network | HDMI. See the first-boot warning above -- it may be powered off, not hung |
| `exec format error` | 32-bit OS; the fleet images are arm64 |
| `permission denied` on `docker ps` | `newgrp docker` or re-login (not a reboot) |
| `coord: compose file not found` | The checkout is missing -- `/opt/stacks/coordinator` is a symlink into it |
| FC link silent or garbled | `ls -l /dev/serial0` should be `ttyAMA0`; mini-UART garbles because `arm_boost=1` moves the VPU clock |
| MAVLink stream corrupt on an unknown card | `cat /proc/cmdline` -- `console=serial0` together with `enable_uart=1` puts console bytes on the FC's port. The current image makes this combination impossible |
| Node is set up but not at head | `cat /etc/fleet-image` -- config converges over SSH, but the image only changes by reflashing |

## Related

- [campod.md](campod.md) -- the Pi Zero counterpart, same bootstrap and CLI
- [deployment-model.md](deployment-model.md) -- why config is git-authoritative with no on-box override
- [power-loss-filesystem.md](power-loss-filesystem.md) -- the btrfs substrate the image lays down
- [architecture.md](architecture.md) -- host vs container split, runtime paths
- `dotfiles-symm/pi-image/` -- the image build; `provision/` -- per-unit identity injection
