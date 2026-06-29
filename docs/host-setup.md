# Host setup: Pi 4B to coordinator stack

One-time path from a fresh SD card to a host that can run `coord pull` / `coord start` for the `tracker` profile. OAK-D bench steps live in [bench-tracker.md](bench-tracker.md).

Automated host bootstrap: [host/one_time.sh](../host/one_time.sh) (installs Ansible, runs [host/ansible/site.yaml](../host/ansible/site.yaml) with `device_role=coordinator`). Manual narrative below ends at that script; bench bring-up is separate. The pod (Pi Zero) equivalent is [pi-zero-host-setup.md](pi-zero-host-setup.md).

## Resolved choices (issue #5)

| Topic | Decision |
|-------|----------|
| Base OS | Stock **Raspberry Pi OS (64-bit)** via Raspberry Pi Imager -- not custom pi-gen ([architecture.md](architecture.md)) |
| GHCR | `ghcr.io/symmatree/coordinator-vio-tracker` is **public**; no `docker login ghcr.io` for pull |
| GitHub clone | Repo is **public**; HTTPS clone needs no `gh auth login` |
| Standalone `docker pull` | **Optional diagnostic only** -- `coord pull` already pulls the image; skip unless debugging registry/network |
| Install path | Clone repo on Pi, run `host/one_time.sh` (wired with `device_role=coordinator` + `sync_repo=true`) |
| Reboot | Ansible reboots when `/var/run/reboot-required` is set (kernel, firmware, or modules after `dist-upgrade`) -- **not** for docker group membership |
| Repeat until stable | Run `./host/one_time.sh` again after each reboot until it exits without triggering one |
| Deployer alternative | Run Ansible from a laptop against the Pi inventory instead of installing Ansible on the Pi -- lighter if local bootstrap becomes painful (not needed yet) |

## Imager settings (tracker bench)

Use **Raspberry Pi Imager 2.0+** and pick **official Raspberry Pi OS (64-bit)** from the online OS list (not a bare local `.img` file). Nothing beyond 64-bit is required for issue #5; later host work (chrony, USB `br0`) adds boot config in follow-up playbooks.

### Pre-flash configuration (what Imager still supports)

