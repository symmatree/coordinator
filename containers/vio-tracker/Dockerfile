# coordinator-vio-tracker: chobitsfan feature_tracker (arm64 / Pi 4B).
#
# The heavy, rarely-changing foundation -- Debian base, C++ toolchain, OpenCV dev libs, and a
# built depthai-core at /opt/depthai -- lives in coordinator-vio-tracker-base (pinned by digest;
# see containers/vio-tracker-base + #145). So this image only clones + builds oak_d_vins_cpp with
# our overlay: an app change re-pulls the small feature_tracker layer, not the ~90 MB depthai layer.
#
# Local build on the Pi (pulls the base from GHCR):
#   docker build -t coordinator-vio-tracker:local containers/vio-tracker
# Cross-build (slow; uses QEMU):
#   docker buildx build --platform linux/arm64 -t coordinator-vio-tracker:local containers/vio-tracker

FROM ghcr.io/symmatree/coordinator-vio-tracker-base:main@sha256:763da8d87a04f66711931da044370eb09a8f954fe94ed6a816665d0ccd001929 AS builder

ARG OAK_D_VINS_CPP_BRANCH=apm_wiki
ARG OAK_D_VINS_CPP_SHA

WORKDIR /src

RUN git clone --depth 1 --branch "${OAK_D_VINS_CPP_BRANCH}" https://github.com/chobitsfan/oak_d_vins_cpp.git \
  && cd oak_d_vins_cpp \
  && if [ -n "${OAK_D_VINS_CPP_SHA}" ]; then git fetch --depth 1 origin "${OAK_D_VINS_CPP_SHA}" && git checkout "${OAK_D_VINS_CPP_SHA}"; fi

# Our overlay of feature_tracker.cpp: periodic disparity + RGB still capture (#72), plus the
# UsbSpeed::HIGH change (was a sed). Kept byte-diffable vs the pinned upstream.
# capture_writer.hpp (#89): power-loss-safe background writer, included by feature_tracker.cpp.
COPY overlay/oak_d_vins_cpp/feature_tracker.cpp /src/oak_d_vins_cpp/feature_tracker.cpp
COPY overlay/oak_d_vins_cpp/capture_writer.hpp /src/oak_d_vins_cpp/capture_writer.hpp

# depthai is preinstalled at /opt/depthai by the base image.
RUN cd /src/oak_d_vins_cpp \
  && cmake -S. -Bbuild -DCMAKE_BUILD_TYPE=Release -Ddepthai_DIR=/opt/depthai/lib/cmake/depthai \
  && cmake --build build --parallel "$(nproc)" \
  && install -D build/feature_tracker /opt/coordinator/bin/feature_tracker

FROM ghcr.io/symmatree/coordinator-vio-runtime-base:main@sha256:9c6114f4724cf348e9536dd4c2d48f1ff560bf7a1f3fd817f1f9a556687704fa

ARG DEBIAN_FRONTEND=noninteractive

# OpenCV runtime + ca-certificates/dumb-init/libgomp1/libstdc++6 come from the runtime base
# (shared with the estimator, pulled once). Only the tracker-specific extras are added here.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libopencv-imgcodecs406 \
    libusb-1.0-0 \
    udev \
  && rm -rf /var/lib/apt/lists/*

# /opt/depthai comes from the base (the builder's FROM); the runtime links against it.
COPY --from=builder /opt/depthai /opt/depthai
COPY --from=builder /opt/coordinator/bin/feature_tracker /opt/coordinator/bin/feature_tracker
COPY entrypoint.sh /opt/coordinator/entrypoint.sh
RUN chmod +x /opt/coordinator/entrypoint.sh /opt/coordinator/bin/feature_tracker

ENV LD_LIBRARY_PATH=/opt/depthai/lib
WORKDIR /opt/coordinator

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/coordinator/entrypoint.sh"]
