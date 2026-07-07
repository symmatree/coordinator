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

# Clone + pin (its own layer, so it stays cached when only the overlay changes).
RUN git clone --depth 1 --branch "${VINS_FUSION_BRANCH}" https://github.com/chobitsfan/VINS-Fusion.git \
  && cd VINS-Fusion \
  && if [ -n "${VINS_FUSION_SHA}" ]; then git fetch --depth 1 origin "${VINS_FUSION_SHA}" && git checkout "${VINS_FUSION_SHA}"; fi

# Overlay: whole-file shadows in overlay/VINS-Fusion/ are copied over the pinned checkout right
# before building, so modified upstream files can be iterated with no fork/patch machinery yet.
# Empty overlay = no-op (unchanged upstream build). See overlay/README.md.
COPY overlay/ /overlay/
RUN cd VINS-Fusion \
  && if [ -d /overlay/VINS-Fusion ]; then cp -a /overlay/VINS-Fusion/. . ; fi \
  && cmake -S vins_estimator -B build -DCMAKE_BUILD_TYPE=Release \
  && cmake --build build --parallel "$(nproc)" \
  && install -D build/vins_fusion /opt/coordinator/bin/vins_fusion \
  # deterministic offline harness, built only when the overlay provides it (main_offline.cpp)
  && if [ -f build/vins_fusion_offline ]; then install -D build/vins_fusion_offline /opt/coordinator/bin/vins_fusion_offline; fi

FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

# libceres3 pulls its own runtime deps (glog, suitesparse, lapack, tbb...). python3 (not
# -minimal, which omits json/hashlib) is only for offline_runner.py's provenance sidecar;
# the flight binary itself needs none of it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    dumb-init \
    libceres3 \
    libgomp1 \
    libopencv-calib3d406 \
    libopencv-core406 \
    libopencv-imgproc406 \
    libstdc++6 \
    python3 \
  && rm -rf /var/lib/apt/lists/*

# Whole bin dir so vins_fusion_offline comes along when the overlay builds it (main
# just has vins_fusion). install -D set them 0755 in the builder; COPY preserves that.
COPY --from=builder /opt/coordinator/bin/ /opt/coordinator/bin/
COPY entrypoint.sh /opt/coordinator/entrypoint.sh
COPY offline_runner.py /opt/coordinator/bin/vio-offline-runner
RUN chmod +x /opt/coordinator/entrypoint.sh /opt/coordinator/bin/vins_fusion \
    /opt/coordinator/bin/vio-offline-runner

# Provenance stamp for the offline runner: the coordinator commit (overlay + runner source)
# and the pinned upstream vins commit. Placed LAST -- COORDINATOR_SHA changes every commit,
# so keeping it after the apt + COPY layers leaves those cached across pushes. CI passes
# COORDINATOR_SHA=github.sha; IMAGE_DIGEST is injected at run time by the k8s Job.
ARG COORDINATOR_SHA=unknown
ARG VINS_FUSION_SHA=unknown
ENV COORDINATOR_SHA=${COORDINATOR_SHA} \
    VINS_FUSION_SHA=${VINS_FUSION_SHA}

WORKDIR /opt/coordinator

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/coordinator/entrypoint.sh"]