Imager **did not** drop OS customization for official Pi OS images. In [Imager 2.0](https://www.raspberrypi.com/news/a-new-raspberry-pi-imager/) it moved from a hidden "advanced options" dialog into wizard **step 4 -- Configure your system** (hostname, locale, user, WiFi, SSH keys, Raspberry Pi Connect, etc.). That is still pre-imaging configuration baked into the written image.

What **did** change in 2.0 (and may match what you heard):

| Case | Pre-flash customize in Imager? |
|------|--------------------------------|
| Official **Raspberry Pi OS** from Imager's online list, Imager **2.0+** | Yes -- wizard step 4 |
| **Trixie** + Imager **1.9.x** | No -- old Imager cannot apply Trixie's `cloudinit-rpi` format; use Imager 2.0 or skip and use the [first-boot wizard](https://www.raspberrypi.com/documentation/computers/getting-started.html#configuration-on-first-boot) on a display |
| **Local/custom `.img`** (not from the official list) | No by default -- needs a [custom repository JSON](https://www.raspberrypi.com/news/how-to-add-your-own-images-to-imager/) with the right `init_format` |

For headless bench without Imager step 4: use **Ethernet**, or attach a **display/keyboard once** for the first-boot wizard, or sign in to **Raspberry Pi Connect** during imaging (Imager 2.0).

**Lab WiFi while flashing:** Have the bench/lab WiFi network available when you run Imager step 4 and enter SSID + credentials there. That gives the Pi onboard WiFi on first boot for SSH, `git clone`, and `coord pull` during setup and testing. In flight the coordinator will not rely on upstream WiFi (Pi Zero USB gadget network only); disabling WiFi in flight is a later ops concern, not an Imager step.

| Setting | Recommendation | Why |
|---------|----------------|-----|
| OS | Raspberry Pi OS (64-bit), official list entry | Container images are `linux/arm64` |
| Imager | 2.0 or newer | Matches current Pi OS customization mechanism |
| Variant | Desktop or Lite | Either works; Lite is fine headless if step 4 sets SSH/WiFi or you use Ethernet |
| Hostname | e.g. `coordinator` (step 4) | Matches `HOSTNAME` in stack `.env` (cosmetic) |
| User / password | Your operator account (step 4) | SSH and Docker group membership |
| SSH | Enable in step 4, or use first-boot wizard / Pi Connect | Headless bring-up |
| WiFi | **Lab SSID in step 4** (and/or Ethernet on the bench) | Built-in Pi 4B WiFi; no dongle. Useful for prep; not the in-flight network |
| Storage | Quality SD or USB boot later | Per [virtualization-study](https://github.com/symmatree/fables/blob/main/fables/Drones/coordinator/virtualization-study.md): avoid heavy control-plane IOPS on SD; Docker Compose idle I/O is low |

### Not needed at image install (later host playbooks)

These are **out of scope** for the tracker-only bootstrap and **do not** belong in Imager step 4 or the first `./host/one_time.sh` run:

| Future subsystem | Host change (later playbook) | Imager / first bootstrap OK now? |
|------------------|------------------------------|----------------------------------|
| chrony + PPS (DS3234 SQW to GPIO) | `dtoverlay=pps-gpio,gpiopin=18` in `/boot/firmware/config.txt`, chrony on **host** | Yes |
| Pi Zero USB gadget `br0` | Host bridge + DHCP (NetworkManager or systemd-networkd); `dwc2`/`g_ether` on **Zeros**, not coordinator | Yes |
| FC MAVLink UART | `enable_uart=1`, serial console off primary UART | Yes (no FC in `tracker` profile) |
| WiFi AP/station utility | NetworkManager / D-Bus (host or utility container) | Yes |
| SparkFun Top pHAT 2.4" TFT | Compile `sfe-topphat-overlay.dts` to `.dtbo`, install under `/boot/firmware/overlays/`, add `dtoverlay=rpi-display,...` to `/boot/firmware/config.txt` ([SparkFun guide](https://learn.sparkfun.com/tutorials/sparkfun-top-phat-hookup-guide/24-tft-display-linux-54-update)) | Yes -- add when the pHAT is on the board, not at SD flash time |

The study recommends **stock Pi OS + Docker Compose** with chrony and `br0` on the host kernel -- consistent with Imager defaults plus Ansible, not a custom image.

## 1. Flash and first boot

1. Flash the SD card: Imager 2.0+, official Pi OS (64-bit).
2. At step 4: username "pi", password from 1password "rpi/pi" item. Hostname "coordinator". Provide house wifi. Enable SSH, provide public key for OnePKey ssh identity.
2. Boot the Pi 4B, connect power and network (Ethernet, Imager WiFi, or wizard-configured WiFi).
3. SSH in: `ssh <user>@<hostname>.local` (or the Pi IP from your router).

Optional sanity check:

```bash
uname -m    # expect aarch64
lsb_release -a
```

## 2. Clone the coordinator repo

Public repo -- no GitHub CLI or token required for HTTPS:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/symmatree/coordinator.git
cd coordinator
```

Optional: install [GitHub CLI](https://cli.github.com/) and `gh auth login` if you prefer `gh repo clone` or will push from this Pi later. Not required for read-only clone.

## 3. Run one-time host bootstrap

From the repo root (or from `host/` -- the script resolves its own directory):

```bash
./host/one_time.sh
```

What it does:

1. `apt update`, `dist-upgrade`, install `ansible` and minimal deps (same shape as [dotfiles `one_time.sh`](https://github.com/symmatree/dotfiles-symm/blob/main/ubuntu-zsh/one_time.sh)).
2. Ansible: Docker CE + Compose plugin, `/opt/stacks/coordinator`, `/var/lib/coordinator/{config,ipc}`, sync stack + `coord` CLI from this checkout.
3. If `dist-upgrade` left `/var/run/reboot-required` set (new kernel, firmware, or modules), Ansible **reboots and waits** for the host to return.

**Repeat until stable:** run `./host/one_time.sh` again after any reboot until the script prints `one_time: complete (coordinator, no pending kernel/firmware reboot).` Fresh images often need one cycle; idempotent re-runs should not reboot again.

If bootstrap fails on architecture, the playbook requires **aarch64** (64-bit Pi OS).

## 4. After bootstrap (no OAK-D required yet)

If `docker ps` reports permission denied, run `newgrp docker` once or log out and back in (docker group membership does not trigger a reboot).

Optional registry check (not required when GHCR is public):

```bash
docker pull ghcr.io/symmatree/coordinator-vio-tracker:main
```

Confirm stack files:

```bash
ls /opt/stacks/coordinator/
coord status   # may show no containers until start
```

Default `.env` already sets `VIO_TRACKER_VERSION=main` and `COMPOSE_PROFILES=tracker`.

## 5. Bench: OAK-D + vio-tracker

Attach the OAK-D to a **USB 3** port on the Pi, then:

```bash
coord pull
coord start
coord logs -f vio-tracker
```

Full checklist and failure modes: [bench-tracker.md](bench-tracker.md).

## Troubleshooting

| Symptom | Check |
|---------|--------|
| `exec format error` / wrong arch | 32-bit Pi OS; re-flash **64-bit** image |
| `permission denied` on `docker ps` | `newgrp docker` or re-login (not a reboot) |
| Script exits 1, reboot-required still set | Run `./host/one_time.sh` again after the host is back |
| Ansible `apt` / Docker repo errors | Pi has network; `ansible_distribution_release` matches your Pi OS codename |
| Playbook OK but no `/opt/stacks/coordinator/compose.yaml` | Re-run with `-e sync_repo=true` (default in `one_time.sh`) |

## Out of scope (separate issues)

- Dockge install and stack registration
- chrony + PPS overlay and config
- USB gadget `br0` + dnsmasq for Pi Zeros
- `vio-estimator`, `coordinator-mavlink` images and profiles

See [architecture.md](architecture.md) and coordinator issue #5 on GitHub.
