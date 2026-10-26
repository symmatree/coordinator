# coordinator-oak-capture-base: pinned build foundation for the OAK capture app (arm64).
#
# The digest-pinned Debian base, a C++20 toolchain (gcc 14, CMake, Ninja), and depthai-core v3
# built into /opt/depthai. The app's builder stage FROMs this image by digest and its runtime
# stage copies /opt/depthai, the same split as vio-tracker-base (containers/README.md).
#
# arm64 only: the OAK hosts are the coordinator (Pi 4B) and possibly a Radxa board, both arm64.
# Rebuilt only when this directory changes: a base-digest bump (containers/pin-base-digests.sh),
# the apt list, or the depthai pin in upstream.lock.
#
# Local build (arm64 box, or QEMU):
#   docker build -t coordinator-oak-capture-base:local containers/oak-capture-base
FROM debian:trixie-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad

ARG DEBIAN_FRONTEND=noninteractive
ARG DEPTHAI_CORE_TAG=v3.11.0
ARG DEPTHAI_CORE_SHA

# Toolchain, plus what vcpkg needs to bootstrap itself and fetch ports (curl, git, tar, zip,
# unzip, pkg-config). python3 runs vcpkg's meson tool, which depthai's manifest pins.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    git \
    ninja-build \
    pkg-config \
    python3 \
    tar \
    unzip \
    zip \
  && rm -rf /var/lib/apt/lists/*

# vcpkg will not bootstrap on arm64 without this; it then uses the cmake and ninja above.
ENV VCPKG_FORCE_SYSTEM_BINARIES=1

WORKDIR /src

# depthai-core -> /opt/depthai, its dependencies built by its own vcpkg manifest.
#
# Off, because the capture app does not use them: OpenCV (JPEG is encoded on the camera),
# xtensor, AprilTag, MP4 recording, protobuf (and with it the remote-connection viewer), curl,
# the dynamic-calibration library (a prebuilt download), and the RVC4 firmware -- RVC4 is a
# different device family; the original OAK-D and the OAK-D S2 are both RVC2.
#
# On: libusb (vcpkg's port, built without udev) and the RVC2 device and bootloader firmware,
# compiled into the library, since a USB OAK boots from firmware the host uploads.
# Built shared, so the app's runtime stage takes /opt/depthai/lib as one layer.
RUN git clone --depth 1 --branch "${DEPTHAI_CORE_TAG}" https://github.com/luxonis/depthai-core.git \
  && cd depthai-core \
  && if [ -n "${DEPTHAI_CORE_SHA}" ]; then test "$(git rev-parse HEAD)" = "${DEPTHAI_CORE_SHA}"; fi \
  && git submodule update --init --recursive --depth 1 \
  && cmake -S. -Bbuild -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/opt/depthai \
    -DBUILD_SHARED_LIBS=ON \
    -DDEPTHAI_OPENCV_SUPPORT=OFF \
    -DDEPTHAI_XTENSOR_SUPPORT=OFF \
    -DDEPTHAI_ENABLE_APRIL_TAG=OFF \
    -DDEPTHAI_ENABLE_MP4V2=OFF \
    -DDEPTHAI_ENABLE_PROTOBUF=OFF \
    -DDEPTHAI_ENABLE_CURL=OFF \
    -DDEPTHAI_DYNAMIC_CALIBRATION_SUPPORT=OFF \
    -DDEPTHAI_ENABLE_DEVICE_RVC4_FW=OFF \
    "-DVCPKG_INSTALL_OPTIONS=--clean-buildtrees-after-build;--clean-packages-after-build" \
  && cmake --build build --parallel "$(nproc)" \
  && cmake --install build \
  && cd /src && rm -rf /src/depthai-core /root/.cache/vcpkg

# The app builds against depthai's cmake package with
#   -Ddepthai_DIR=/opt/depthai/lib/cmake/depthai
