# coordinator-vio-runtime-base

Shared **runtime** foundation for the VIO images ([`vio-tracker`](../vio-tracker/),
[`vio-estimator`](../vio-estimator/)). Part of the layer-cache strategy in
[containers/README.md](../README.md) and [#145](https://github.com/symmatree/coordinator/issues/145).

## Why this exists

The OpenCV runtime libraries are the **biggest layer in the shipped VIO images (~140 MB)**. When
each image installs them via `apt-get install`, that layer is non-reproducible, so a CI build-cache
miss gives it a new digest and the Pi re-pulls all ~140 MB even though only app code changed
(measured in #145). Building the OpenCV runtime **once** here, pinned by digest, fixes that:

- `debian:bookworm-slim` **pinned by digest** (refreshed by `containers/pin-base-digests.sh`),
- the OpenCV runtime + the libs common to both VIO runtime stages.

Both app runtime stages `FROM` this by digest, so the OpenCV layer is built once, stays stable
across app changes, and -- because the Pi runs the tracker and the estimator together -- is
**pulled once for both**.

## Contents

The intersection of the tracker's and estimator's runtime deps: `ca-certificates`, `dumb-init`,
`libgomp1`, `libopencv-calib3d406`, `libopencv-core406`, `libopencv-imgproc406`, `libstdc++6`.
Image-specific extras stay in each app Dockerfile (tracker: `libglib2.0-0`,
`libopencv-imgcodecs406`, `libusb-1.0-0`, `udev`; estimator: `libceres3`, `python3`).

## Scope

**Multi-arch** (`linux/arm64` for the Pi 4B, `linux/amd64` for the estimator's offline replay on
the notebook host). Rebuilt only when this directory changes. Consumed by digest downstream, so a
rebuild is a deliberate `FROM ...@sha256:` bump in the app images.
