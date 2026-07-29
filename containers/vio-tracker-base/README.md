# coordinator-vio-tracker-base

Pinned build foundation for [`coordinator-vio-tracker`](../vio-tracker/) (arm64 / Pi 4B). Part
of the layer-cache fix in [#145](https://github.com/symmatree/coordinator/issues/145); the
rationale is in [docs/deployment-model.md](../../docs/deployment-model.md).

## Why this exists

The tracker image used to rebuild the Debian base, the C++ toolchain, and **depthai-core**
(~90 MB) on every source change. Because those layers are non-reproducible, a rebuild produced
new digests for byte-identical content and the Pi re-pulled the whole ~142 MB image even when
only `feature_tracker.cpp` changed (measured; see #145).

This image carries that heavy, rarely-changing content once:

- `debian:bookworm-slim` **pinned by digest** (Renovate-managed),
- the build toolchain + OpenCV dev libs + depthai's build deps,
- a built **depthai-core at `/opt/depthai`** (pinned via `upstream.lock`).

`vio-tracker`'s builder stage `FROM`s this image **by digest**, so an app change rebuilds only
the small `feature_tracker` layer and the runtime's `COPY --from` of `/opt/depthai` is a stable,
already-present layer.

## Cadence

Rebuilds only when this directory changes: a Renovate base-digest bump, the apt dep list, or the
depthai pin in `upstream.lock`. Each rebuild is a **deliberate, reviewable** event that Renovate
turns into a `FROM ...@sha256:` bump in `vio-tracker` -- never an implicit move.

## Scope

arm64 only -- depthai is used only by the tracker (a Pi 4B payload). The estimator does not use
depthai; a separate estimator/runtime base can follow if its layers prove to churn after the
debian pin (see #145 fix ladder).
