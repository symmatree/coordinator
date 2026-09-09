# Campod node setup and maintenance

The **campod** is an arm-mounted Pi Zero 2 W + Camera Module 3 (IMX708) + ADXL345
vibration sensors. This is the operator doc: how to bring a node up from a blank card, and
what to run on every update afterwards. It is deliberately command-oriented; the reasoning
lives in the docs it points at.

> **Naming.** The device is a **campod**, everywhere: hostnames (`campod-ne` ...), the
> ansible role, `stacks/campod/`, `/opt/stacks/campod`, `/var/lib/campod`, `CAMPOD_*` env,
> `one_time.sh campod`, `containers/campod-camera`, and the image role in `dotfiles-symm`.
>
> A bare "pod" already denoted three other things -- the Kubernetes object, this repo's
> ansible role, and (per [rekon10/arm-pods.md](rekon10/arm-pods.md)) the physical arm
> enclosure that holds one or two of these hosts. None of the three is the machine. Where
> "pod" survives in this repo it means one of those other things and is left alone.

> **Cross-repo tripwire: `/var/lib/campod` is load-bearing.** The image mounts the `@data`
> btrfs subvolume there **specifically** to match `coord_state_root` in
> `host/ansible/roles/campod/tasks/main.yml`. Move that path on either side alone and
> captures land silently on `@var` instead of the capture subvolume -- no error, no warning,
> and the power-loss properties the subvolume exists for quietly stop applying.
>
> **This path just moved** (`/var/lib/pod` -> `/var/lib/campod`), so it needs
> `DATA_MOUNT` in `dotfiles-symm/pi-image/roles/campod.env` to move in the same window.
> Done now while nothing is deployed and no captures exist; after four units are stamped
> and capturing it is the divergence above.

