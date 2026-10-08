# Deployment & config model (coordinator + campods)

How on-disk state gets **deployed, updated, and maintained** on the coordinator (and the
campods) -- and, deliberately, how that is **not** a develop-on-the-box workflow. This is the
management-layer companion to [power-loss-filesystem.md](power-loss-filesystem.md) (the
substrate) and [architecture.md](architecture.md) (what runs).

**How the image itself is built is not here.** That lives with the code, in
[`dotfiles-symm/pi-image/PIPELINE.md`](https://github.com/symmatree/dotfiles-symm/blob/main/pi-image/PIPELINE.md)
-- the path from a git push to a card, which decisions belong to the image versus to
provisioning versus to ansible, and the traps in that build. This doc starts where that one
ends: the card is in the Pi, and now config has to get deployed and stay deployed.

Captured from a 2026-07-29 design pass. Parts are **decided but not yet built**; the
"decided vs built" table at the end says which is which, and nothing here should be read as
describing current runtime until it lands. Tracking: [#48](https://github.com/symmatree/coordinator/issues/48),
[#90](https://github.com/symmatree/coordinator/issues/90), [#96](https://github.com/symmatree/coordinator/issues/96).

## The appliance model, in three tiers

The device runs from an **image**, keeps **data**, and **converges** on boot. Each has one
owner, so there is never a question of where the source of truth is.

| Tier | Contents | How it changes | Source of truth |
|------|----------|----------------|-----------------|
| **Immutable image** ([#96](https://github.com/symmatree/coordinator/issues/96)) | OS + btrfs layout + Pi kernel/firmware, device tree, the initramfs, and the fleet-wide system facts (no swap, no scheduled maintenance, passwordless sudo). **Not** the container images ([#90](https://github.com/symmatree/coordinator/issues/90), not built) and **not** the checkout, which ansible creates. [What goes in and why](https://github.com/symmatree/dotfiles-symm/blob/main/pi-image/PIPELINE.md) | **rebuild + reflash** (versioned, per-role, CI) | git / CI |
| **Persisted data** (`@home`, `@data`) | captures, journald, operator scratch | written at runtime; survives reflash | the box |
| **Convergence** (ansible) | app/config reconcile on boot; `remount,rw /usr` wrapper for maintenance | re-runnable; `git pull` == deploy | git |

The test, from a prior session, is: **reflash a role image and the box is fully defined by
git + the image** -- clean box == git, the snowflake gone. The only thing on top is
runtime-written data, never hand-tuned config.

## Config is git-authoritative -- no on-box override

> **The stack definition is podman quadlet units**: `stacks/<name>/*.container` plus a
> `<name>-stack.target`, symlinked into `/etc/containers/systemd/`, where podman's
> generator turns each into a `.service` at boot. Read `compose.yaml` below as "the
> stack definition"; git-authority and the symlink deploy are unchanged. The runtime
> choice and the measurements behind it are in
> [#449](https://github.com/symmatree/coordinator/issues/449).

There is **no per-box config override and no hand-editing on the device.** The stack
definition lives in git, ships in the image, and is the only source -- values included, since both
stacks now carry them inline rather than in a `.env` beside them ([#233](https://github.com/symmatree/coordinator/pull/233)
folded campod's in; the coordinator's followed). A value you want different is changed in git and redeployed -- not `nano`-ed on the box (that produces a
snowflake the next deploy reverts, which is exactly the [#48](https://github.com/symmatree/coordinator/issues/48)
drift trap).

This is a deliberate reversal of the `.env` "edit on each Pi" habit the stack grew up with.
The `.env` files are now gone outright: a file whose whole purpose is to be copied and
locally edited is the wrong shape for a fleet nobody develops on interactively.
The genuine need behind "let me change something easily" is **not** a config file -- see the
two channels below.

## "Easy to change" is two channels, neither of which is a config file

1. **Runtime command + status (own track).** A few bits of intent and readiness --
   capture-before-arm vs armed-only, "capturing now?", an exposure-sweep *mode* you fly --
   belong on a **real runtime channel**, MAVLink-payload-shaped, owned by the
   coordinator-mavlink router (architecture.md UC1/UC2: "commanded by intent, reports its
   own readiness"). This is how you change behaviour without a laptop; it is not a config file.
   Tuning params (e.g. still max-exposure) are either exposed here as a mode or are a git
   value you redeploy -- not a live on-box edit.

2. **Cheap, reliable reflection of a merged git change (this doc).** *How* changes are made
   is git; the ask is only that **reflecting** a merged change onto the box be cheap and
   reliable. It decomposes:
   - **Text** (`compose.yaml`, `coord`) is a few KB. The copy -> **symlink** fix
     ([#48](https://github.com/symmatree/coordinator/issues/48)) makes `git pull` *be* the
     deploy with zero drift; whether the box carries a git clone or a rendered bundle is
     aesthetic at that size. (Pure no-clone form, if ever wanted: publish the config as a
     small pinned OCI artifact the box pulls and atomic-swaps -- everything the box holds is
     then a digest-pinned pull, no working tree. More machinery than a single vehicle needs.)
   - **Images** are the real cost, and are gated by the **layer-cache fix** below. Once
     fixed, "the image is the deploy": an app update is a few MB, and baking images into the
     reflash image ([#90](https://github.com/symmatree/coordinator/issues/90)) is cheap and
     incremental.

## Deploy mechanics (built -- #48)

- **Copy -> symlink.** `host/ansible/roles/coord-stack` used to `ansible.builtin.copy` the whole
  `stacks/<name>/` dir into `/opt/stacks/<name>/` -- two copies of the same bytes, a sync
  ceremony between them, and hand-edits silently reverted. It now **symlinks**
  `/opt/stacks/<name> -> <checkout>/stacks/<name>`, so `git pull` is the deploy and deployed
  `compose.yaml` == repo `compose.yaml` by construction. `coord`'s `/opt/stacks/*/compose.yaml`
  glob resolves through it. (A stale copied dir from a pre-symlink deploy is removed once, on the next run.)
- **`dist-upgrade` split out of the config deploy.** It used to drag a full
  `apt-get dist-upgrade` (network + possible reboot) in front of the playbook. `site.yaml` is
  **config-only**; the OS upgrade is off unless asked for, with **`-e dist_upgrade=true`**. In the
  appliance model the OS version is normally a property of the image (#96), upgraded by reflash;
  `-e dist_upgrade=true` is the in-place alternative. A field config deploy no longer touches the OS.

## The GPU/CPU memory split, and why it is 32 on a campod

A Raspberry Pi hands part of its RAM to the VideoCore before Linux starts, and what
is left is all the kernel ever sees. On a campod the firmware default was never a
choice: there is no `gpu_mem` line in a stock `config.txt`, and the default for a
board under 1 GB is 64 MiB. Against a 512 MiB part that is 64 MiB the kernel never
counts, on a device with no display, no `vc4-kms-v3d`, `drm` blacklisted, no cards
in `/sys/class/drm` and "no soundcards" in `/proc/asound`.

**It is `gpu_mem=32`, and the value below that is unavailable rather than merely
tight.** Measured on `campod-se` 2026-10-08, one variable at a time, from image
`campod-pi-20261008.img`:

| `gpu_mem` set | `vcgencmd get_mem gpu` | firmware | VCHI | ISP nodes | camera |
|---|---|---|---|---|---|
| 16 | `gpu=16M` | `start_cd` | fails `-22` | absent | `rpicam` refuses |
| 17 | `gpu=16M` | `start_cd` | fails `-22` | absent | `rpicam` refuses |
| 24 | `gpu=16M` | `start_cd` | fails `-22` | absent | `rpicam` refuses |
| **32** | **`gpu=32M`** | **full** | **ok** | **`video13`-`16`** | **enumerates 4608x2592** |
| 64 (default) | `gpu=64M` | full | ok | present | works |

`MemTotal` moves 424,728 -> 457,432 kB, so **31.9 MiB returns to the ARM**. For
scale, the only reclaimable memory on a capturing campod is ~162 MiB of page cache
plus ~29 MiB free; anonymous memory, unreclaimable slab and the `dma_buf`-held part
of the CMA region cannot be reclaimed under any pressure (#434).

### Two things the Raspberry Pi documentation does not say

**The firmware rounds `gpu_mem` down to a multiple of 16.** 17 and 24 both report
`gpu=16M`. The documentation presents the cut-down firmware as selected by the
exact value 16 -- "The only way to enable the cut-down firmware is to specify
`gpu_mem=16`" -- and in practice anything that rounds to 16 selects it. No
granularity is published and `start.elf` is closed, so this is measurement rather
than citation.

**The cut-down firmware takes the camera with it.** Its own description is
"removes support for hardware blocks such as codecs and 3D as well as debug
logging support" -- an open list, and the ISP is in it. Under `start_cd`:

```
vc_sm_cma_vchi_init: failed to open VCHI service (-22)
[vc_sm_connected_init]: failed to initialize shared memory service
bcm2835_mmal_vchiq: Failed to open VCHI service connection (status=-22)
```

`bcm2835_isp` then creates no device nodes -- `/dev/video13..16` are simply absent
-- and libcamera's Raspberry Pi `vc4` pipeline drives the ISP through exactly that
path. The sensor and Unicam are unaffected throughout: `imx708` probes and reads
module ID `0x0302` over I2C, and `/dev/video0`, `/dev/video1` and `/dev/media0`
exist. That combination is what makes the failure confusing -- it presents as a
camera that will not start, names nothing about memory, and leaves every part you
would check first looking healthy.

`rpicam-hello` reports `ERROR: rpicam-apps currently only supports the Raspberry Pi
platforms` on a Raspberry Pi, which is the same cause wearing a misleading message.

### The consequence for anyone changing this

The 16 MiB that only the cut-down firmware offers is not available to this fleet at
any price, so 32 is the floor rather than a compromise. The check on any change
here is #316's and it is the only one that matters: **capture works at full sensor
resolution.** A value that is too small, or a missing firmware component, fails
inside libcamera's allocation and surfaces as a camera that will not start -- not
as an out-of-memory message, and not as anything a boot log flags.

`config.txt` is an ordinary file on a mounted FAT partition, so a candidate value
can be tried on a booted device and backed out with one edit and a reboot. It does
not need a reflash to test, which is how the table above was produced.

## What a campod costs before anything is converged

The state a freshly flashed card is in exists only until the first converge, so it
is worth having written down. `campod-se`, `campod-pi-20261008.img` with
`gpu_mem=32`, booted and never converged -- no containers, no collectd, no
`coord`, no gadget network, WiFi up from the cloud-init seed:

| | |
|---|---|
| `MemTotal` | 457,432 kB (446.7 MiB) |
| `MemFree` | 217,028 kB |
| `MemAvailable` | **312,176 kB (305.1 MiB)** |
| `Cached` | 134,556 kB |
| `AnonPages` | **24,736 kB (24.2 MiB)** |
| `Mapped` | 37,612 kB |
| `Slab` | 38,104 kB, of which `SUnreclaim` **24,948 kB** |
| `KernelStack` / `PageTables` | 2,328 / 1,696 kB |
| `CmaFree` of `CmaTotal` | 126,644 of 131,072 kB |
| userspace RSS, summed | 143.6 MiB across 138 processes |
| loaded modules | 41, 5.02 MiB |

**The bare OS holds about 49 MiB that cannot be reclaimed** -- 24.2 MiB of
anonymous memory plus 24.4 MiB of unreclaimable slab -- and both figures are
unchanged at `gpu_mem=16`, so they are a property of the OS rather than of the
split. `CmaFree` sitting at 126.6 of 131.1 MiB is simply the camera not having
started; the region fills with movable pages under load and that number stops
meaning anything (#434).

### What it says about where the footprint is

Set against the same pod converged and capturing (#434): 97.1 MiB of anon and
30.4 MiB of unreclaimable slab. So **the OS accounts for roughly a quarter of the
unreclaimable anonymous memory and the payload stack for the other three
quarters.** For comparison, `dockerd` and `containerd` together held 41.3 MiB of
anon -- more than the entire bare OS -- which is the measurement behind
[#449](https://github.com/symmatree/coordinator/issues/449).

That ordering is the useful part: shrinking the base system is a real but bounded
win, because the base system is not where the memory goes.

## Boot without a network

A normal power-up must need **no network and no `coord pull`**. That falls out of the model:
**baked images** ([#90](https://github.com/symmatree/coordinator/issues/90)) + **auto-start
oneshot** ([#97](https://github.com/symmatree/coordinator/issues/97), done; `coord stop` uses
`stop` not `down` so a power bounce re-ups) + **config already on disk**. The only
network-touching path is deliberate bench iteration (`coord pull`), never the boot path.

## The image layer-cache bug (measured)

The images re-download in full far more often than their content changes. Measured from GHCR,
diffing layer digests across consecutive `coordinator-vio-tracker` `main` builds:

| Build transition | Git change | Pi re-downloads |
|------------------|-----------|-----------------|
| `5151a93 -> ccfef3e` | `entrypoint.sh` (1 line) | **0.0 MB** of 142 (6/7 layers reused) |
| `ccfef3e -> 05d9bb3` | one Dockerfile line + a CI tweak; **depthai pin identical** | **142 MB of 142** (every content layer new) |

In the 142 MB case the layers are **byte-identical in size** (28.12 / 90.36 / 13.62 / 9.86 MB)
but **every digest changed**, including layer 0 -- the ~28 MB `debian:bookworm-slim` base,
which is unrelated to the code that changed.

**Root cause (measured + strong inference):** layer 0 changing digest with unchanged content
means the **floating `debian:bookworm-slim` base tag moved** to a new point release between
the two builds. It is the bottom layer, so when it moves, everything above it re-pulls.
Compounding it: the layers above the base are **non-reproducible** (`apt-get install` with no
version/snapshot pin, from-scratch C++ builds), so any invalidation rebuilds them to
*different bytes* rather than reproducing the same digest. When the base tag holds and CI's
build cache hits, the Pi pulls ~0; when either slips, it pulls all 142 MB. That intermittency
is why it read as undiagnosed. The same unpinned `FROM debian:bookworm-slim` is in
vio-estimator, oak-still-capture, etc. -- so it hits the whole fleet.

**Fix ladder** (each step also serves [#90](https://github.com/symmatree/coordinator/issues/90)
baking / [#96](https://github.com/symmatree/coordinator/issues/96) image build):

1. **Pin the base to an immutable reference.** A bare rolling tag does **not** fix this --
   `debian:trixie-slim` *is* rolling, which is the bug. What pins the cache is either the
   digest or an immutable dated tag. The idiom that keeps human visibility is
   `FROM debian:trixie-slim@sha256:...` (readable tag **and** immutable digest) in every
   stage of every Dockerfile, so a base bump is a **deliberate, reviewable** commit, not an
   implicit move. The pins are kept fresh in bulk by **`containers/pin-base-digests.sh`** (run
   by hand, or on a schedule via `.github/workflows/update-base-digests.yaml`, which opens a
   PR when a base moved) -- a self-hosted "shared pin" with no third-party app. This also
   resolves the freshness tension: instead of a daily cache-bust to force `apt-get` to re-run,
   apt re-runs when the **base pin bumps** -- fresh *and* reproducible, on a cadence you control.
2. **Factor the heavy stable content into pinned base images**, built on their own cadence and
   consumed by digest, so an app change rebuilds only its small layer. Realized as **two** bases:
   `coordinator-vio-tracker-base` (Debian + toolchain + built depthai; tracker builder) and
   `coordinator-vio-runtime-base` (the ~140 MB OpenCV runtime; both VIO runtime stages, so the Pi
   pulls OpenCV once for both). Highest leverage. Full per-image detail:
   [containers/README.md](../containers/README.md).
3. **Reproducible-build hardening** (`SOURCE_DATE_EPOCH`, an apt snapshot mirror, pinned
   package versions) -- only if 1 + 2 leave residual churn.

Moving a blob out to a mount would also stop it re-downloading, but the pinned base image gets
the same "pull once" benefit without coupling the container's runtime to host filesystem layout
-- so prefer the base image over a mount.

## Dockge: dropped

[#13](https://github.com/symmatree/coordinator/issues/13) proposed installing Dockge (a
compose-stack web UI) on the coordinator. **Dropped.** It maps to neither real "easy to
change" channel: it is not a runtime command/status path, and as a web **authoring** surface
it re-introduces the [#48](https://github.com/symmatree/coordinator/issues/48) drift (a third
writer alongside git and the box). Visibility is already covered by `coord status`, the
SH1106 status OLED, the front-panel readiness indicator
([#87](https://github.com/symmatree/coordinator/issues/87)), and persisted journald.

**Why it was ever there:** [OpenMower](https://github.com/symmatree/fables/blob/main/OpenMower/openmower-os-stack.md)
has built a Docker + Dockge + `/opt/stacks/` edge stack, and interop with it (as a rover, at
least to start) motivated adopting the same shape. That interop is a **separate future
decision**; it is not a reason to run a web authoring surface on the flight appliance now.
The `/opt/stacks/` path convention stays (it costs nothing and keeps the OpenMower shape);
Dockge itself does not.

## Decided vs built

| Item | Status |
|------|--------|
| Config is git-authoritative, no on-box override | **decided** |
| Runtime change is a MAVLink channel, not a config file | **decided** (build is its own track) |
| Dockge dropped | **decided** |
| btrfs subvolume substrate ([#41](https://github.com/symmatree/coordinator/issues/41)/[#96](https://github.com/symmatree/coordinator/issues/96)) | **built**; boots on all three roles from `dotfiles-symm/pi-image`. Base is Trixie ([#238](https://github.com/symmatree/coordinator/issues/238)) |
| Copy -> symlink deploy ([#48](https://github.com/symmatree/coordinator/issues/48)) | **built** |
| podman + quadlet; the payload runs as systemd units ([#449](https://github.com/symmatree/coordinator/issues/449)) | **built, unverified on hardware.** `systemctl stop` is the stop path; container logs go to the journal |
| Split `dist-upgrade` out of the config deploy (`-e dist_upgrade=true`, default false) | **built** |
| Convergence driven from another machine over SSH; `one_time.sh` and its `/usr` shell helper deleted | **built** |
| Pin bases + `vio-tracker-base` (depthai) + `vio-runtime-base` (OpenCV) -- layer-cache fix ([#145](https://github.com/symmatree/coordinator/issues/145)) | **built** (#146 pins, #147 pin script, #148 tracker build-base, #151 runtime base + both VIO images rewired onto it) |
| Baked images / boot-without-network ([#90](https://github.com/symmatree/coordinator/issues/90)) | decided; **not built** (auto-start [#97](https://github.com/symmatree/coordinator/issues/97) is done) |

## Related

- [power-loss-filesystem.md](power-loss-filesystem.md) -- the storage substrate this rides on
- [architecture.md](architecture.md) -- host-vs-container split, runtime paths, UC1/UC2
- [host-setup.md](host-setup.md) / [host/README.md](../host/README.md) -- current provisioning mechanics
- [`dotfiles-symm/pi-image/PIPELINE.md`](https://github.com/symmatree/dotfiles-symm/blob/main/pi-image/PIPELINE.md)
  -- how the image is built, and the image/provisioning/ansible layer split
