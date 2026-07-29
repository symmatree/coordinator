# Container images -- build & layer-cache strategy

How the images in this directory are built so that a normal code change re-pulls **kilobytes,
not the whole image**, on the offline-first Pi. This is the container half of the appliance
model in [../docs/deployment-model.md](../docs/deployment-model.md); the measured problem and
decision are [#145](https://github.com/symmatree/coordinator/issues/145).

## The problem (measured)

The coordinator pulls images over the network only when connected, and a fresh flash should boot
the stack offline (#90). So image **pull cost matters** -- and it was far higher than the code
churn justified. Diffing the shipped `coordinator-vio-tracker` layers across consecutive builds,
one transition re-pulled **0 MB**, the next re-pulled the **entire ~190 MB** with byte-identical
layer *sizes* but all-new *digests*. Two causes:

1. **Floating base tag.** `FROM debian:bookworm-slim` is a rolling tag. When Debian ships a point
   release the base layer's digest changes, and since it is the bottom layer, *every* layer above
   it re-pulls -- on every device, regardless of whether any of our code changed.
2. **Non-reproducible heavy layers.** `apt-get install` (unpinned versions) and from-scratch C++
   builds (depthai, VINS) produce **different bytes on each rebuild**. So whenever the CI build
   cache misses, those layers get new digests for identical content and re-pull in full.

The two big offenders were **depthai-core** (~built, then copied in) and the **OpenCV runtime**
(~140 MB `apt` layer).

## The strategy (three parts)

1. **Pin every base image by digest.** `FROM debian:bookworm-slim@sha256:...` in every Dockerfile
   (and `pod-camera`'s `BASE_IMAGE` default). The readable tag stays for humans; the digest makes
   the base layer immutable, so a Debian release no longer silently re-pulls the fleet. A base
   move becomes a **deliberate, reviewed** digest bump.
2. **Factor the heavy, stable content into pinned base images**, built on their own cadence and
   consumed **by digest**. Then an app-source change rebuilds only its small layer; the heavy
   layers keep their digests and are not re-pulled. Two such bases exist (below).
3. **Keep the digests fresh with `pin-base-digests.sh`** (no third-party app). It auto-discovers
   the base refs in `*/Dockerfile`, resolves each tag's current digest, and rewrites the pins;
   `.github/workflows/update-base-digests.yaml` runs it monthly / on demand and opens a PR when a
   base moved. So freshness (security updates) and reproducibility (stable digests) coexist: `apt`
   re-runs when the **base pin bumps**, not on every build.

## The base images

| Base | Arch | Carries | Consumed by | Ships to the Pi? |
|------|------|---------|-------------|------------------|
| [`vio-tracker-base`](vio-tracker-base/) | arm64 | Debian + C++ toolchain + a built **depthai-core** at `/opt/depthai` | `vio-tracker` **builder** stage | no (build-only) |
| [`vio-runtime-base`](vio-runtime-base/) | arm64 + amd64 | Debian + the **OpenCV runtime** + libs common to both VIO runtime stages | `vio-tracker` + `vio-estimator` **runtime** stages | yes (its layers ship inside both app images, pulled once) |

Each base rebuilds only when **its own** directory changes (a base-digest bump or its dep list),
and is consumed downstream by digest -- so a base rebuild is a reviewed `FROM ...@sha256:` bump in
the app images, never an implicit move.

## How it applies per image

| Image | Arch | Builder base | Runtime base | Notes |
|-------|------|--------------|--------------|-------|
| `vio-tracker` | arm64 | `vio-tracker-base@digest` | `vio-runtime-base@digest` | depthai from the build base, OpenCV from the runtime base; only `feature_tracker` + the small tracker-only libs build here |
| `vio-estimator` | arm64 + amd64 | `debian@digest` (own Ceres/OpenCV-dev toolchain) | `vio-runtime-base@digest` | a dedicated build base is a possible future CI-time win; the runtime OpenCV is the layer that ships, and it comes from the shared base |
| `oak-still-capture` | arm64 | -- | `debian@digest` | uses **depthai-python + OpenCV pip wheels**, a different dependency path -- no shared apt/C++ base to factor; just pins Debian |
| `sh1106-display` | arm64 | -- | `debian@digest` | small; no heavy shared content |
| `coordinator-mavlink` | arm64 | -- | `debian@digest` | small; no heavy shared content |
| `pod-camera` | arm64 | -- | `debian@digest` (via `BASE_IMAGE`) | small; adds the Raspberry Pi apt suite for matched `libcamera`/`picamera2` |

The rule of thumb: **factor a base only for content that is both heavy and shared/expensive to
rebuild** (the C++ depthai build; the OpenCV runtime). Small images, or ones on a self-contained
dependency path (pip wheels), just pin Debian -- a base would be carrying cost for no pull saving.

> Status: `vio-tracker`/`vio-estimator` consuming `vio-runtime-base` lands in the follow-up to the
> PR that introduces this base (see [#145](https://github.com/symmatree/coordinator/issues/145));
> `../docs/deployment-model.md` tracks built-vs-pending.

## Adding or changing an image

- **New heavy image sharing OpenCV/depthai?** `FROM` the relevant base by digest; keep only the
  image-specific layers in its own Dockerfile.
- **New small image?** Just `FROM debian:bookworm-slim@sha256:...`; `pin-base-digests.sh` will keep
  the digest fresh once the line exists.
- **Bumping a pinned upstream** (depthai, a base): edit the relevant `upstream.lock` / run the pin
  script, let the base rebuild, then bump the consuming images' `FROM ...@sha256:` to the new
  digest (the pin script does this for base images; do it in the same PR).
- Every image builds via its own `.github/workflows/build-<name>.yaml` (path-triggered), pushing to
  `ghcr.io/symmatree/coordinator-<name>`.
