# Campod node setup and maintenance

The **campod** is an arm-mounted Pi Zero 2 W + Camera Module 3 (IMX708) + ADXL345
vibration sensors. This is the operator doc: how to bring a node up from a blank card, and
what to run on every update afterwards. It is deliberately command-oriented; the reasoning
lives in the docs it points at.

> **Naming.** Code identifiers say `pod`, not `campod` -- `host/ansible/roles/pod`,
> `stacks/pod/`, `POD_*` env, `one_time.sh pod`. Renaming them is a separate mechanical
> change; this doc uses the operator's word and the code's identifiers side by side.

| Where the reasoning lives | |
|---|---|
| Plan of record, phases, why the pod lives in this repo | [pi-zero-bringup.md](pi-zero-bringup.md) |
| Capture container, ADXL345 reader, SPI settings, readout constant | [containers/pod-camera/README.md](../containers/pod-camera/README.md) |
| Airframe/payload design, aim geometry, vibration rationale | [rekon10/arm-pods.md](rekon10/arm-pods.md) |
| Filesystem choice and power-loss behaviour | [power-loss-filesystem.md](power-loss-filesystem.md) |
| Coordinator equivalent of this doc | [host-setup.md](host-setup.md) |
| The vibration question the pod exists to answer | [#211](https://github.com/symmatree/coordinator/issues/211) |

---

## One-time: blank card to a running node

### 1. Flash

**Raspberry Pi Imager 2.0+**, **Raspberry Pi OS Lite (64-bit)** from Imager's *online list*
-- not a downloaded `.img`. Two traps, both silent:

- **Imager 1.x cannot customise Trixie.** It assumes the old `firstrun.sh` format, writes
  one, and Trixie never runs it. No user, no SSH, no WiFi, and nothing says so. Current Pi
  OS Lite arm64 declares `init_format: cloudinit-rpi`; only Imager 2.x honours it.
- **A local `.img` gets `init_format: none`** in Imager 2.x, so customisation is silently
  unavailable. Pick from the online list, or build a local manifest with Imager's
  `create_local_json.py`.

Imager step 4:

| Setting | Value |
|---------|-------|
| Hostname | `pod-NNE` (or the camera-node name from `rekon10/arm-pods.md`) |
| User / password | operator account |
| SSH | enable, public key |
| WiFi | **lab SSID** -- for `git clone` and `coord pull` during bring-up, not for flight |

Two things Imager will *not* do for you, so don't go looking:

- **The Interfaces & Features step (SPI, I2C, USB gadget) does not appear.** It is gated on
  a per-device hardware capability list, and the live manifest ships `"capabilities": []`
  for every model including the Zero 2 W. SPI is handled by the pod role below instead.
- **Passwordless sudo is not offered** for this image (its manifest advertises only
  `rpi_connect`), and Pi OS disabled passwordless sudo by default in 2026-04. Expect to
  type the password during bootstrap; that is fine interactively and will hang a
  non-interactive run.

Filesystem: btrfs. `CONFIG_BTRFS_FS=m` and `btrfs.ko` ship in the Pi OS kernel, so no
custom kernel is needed -- but it is a **module**, so root-on-btrfs needs it in the
initramfs. See [power-loss-filesystem.md](power-loss-filesystem.md).

### 2. Clone and bootstrap

```bash
ssh <user>@pod-NNE.local
uname -m                      # expect aarch64

sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/symmatree/coordinator.git
cd coordinator
./host/one_time.sh pod
```

`one_time.sh pod` installs Ansible, then runs the shared playbook with
`device_role=pod`:

1. `docker-host` role -- Docker CE + Compose plugin, docker group, service enabled.
2. `pod` role -- `/var/lib/pod/{config,captures}`, **symlinks** `/opt/stacks/pod` to this
   checkout's `stacks/pod/`, installs the `coord` CLI, and enables **SPI0** in
   `/boot/firmware/config.txt`.

It does **not** reboot itself ([#113](https://github.com/symmatree/coordinator/issues/113)
removed that -- it runs locally, so it cannot reboot out from under its own play). It exits
non-zero while `/var/run/reboot-required` is set. **Re-run it after each reboot until it
exits clean.** The SPI change alone guarantees at least one cycle on a fresh card.

```bash
sudo reboot            # after the first run, for the SPI overlay
# ... then, once it is back:
cd coordinator && ./host/one_time.sh pod
```

### 3. Check the host

```bash
newgrp docker                 # or re-login, if `docker ps` says permission denied
docker ps
ls -l /opt/stacks/pod/        # symlink into the checkout
ls /dev/spidev*               # expect spidev0.0 and spidev0.1
coord status                  # empty until `coord start`
```

`/dev/spidev0.*` missing means the SPI overlay has not taken effect -- reboot and re-run
`one_time.sh pod`.

### 4. Wire the sensors

Full pin table, connector and harness guidance: `#211` and
[containers/pod-camera/README.md](../containers/pod-camera/README.md). Everything lives in
one contiguous 2x5 block on the Zero's 40-pin header:

| odd | | even | |
|---|---|---|---|
| **17** | 3V3 | **18** | GPIO24 (spare -- INT) |
| **19** | GPIO10 MOSI -> `SDA` | **20** | Ground |
| **21** | GPIO9 MISO -> `SDO` | **22** | GPIO25 (spare -- INT) |
| **23** | GPIO11 SCLK -> `SCL` | **24** | GPIO8 CE0 -> camera `CS` |
| **25** | Ground | **26** | GPIO7 CE1 -> arm `CS` |

Power the breakout from **3V3 (pin 17)**, not 5 V: the ADXL345 draws 145 uA, where an LDO's
dropout is millivolts, so the regulator passes 3.3 V straight through and nothing on the
harness is above the Pi's own logic level.

### 5. Turn on capture

`stacks/pod/.env` ships `COMPOSE_PROFILES=capture`, so:

```bash
coord pull                    # ~241 MB compressed for pod-camera
coord start
coord logs -f pod-camera
```

Expect, in the log:

```
pod: session 2026...Z
capture: exposure pinned to 5000 us, gain left on AEGC
capture: node=pod-NNE dir=/captures/pod-NNE/<session> size=4608x2592 ...
accel: camera: DEVID ok, self-test PASS (x=+0.99g y=-0.99g z=+1.50g)
```

The accelerometer reader is **opt-in and off by default** -- `POD_ACCEL_DEVICES` is empty
in `stacks/pod/.env` so a node without sensors wired does not spew retries. Set it (in git,
see below) to switch it on:

```
POD_ACCEL_DEVICES=camera:/dev/spidev0.0,arm:/dev/spidev0.1
POD_ACCEL_SEPARATION_M=<measured camera-to-arm baseline>
```

Done when a session directory holds frames **and** a continuous accel record over the same
interval:

```bash
ls /var/lib/pod/captures/pod-NNE/<session>/
# pod-NNE_00000000_...jpg  pod-NNE_00000000_...json  accel-camera.jsonl  accel-arm.jsonl
```

---

## Every update

**Config and code are the same thing here.** `/opt/stacks/pod` is a *symlink* into the
checkout, so `git pull` **is** the config deploy -- there is no copy step and no on-box
edit to make ([#48](https://github.com/symmatree/coordinator/issues/48)). Never hand-edit
the deployed `.env`; change it in git and pull.

```bash
cd ~/coordinator
git pull
coord pull                    # new container images
coord start
```

Re-run the bootstrap only when the **host** changes -- a new role task, a new config.txt
entry, a Docker or Ansible bump:

```bash
./host/one_time.sh pod        # re-run after any reboot it asks for, until clean
```

It is idempotent; running it when nothing changed is cheap and safe.

| What changed | What to run |
|---|---|
| `stacks/pod/.env`, `compose.yaml` | `git pull && coord start` |
| A container image (new build on `main`) | `coord pull` |
| `containers/pod-camera/*` merged upstream | `coord pull` (CI builds it; never build on the Zero) |
| An Ansible role, or anything in `/boot/firmware/config.txt` | `./host/one_time.sh pod`, reboot, re-run |
| OS packages | `./host/os_upgrade.sh` -- deliberate, not part of a config deploy |

**Never build on the Zero.** CI builds arm64 and the Zero pulls. 512 MB of RAM is the
binding constraint on this device and a build will not fit.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `exec format error` | 32-bit OS flashed; re-flash **64-bit** Lite |
| `permission denied` on `docker ps` | `newgrp docker` or re-login (not a reboot) |
| `one_time.sh` exits 1, reboot-required set | reboot, run it again -- expected at least once on a fresh card |
| `accel: ... does not exist -- is dtparam=spi=on set?` | `ls /dev/spidev*`; if empty, reboot and re-run `one_time.sh pod` |
| `accel: DEVID 0x00, expected 0xE5` | wiring, chip select, or SPI mode -- the bus is reaching nothing |
| `accel: self-test FAIL` | sensor is talking but not moving: cold joint on a supply pin, or a dead part |
| `capture: WARNING could not pin exposure` | libcamera in the image is older than the exposure/gain mode split; check `RPI_SUITE` matches the host OS |
| libcamera reports "no cameras" | container/host suite mismatch -- `RPI_SUITE` must track the host Pi OS release |
| Out-of-memory during bootstrap | expected pressure point on 512 MB; confirm zram/swap is on (Pi OS default) |
| `coord` picks the wrong stack | only the pod stack belongs under `/opt/stacks/` on a campod |

---

## Open: how updates reach a flying set of nodes

Everything above assumes the node has its own route to GitHub and GHCR, which is true on
the bench over lab WiFi and false in the field. The options, with the numbers that matter:

- **pod-camera is ~241 MB compressed.** Five nodes pulling independently is ~1.2 GB of WAN
  traffic per image bump; one coordinator pull plus local distribution is 241 MB of WAN and
  ~1 GB over USB.
- **The USB gadget link is not the constraint.** An image update is an occasional bulk
  transfer with no latency requirement, not a stream -- capture data never leaves the
  node's own SD by design. Even a pessimistic few MB/s finishes in minutes.
- **A transparent registry mirror does not work for GHCR.** Docker's `registry-mirrors` is
  Docker Hub only -- *"It's currently not possible to mirror another private registry. Only
  the central Hub can be mirrored."* Serving images locally therefore means either a
  registry on the coordinator plus a registry-prefix in the image ref, or
  `docker save | ssh | docker load`.

Not decided. Related: [#12](https://github.com/symmatree/coordinator/issues/12) (coordinator
bridge), [#24](https://github.com/symmatree/coordinator/issues/24) (pod gadget net).