| Where the reasoning lives | |
|---|---|
| Capture container, ADXL345 reader, SPI settings, readout constant | [containers/campod-camera/README.md](../containers/campod-camera/README.md) |
| Airframe/payload design, aim geometry, vibration rationale | [rekon10/arm-pods.md](rekon10/arm-pods.md) |
| Filesystem choice and power-loss behaviour | [power-loss-filesystem.md](power-loss-filesystem.md) |
| Coordinator equivalent of this doc | [host-setup.md](host-setup.md) |
| The vibration question the campod exists to answer | [#211](https://github.com/symmatree/coordinator/issues/211) |

---

## Why the campod lives in this repo

The campod and the coordinator have to collaborate -- gadget network, time, start/stop,
status -- so they share one operator model rather than maintaining two parallel copies:

| Shared asset | How it serves both |
|--------------|--------------------|
| `bin/coord` | One stack-aware CLI. Each device runs only its own stack under `/opt/stacks/*`; `coord` defaults to the sole installed stack. |
| `host/ansible/roles/docker-host` | Docker engine, group, state dirs -- identical on Pi 4B and Pi Zero. |
| `host/one_time.sh [coordinator\|campod]` | One bootstrap entrypoint; the role argument selects the device. |
| `/opt/stacks/<name>` | Both devices lay their one stack there. |

Device-specific code stays small: `roles/campod`, `containers/campod-camera/`, `stacks/campod/`.

## Constraints that shape everything below

- **512 MB of RAM is the binding limit.** Estimate, not measured: Pi OS Lite headless
  ~100 MB + `dockerd`/`containerd` ~100 MB + one `picamera2`-class container ~100 MB, with
  zram/swap for spikes. Capture is 1-2 Hz at a very low duty cycle, so steady-state churn
  is low.
- **Never build on the Zero.** CI builds arm64 and the Zero pulls. A build will not fit.
- **Camera passthrough is the fiddly part, not resources.** libcamera in a container needs
  `/dev/video*`, `/dev/media*`, `/dev/dma_heap`, vchiq and `/run/udev` visible inside;
  `stacks/campod/compose.yaml` uses `privileged: true` + `/run/udev`, with explicit device
  mounts as the fallback if enumeration ever fails.
- **Thermal is handled in hardware**, not by the runtime -- full-length heatsinks and an
  open centre channel for prop-wash. See [rekon10/arm-pods.md](rekon10/arm-pods.md).

---

## One-time: blank card to a running node

### 1. Flash

**Campods boot the btrfs image, not stock Pi OS Lite.** This reverses the "SD image" row
in [#211](https://github.com/symmatree/coordinator/issues/211)'s settled table -- see the
[owner decision of 2026-09-06](https://github.com/symmatree/coordinator/issues/211#issuecomment-5559432953).
Reasoning: validating the stack on one storage layout and then switching means validating a
stack you throw away.

The image and its per-unit provisioning live in **`dotfiles-symm/pi-image`**, not here:

- `roles/campod.env` -- Zero 2 W / SD knobs: `DATA_MOUNT=/var/lib/campod`, `METADATA=single`,
  and a `config.append.txt` carrying `enable_uart=1` + `dtoverlay=disable-bt` (serial
  console) and `dtoverlay=dwc2,dr_mode=peripheral` (gadget net, device tree, must come
  from the image).
- `pi-image/provision/` -- `firstrun.sh` template + `Flash-Card.ps1`. Per-unit identity
  (hostname, user, SSH key, WiFi) is injected onto the FAT partition **at flash time**;
  secrets stay in a gitignored `fleet.env` on the operator's machine, so the image itself
  stays secret-free.

**Do not use Imager's step 4 for this.** rpi-imager offers no customisation for a
locally-selected `.img.xz` (`OSSelectionStep.qml`: *"For custom images, customization is
not supported"*), so the wizard path that works for stock Pi OS does not apply. Provision
via `pi-image/provision/` instead.

**Stage the rollout.** Flash **one** campod with the btrfs image and prove it boots before
touching the rest, and keep that unit's stock Pi OS Lite card. If it doesn't come up,
swapping the card isolates the fault: boots on stock means the image, boots on neither
means the hardware. That preserves the one-candidate-cause property #211 wanted, without
deferring the image.

> **Known, unexplained, and silent.** On the first card built, the **very first boot hung
> partway through `firstrun.sh`** -- no network, dark ACT LED, `firstrun.sh` still on the
> card and `cmdline.txt` still carrying `systemd.run=`. A power cycle got through cleanly
> and it has not recurred. No explanation. If a campod comes up dead on its first boot,
> that is the shape to look for, and **it is invisible without the serial console** --
> which is why `enable_uart=1` + `dtoverlay=disable-bt` are in the image. Both are needed
> on a Zero 2 W: without them `console=serial0` is silent, because PL011 goes to Bluetooth
> and the mini-UART is disabled.

btrfs boot on a Zero 2 W on SD is **proven** as of 2026-09-07 -- `@` and `@usr` both mount,
the initramfs carries btrfs, and `initramfs8` loads under `auto_initramfs=1`. That was
[#96](https://github.com/symmatree/coordinator/issues/96)'s standing gate.

### 2. Clone and bootstrap

```bash
ssh <user>@campod-sw.local
uname -m                      # expect aarch64

sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/symmatree/coordinator.git
cd coordinator
./host/one_time.sh campod
```

`one_time.sh campod` installs Ansible, then runs the shared playbook with
`device_role=campod`:

1. `docker-host` role -- Docker CE + Compose plugin, docker group, service enabled.
2. `campod` role -- `/var/lib/campod/{config,captures}`, **symlinks** `/opt/stacks/campod` to this
   checkout's `stacks/campod/`, and installs the `coord` CLI. It deliberately writes nothing
   under `/boot/firmware`: device tree comes from the image (see below).

It does **not** reboot itself ([#113](https://github.com/symmatree/coordinator/issues/113)
removed that -- it runs locally, so it cannot reboot out from under its own play). It exits
non-zero while `/var/run/reboot-required` is set. **Re-run it after each reboot until it
exits clean.** With device tree coming from the image there is nothing here that forces a
reboot by itself, so a clean card should normally go through in one pass.

**SPI0 comes from the image**, not from ansible: `dtparam=spi=on` lives in
`dotfiles-symm/pi-image/roles/campod/config.append.txt` alongside the serial console and
`dtoverlay=dwc2`. It is device tree, it is inert with nothing on the bus, and keeping
ansible out of `/boot/firmware` matters more than it looks -- that partition is FAT on an
SD card in a vehicle that loses power abruptly, with none of the checksumming or CoW the
btrfs subvolumes give the rest of the disk.

> **Cards flashed before that line landed do not have it.** `ls /dev/spidev*` is the check
> in the next section. If it is empty, the card predates the change: reflash from a current
> image (clean), or add `dtparam=spi=on` to `/boot/firmware/config.txt` by hand and reboot
> (fast, and a stopgap rather than a pattern -- the image is the source of truth).

### 3. Check the host

```bash
newgrp docker                 # or re-login, if `docker ps` says permission denied
docker ps
ls -l /opt/stacks/campod/        # symlink into the checkout
ls /dev/spidev*               # expect spidev0.0 and spidev0.1
coord status                  # empty until `coord start`
```

`/dev/spidev0.*` missing means the card's `config.txt` has no `dtparam=spi=on` -- see the
note above. Re-running `one_time.sh campod` will not fix it; that line comes from the image.

### 4. Wire the sensors

Full pin table, connector and harness guidance: `#211` and
[containers/campod-camera/README.md](../containers/campod-camera/README.md). Everything lives in
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

`stacks/campod/.env` ships `COMPOSE_PROFILES=capture`, so:

```bash
coord pull                    # ~241 MB compressed for campod-camera
coord start
coord logs -f campod-camera
```

Expect, in the log:

```
pod: session 2026...Z
capture: exposure pinned to 5000 us, gain left on AEGC
capture: node=campod-sw dir=/captures/campod-sw/<session> size=4608x2592 ...
accel: camera: DEVID ok, self-test PASS (x=+0.99g y=-0.99g z=+1.50g)
```

The accelerometer reader is **opt-in and off by default** -- `CAMPOD_ACCEL_DEVICES` is empty
in `stacks/campod/.env` so a node without sensors wired does not spew retries. Set it (in git,
see below) to switch it on:

```
CAMPOD_ACCEL_DEVICES=camera:/dev/spidev0.0,arm:/dev/spidev0.1
CAMPOD_ACCEL_SEPARATION_M=<measured camera-to-arm baseline>
```

Done when a session directory holds frames **and** a continuous accel record over the same
interval:

```bash
ls /var/lib/campod/captures/campod-sw/<session>/
# campod-sw_00000000_...jpg  campod-sw_00000000_...json  accel-camera.jsonl  accel-arm.jsonl
```

---

## Every update

**Config and code are the same thing here.** `/opt/stacks/campod` is a *symlink* into the
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
./host/one_time.sh campod        # re-run after any reboot it asks for, until clean
```

It is idempotent; running it when nothing changed is cheap and safe.

| What changed | What to run |
|---|---|
| `stacks/campod/.env`, `compose.yaml` | `git pull && coord start` |
| A container image (new build on `main`) | `coord pull` |
| `containers/campod-camera/*` merged upstream | `coord pull` (CI builds it; never build on the Zero) |
| An Ansible role, or anything in `/boot/firmware/config.txt` | `./host/one_time.sh campod`, reboot, re-run |
| OS packages | `./host/os_upgrade.sh` -- deliberate, not part of a config deploy |

**Never build on the Zero.** CI builds arm64 and the Zero pulls. 512 MB of RAM is the
binding constraint on this device and a build will not fit.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `exec format error` | wrong artifact flashed -- confirm it is the campod image, not a stock card. (The campod image is always arm64, so this cannot come from picking a 32-bit variant; there isn't one.) |
| `permission denied` on `docker ps` | `newgrp docker` or re-login (not a reboot) |
| `one_time.sh` exits 1, reboot-required set | reboot, run it again -- expected at least once on a fresh card |
| `accel: ... does not exist -- is dtparam=spi=on set?` | `ls /dev/spidev*`; if empty, reboot and re-run `one_time.sh campod` |
| `accel: DEVID 0x00, expected 0xE5` | wiring, chip select, or SPI mode -- the bus is reaching nothing |
| `accel: self-test FAIL` | sensor is talking but not moving: cold joint on a supply pin, or a dead part |
| `capture: WARNING could not pin exposure` | container libcamera predates the exposure/gain mode split (needs >= 0.4). Check `RPI_SUITE` matches **the campod image's pinned suite** -- `dotfiles-symm/pi-image/build-image.sh`, currently Bookworm -- not whatever Pi OS ships today |
| libcamera reports "no cameras" | container/host suite mismatch. `RPI_SUITE` tracks **the campod image's pinned suite** (`dotfiles-symm/pi-image/build-image.sh`), not current stock Pi OS -- reading it the other way is what produced [#214](https://github.com/symmatree/coordinator/pull/214) |
| Out-of-memory during bootstrap | expected pressure point on 512 MB; confirm zram/swap is on (Pi OS default) |
| `coord` picks the wrong stack | only the campod stack belongs under `/opt/stacks/` on a campod |
| Dead on **first** boot: no network, dark ACT LED, `firstrun.sh` still on the card | seen once, unexplained; power-cycle cleared it. Invisible without the serial console (GPIO 14/15) |
| Captures not landing on the `@data` subvolume | `findmnt /var/lib/campod` -- if it is on `@var`, the image's `DATA_MOUNT` and `coord_state_root` have diverged |

---

## Open: how updates reach a flying set of nodes

Everything above assumes the node has its own route to GitHub and GHCR, which is true on
the bench over lab WiFi and false in the field. The options, with the numbers that matter:

- **campod-camera is ~241 MB compressed.** Five nodes pulling independently is ~1.2 GB of WAN
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
bridge), [#24](https://github.com/symmatree/coordinator/issues/24) (campod gadget net).

## Gadget network, campod side

**What comes from where.** The image supplies exactly one thing: the
`dtoverlay=dwc2,dr_mode=peripheral` line, because it is device tree and nothing in userspace
can substitute for it. Everything else is `roles/campod`, applied by `one_time.sh campod` -- so a
gadget-net change is a `git pull` and a bootstrap re-run, not a reflash.

The bootstrap loads `g_ether` itself rather than leaving it for the next boot, so the link
comes up in the same run. Both ends need their own bootstrap: `one_time.sh campod` on each
campod, `one_time.sh` on the coordinator for the bridge. The coordinator side needs no
reboot -- the handler reloads NetworkManager.

Everything in userspace is `roles/campod`:

| | |
|---|---|
| `/etc/modules-load.d/campod-gadget.conf` | loads `dwc2` + `g_ether` at boot |
| `/etc/modprobe.d/campod-g_ether.conf` | pins both MACs, derived from the hostname (below) |
| `/etc/NetworkManager/system-connections/campod-gadget.nmconnection` | static address on `usb0`, no DHCP (#211) |

Addresses live in `host/ansible/vars/gadget-net.yml` as a map, not a derivation -- a MAC
collision is improbable and harmless, an IP collision is neither. That file is the contract
**both** roles read, on purpose: the subnet and the coordinator's address have to agree
across two devices, and two definitions is how they end up disagreeing. A node whose
hostname is not in the map gets no address and says so.

Neither side sets a gateway, and both set `never-default`. This is a link-local segment
between two boxes, not a route to anywhere. Naming the coordinator as a gateway would put a
default route on `usb0`, and NM's per-type metrics rank ethernet (100) ahead of WiFi (600)
-- so a campod would try to reach the internet through a coordinator that neither forwards nor
NATs, and lose the WiFi path it currently uses for updates. Adding forwarding + NAT later is
a deliberate change on the coordinator, not something to fall into.

## Gadget network, coordinator side

All campods land on one bridge (`br0`, `10.55.0.1/24`), so the coordinator holds one address
rather than one per campod and the campods share an L2 segment. `stp=false` and `forward-delay=0`:
it is a star of point-to-point USB links with no possible loop, and 30 s of
listening/learning on every campod reboot would cost something for nothing.

Membership is dynamic -- campods appear when they boot -- and is handled by one NM profile with
`multi-connect=3`, which lets a single profile be active on every matching device at once.
No per-campod profile, no udev glue.

**It matches on driver, not interface name.** systemd will generate an `enx<mac>` identifier
for our pinned MACs -- `names_mac()` only skips non-permanent addresses and has no
locally-administered guard -- but `99-default.link` ships
`NamePolicy=keep kernel database onboard slot path`, with `mac` only in
`AlternativeNamesPolicy`. So `enx<mac>` is an *alternative* name and the primary is
path-based (`enp1s0u1u2`), which identifies the hub port rather than the campod. Matching a
name glob would be matching cabling; the driver is the invariant.

No DHCP and no dnsmasq (#211). #12's title says DHCP and predates that decision.

**A laptop at the other end is a debugging tool, not a validation path.** With a static
address on `usb0` you can reach a campod over its inner micro-USB from a laptop, which is
worth having when something is broken and you want to dummy out one end. It does not
substitute for the real pairing: a success there does not predict a Pi 4B host, and a
failure there indicts the laptop as readily as the campod. Different host controller,
different scheduler -- and against Windows, `g_ether` presents RNDIS rather than the
CDC-ECM a Linux host binds through `cdc_ether`, so it is not even the same protocol. If you
do reach for one, a **Linux** laptop at least shares the driver with the coordinator.

Throughput and stability over the gadget link are unmeasured, and the only measurement that
means anything is Pi 4B host to Zero 2 W gadget through the real hub.

Set `campod_gadget_enabled: false` to leave a node exactly as it was.

## Open: seams with the coordinator

Each of these has to be agreed on both sides, and each is owned by a pair of issues.

| Contract | Campod side | Coordinator side | Must agree on |
|----------|-------------|------------------|---------------|
| Gadget-net reachability | [#24](https://github.com/symmatree/coordinator/issues/24) | [#12](https://github.com/symmatree/coordinator/issues/12) | subnet, static vs DHCP, per-node address (see the `g_ether` MAC note below) |
| Time | [#24](https://github.com/symmatree/coordinator/issues/24) | [#11](https://github.com/symmatree/coordinator/issues/11) | NTP server address, shared epoch. No PPS is wired anywhere on this vehicle, so this is not on the path for #211 |
| Control + status | [#25](https://github.com/symmatree/coordinator/issues/25) | [#10](https://github.com/symmatree/coordinator/issues/10) | start/stop and status wire format and transport |

### Verified in passing: `g_ether` really does randomise its MAC every boot

Worth recording before the gadget net gets built, because it looks exactly like a flaky
link. From the kernel source (`drivers/usb/gadget/function/u_ether.{c,h}`, rpi-6.12.y):
`USB_ETHERNET_MODULE_PARAMETERS()` declares `dev_addr` and `host_addr` as `charp` params
defaulting to NULL, and `get_ether_addr()` falls straight through to `eth_random_addr()`
when its string argument is NULL. **Both** ends randomise, not just one.

Harmless with a single campod; with four on a bridge it means DHCP reservations never stick
and NetworkManager creates a fresh connection profile per boot.

**Implemented in `roles/campod`:** `options g_ether dev_addr=... host_addr=...` in
`/etc/modprobe.d/campod-g_ether.conf`, both computed as `02:` + the first five bytes of
`sha256("<salt>" + hostname)` -- different salts for the two ends so they cannot collide.
That is deterministic, stable across reboots, unique per unit, and computable by ansible
from the hostname it already has, so the per-unit provisioning surface stays **one** value
instead of three and there is no registry to drift out of sync with reality. Deriving from
the hostname rather than the board serial is deliberate: identity should follow the logical
node, so a card swapped into a different Zero keeps its address (which is exactly the #211
rollback plan).

`02:` is what makes it valid: locally-administered bit set, multicast bit clear. That
matters more than it sounds, because `get_ether_addr()` silently falls back to a random
address for anything `is_valid_ether_addr()` refuses -- a bad value looks identical to not
having set one. Checked over the node names: 8 addresses, all valid, no
collisions; birthday odds across five bytes at this fleet size are ~1e-10.

Note the packaged `rpi-usb-gadget` does **not** do this for you: it pins the USB
VID/PID/serial strings in `/usr/lib/modprobe.d/g_ether.conf` but leaves both MACs
unspecified.
