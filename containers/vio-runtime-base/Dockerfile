# coordinator-vio-runtime-base: shared runtime foundation for the VIO runtime images.
#
# Carries the OpenCV runtime + the libs that BOTH coordinator-vio-tracker and
# coordinator-vio-estimator install at runtime -- the ~140 MB layer that otherwise rebuilds
# non-reproducibly in each image and re-pulls on the Pi whenever the CI build cache misses
# (see #145). Both runtime stages FROM this image by digest, so the OpenCV layer is built once,
# is stable across app changes, and -- since the Pi runs both containers -- is pulled once.
#
# Multi-arch: arm64 (the Pi 4B flight payload) + amd64 (the estimator's offline replay on the
# x86 notebook host). Rebuilt only when this directory changes: a base-digest bump
# (containers/pin-base-digests.sh) or the shared dep list below. Consumed by digest downstream.
FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

ARG DEBIAN_FRONTEND=noninteractive

# The intersection of vio-tracker's and vio-estimator's runtime deps. Image-specific extras stay
# in each app Dockerfile: tracker adds libglib2.0-0 / libopencv-imgcodecs406 / libusb-1.0-0 /
# udev; estimator adds libceres3 / python3.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    dumb-init \
    libgomp1 \
    libopencv-calib3d406 \
    libopencv-core406 \
    libopencv-imgproc406 \
    libstdc++6 \
  && rm -rf /var/lib/apt/lists/*
