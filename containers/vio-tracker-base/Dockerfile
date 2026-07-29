# coordinator-vio-tracker-base: pinned build foundation for coordinator-vio-tracker (arm64 / Pi 4B).
#
# Carries the heavy, rarely-changing content that used to rebuild -- and re-download on the Pi --
# on every app-source change: the digest-pinned Debian base, the C++ build toolchain + OpenCV dev
# libs, and a built depthai-core at /opt/depthai (the ~90 MB layer that dominated the re-pull, see
# issue #145 + docs/deployment-model.md). vio-tracker's builder stage FROMs this image *by digest*,
# so an app change rebuilds only the small feature_tracker layer and the runtime's `COPY --from`
# of /opt/depthai stays a stable, already-present layer.
#
# arm64 only: depthai is used only by the tracker, which is a Pi 4B (arm64) payload -- no amd64.
# Rebuilt only when this directory changes: a base-digest bump (containers/pin-base-digests.sh),
# the apt dep list, or the depthai pin in upstream.lock. Consumed by digest downstream, so a
# rebuild is a deliberate, reviewed pin bump in vio-tracker -- never an implicit move.
#
# Local build (arm64 box, or QEMU):
#   docker build -t coordinator-vio-tracker-base:local containers/vio-tracker-base
FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

ARG DEBIAN_FRONTEND=noninteractive
ARG DEPTHAI_CORE_TAG=v2.25.0
ARG DEPTHAI_CORE_SHA

# Build toolchain + depthai build deps (libusb/libudev are depthai's). Matches what
# vio-tracker's builder installed before this base existed, so the split is byte-neutral.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    git \
    libopencv-dev \
    libudev-dev \
    libusb-1.0-0-dev \
    pkg-config \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# depthai-core -> /opt/depthai. Pins passed from upstream.lock via CI build-args (tag + exact SHA).
RUN git clone --depth 1 --branch "${DEPTHAI_CORE_TAG}" https://github.com/luxonis/depthai-core.git \
  && cd depthai-core \
  && if [ -n "${DEPTHAI_CORE_SHA}" ]; then git fetch --depth 1 origin "${DEPTHAI_CORE_SHA}" && git checkout "${DEPTHAI_CORE_SHA}"; fi \
  && git submodule update --init --recursive \
  && cmake -S. -Bbuild -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/depthai \
  && cmake --build build --parallel "$(nproc)" --target install \
  && cd /src && rm -rf /src/depthai-core

# depthai's cmake package config lives here; vio-tracker builds against it with
#   -Ddepthai_DIR=/opt/depthai/lib/cmake/depthai
