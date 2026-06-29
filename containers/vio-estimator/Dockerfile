# coordinator-vio-estimator: chobitsfan vins_fusion (VINS-Fusion apm_wiki) for arm64 / Pi 4B.
#
# Local build on the Pi:
#   docker build -t coordinator-vio-estimator:local containers/vio-estimator
#
# Cross-build (slow; uses QEMU):
#   docker buildx build --platform linux/arm64 -t coordinator-vio-estimator:local containers/vio-estimator
#
# NOTE: the apm_wiki branch builds the `vins_estimator` subproject as plain CMake
# (no ROS / no catkin -- it ships a fake_ros.h shim and a socket main.cpp). The
# `docker/Dockerfile` in that repo is the legacy ROS-kinetic catkin path and is
# NOT used here. `vins_fusion` links only OpenCV4 + Ceres + Eigen; the camera
# model lives in feature_tracker, so camera_models (the only roscpp-dependent
# package) is intentionally not built.

FROM debian:bookworm-slim AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG VINS_FUSION_BRANCH=apm_wiki
ARG VINS_FUSION_SHA

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    git \
    libceres-dev \
    libeigen3-dev \
    libgoogle-glog-dev \
    libopencv-dev \
    pkg-config \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --depth 1 --branch "${VINS_FUSION_BRANCH}" https://github.com/chobitsfan/VINS-Fusion.git \
  && cd VINS-Fusion \
  && if [ -n "${VINS_FUSION_SHA}" ]; then git fetch --depth 1 origin "${VINS_FUSION_SHA}" && git checkout "${VINS_FUSION_SHA}"; fi \
  && cmake -S vins_estimator -B build -DCMAKE_BUILD_TYPE=Release \
  && cmake --build build --parallel "$(nproc)" \
  && install -D build/vins_fusion /opt/coordinator/bin/vins_fusion

FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

# libceres2 pulls its own runtime deps (glog, suitesparse, lapack, tbb...).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    dumb-init \
    libceres2 \
    libgomp1 \
    libopencv-calib3d406 \
    libopencv-core406 \
    libopencv-imgproc406 \
    libstdc++6 \
  && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/coordinator/bin/vins_fusion /opt/coordinator/bin/vins_fusion
COPY entrypoint.sh /opt/coordinator/entrypoint.sh
RUN chmod +x /opt/coordinator/entrypoint.sh /opt/coordinator/bin/vins_fusion

WORKDIR /opt/coordinator

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/coordinator/entrypoint.sh"]
