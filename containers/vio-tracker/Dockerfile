# coordinator-vio-tracker: chobitsfan feature_tracker + depthai-core (arm64 / Pi 4B).
#
# Local build on the Pi:
#   docker build -t coordinator-vio-tracker:local containers/vio-tracker
#
# Cross-build (slow; uses QEMU):
#   docker buildx build --platform linux/arm64 -t coordinator-vio-tracker:local containers/vio-tracker

FROM debian:bookworm-slim AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG DEPTHAI_CORE_TAG=v2.25.0
ARG DEPTHAI_CORE_SHA
ARG OAK_D_VINS_CPP_BRANCH=apm_wiki
ARG OAK_D_VINS_CPP_SHA

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

RUN git clone --depth 1 --branch "${DEPTHAI_CORE_TAG}" https://github.com/luxonis/depthai-core.git \
  && cd depthai-core \
  && if [ -n "${DEPTHAI_CORE_SHA}" ]; then git fetch --depth 1 origin "${DEPTHAI_CORE_SHA}" && git checkout "${DEPTHAI_CORE_SHA}"; fi \
  && git submodule update --init --recursive \
  && cmake -S. -Bbuild -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/depthai \
  && cmake --build build --parallel "$(nproc)" --target install

RUN git clone --depth 1 --branch "${OAK_D_VINS_CPP_BRANCH}" https://github.com/chobitsfan/oak_d_vins_cpp.git \
  && cd oak_d_vins_cpp \
  && if [ -n "${OAK_D_VINS_CPP_SHA}" ]; then git fetch --depth 1 origin "${OAK_D_VINS_CPP_SHA}" && git checkout "${OAK_D_VINS_CPP_SHA}"; fi \
  && cmake -S. -Bbuild -DCMAKE_BUILD_TYPE=Release -Ddepthai_DIR=/opt/depthai/lib/cmake/depthai \
  && cmake --build build --parallel "$(nproc)" \
  && install -D build/feature_tracker /opt/coordinator/bin/feature_tracker

FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    dumb-init \
    libglib2.0-0 \
    libgomp1 \
    libopencv-calib3d406 \
    libopencv-core406 \
    libopencv-imgproc406 \
    libstdc++6 \
    libusb-1.0-0 \
    udev \
  && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/depthai /opt/depthai
COPY --from=builder /opt/coordinator/bin/feature_tracker /opt/coordinator/bin/feature_tracker
COPY entrypoint.sh /opt/coordinator/entrypoint.sh
RUN chmod +x /opt/coordinator/entrypoint.sh /opt/coordinator/bin/feature_tracker

ENV LD_LIBRARY_PATH=/opt/depthai/lib
WORKDIR /opt/coordinator

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/coordinator/entrypoint.sh"]
