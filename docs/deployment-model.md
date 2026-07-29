# Deployment & config model (coordinator + pods)

How on-disk state gets **deployed, updated, and maintained** on the coordinator (and the
pods) -- and, deliberately, how that is **not** a develop-on-the-box workflow. This is the
management-layer companion to [power-loss-filesystem.md](power-loss-filesystem.md) (the
substrate) and [architecture.md](architecture.md) (what runs).

Captured from a 2026-07-29 design pass. Parts are **decided but not yet built**; the
"decided vs built" table at the end says which is which, and nothing here should be read as
describing current runtime until it lands. Tracking: [#48](https://github.com/symmatree/coordinator/issues/48),
[#90](https://github.com/symmatree/coordinator/issues/90), [#96](https://github.com/symmatree/coordinator/issues/96).

## The appliance model, in three tiers

The device runs from an **image**, keeps **data**, and **converges** on boot. Each has one
owner, so there is never a question of where the source of truth is.

| Tier | Contents | How it changes | Source of truth |
|------|----------|----------------|-----------------|
| **Immutable image** ([#96](https://github.com/symmatree/coordinator/issues/96)) | OS + btrfs layout + Pi kernel/firmware + **baked container images** ([#90](https://github.com/symmatree/coordinator/issues/90)) + the checkout + the ansible recipe | **rebuild + reflash** (versioned, per-role, CI) | git / CI |
| **Persisted data** (`@home`, `@data`) | captures, journald, operator scratch | written at runtime; survives reflash | the box |
| **Convergence** (ansible) | app/config reconcile on boot; `remount,rw /usr` wrapper for maintenance | re-runnable; `git pull` == deploy | git |

The test, from a prior session, is: **reflash a role image and the box is fully defined by
git + the image** -- clean box == git, the snowflake gone. The only thing on top is
runtime-written data, never hand-tuned config.

## Config is git-authoritative -- no on-box override

There is **no per-box config override and no hand-editing on the device.** `compose.yaml`
and `.env` live in git, ship in the image, and are the only source. A value you want
different is changed in git and redeployed -- not `nano`-ed on the box (that produces a
snowflake the next deploy reverts, which is exactly the [#48](https://github.com/symmatree/coordinator/issues/48)
drift trap).

This is a deliberate reversal of the `.env` "edit on each Pi" habit the stack grew up with.
The genuine need behind "let me change something easily" is **not** a config file -- see the
two channels below.

## "Easy to change" is two channels, neither of which is a config file

1. **Runtime command + status (own track).** A few bits of intent and readiness --
   capture-before-arm vs armed-only, "capturing now?", an exposure-sweep *mode* you fly --
   belong on a **real runtime channel**, MAVLink-payload-shaped, owned by the
   coordinator-mavlink router (architecture.md UC1/UC2: "commanded by intent, reports its
   own readiness"). This is how you change behaviour without a laptop; it is not `.env`.
   Tuning params (e.g. still max-exposure) are either exposed here as a mode or are a git
   value you redeploy -- not a live on-box edit.

2. **Cheap, reliable reflection of a merged git change (this doc).** *How* changes are made
   is git; the ask is only that **reflecting** a merged change onto the box be cheap and
   reliable. It decomposes:
   - **Text** (`compose.yaml`, `.env`, `coord`) is a few KB. The copy -> **symlink** fix
     ([#48](https://github.com/symmatree/coordinator/issues/48)) makes `git pull` *be* the
     deploy with zero drift; whether the box carries a git clone or a rendered bundle is
     aesthetic at that size. (Pure no-clone form, if ever wanted: publish the config as a
     small pinned OCI artifact the box pulls and atomic-swaps -- everything the box holds is
     then a digest-pinned pull, no working tree. More machinery than a single vehicle needs.)
   - **Images** are the real cost, and are gated by the **layer-cache fix** below. Once
     fixed, "the image is the deploy": an app update is a few MB, and baking images into the
     reflash image ([#90](https://github.com/symmatree/coordinator/issues/90)) is cheap and
     incremental.

## Deploy mechanics: the changes queued (not yet built)

- **Copy -> symlink** ([#48](https://github.com/symmatree/coordinator/issues/48)). Today
  `host/ansible/roles/coord-stack` does `ansible.builtin.copy` of the whole `stacks/<name>/`
  dir into `/opt/stacks/<name>/` -- two copies of the same bytes, a sync ceremony between
  them, and hand-edits silently reverted. Replace with a symlink
  `/opt/stacks/<name> -> <checkout>/stacks/<name>`, so `git pull` is the deploy and deployed
  `.env` == repo `.env` by construction. `coord`'s `/opt/stacks/*/compose.yaml` glob still
  resolves through it.
- **Split `dist-upgrade` out of `one_time.sh`.** Today a config deploy drags a full
  `apt-get dist-upgrade` (network + possible reboot) in front of the playbook. In the
  appliance model the **OS version is a property of the image**, upgraded by a deliberate
  rebuild/reflash -- not a side effect of pushing a compose tweak. A field deploy should be
  config-only.

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
   `debian:bookworm-slim` *is* rolling, which is the bug. What pins the cache is either the
   digest or an immutable dated tag. The idiom that keeps human visibility is
   `FROM debian:bookworm-slim@sha256:...` (readable tag **and** immutable digest) in every
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
SH1106 status OLED, the Top pHAT readiness indicator
([#87](https://github.com/symmatree/coordinator/issues/87)), and persisted journald.

**Why it was ever there:** [OpenMower](https://github.com/symmatree/fables/blob/main/fables/OpenMower/openmower-os-stack.md)
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
| btrfs subvolume substrate ([#41](https://github.com/symmatree/coordinator/issues/41)/[#96](https://github.com/symmatree/coordinator/issues/96)) | decided; **not built** (only the pipboy NVMe layout exists in `dotfiles-symm/pi-storage`) |
| Copy -> symlink deploy ([#48](https://github.com/symmatree/coordinator/issues/48)) | decided; **not built** |
| Split `dist-upgrade` out of `one_time.sh` | decided; **not built** |
| Pin base (immutable ref, `pin-base-digests.sh`) + shared `vio-base` (layer-cache fix) | base pins + `vio-tracker-base` **built** (#146); tracker rewire pending |
| Baked images / boot-without-network ([#90](https://github.com/symmatree/coordinator/issues/90)) | decided; **not built** (auto-start [#97](https://github.com/symmatree/coordinator/issues/97) is done) |

## Related

- [power-loss-filesystem.md](power-loss-filesystem.md) -- the storage substrate this rides on
- [architecture.md](architecture.md) -- host-vs-container split, runtime paths, UC1/UC2
- [host-setup.md](host-setup.md) / [host/README.md](../host/README.md) -- current provisioning mechanics
