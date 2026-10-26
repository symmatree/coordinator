# coordinator-oak-capture: colour, both monos and the IMU from one OAK (arm64).
#
# Built on oak-capture-base, which carries the toolchain and depthai-core v3 at
# /opt/depthai; the runtime takes only the binary and depthai's two shared libraries.
#
# The build context is containers/, for the shared control loop in capture-common.
#
# Local build (arm64 box, or QEMU):
#   docker build -t coordinator-oak-capture:local -f containers/oak-capture/Dockerfile containers
FROM ghcr.io/symmatree/coordinator-oak-capture-base:main@sha256:50b995654204f409119b3cfa33afdbb51ad79436a3ef9eb0a5d3b4db4b11ee6a AS builder

WORKDIR /src
COPY capture-common/ capture-common/
COPY oak-capture/CMakeLists.txt oak-capture/
COPY oak-capture/src/ oak-capture/src/
RUN cmake -S oak-capture -B build -GNinja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/opt/depthai \
  && cmake --build build --parallel "$(nproc)" \
  && ./build/capture-common/test_conventions \
  && ./build/capture-common/test_session

# The same Debian as coordinator-mavlink, so the base layer is already on the coordinator.
FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    dumb-init \
  && rm -rf /var/lib/apt/lists/*

# libdepthai-core.so finds libusb beside it ($ORIGIN), and the binary finds both through
# the build-tree rpath, which is the same path here. ldd fails the build if not.
COPY --from=builder /opt/depthai/lib/libdepthai-core.so /opt/depthai/lib/libusb-1.0.so /opt/depthai/lib/
COPY --from=builder /src/build/oak-capture /usr/local/bin/oak-capture
RUN ! ldd /usr/local/bin/oak-capture | grep "not found"

# Build provenance, copied into each session's manifests/ as `oak-capture`. Same format
# and keys as campod-camera's /etc/container-image (#326): no whitespace around `=`, no
# `$` in a value.
ARG GIT_SHA=""
ARG GIT_REF=""
RUN printf '%s\n' \
      "# oak-capture build manifest, written by its Dockerfile." \
      "ORG_OPENCONTAINERS_IMAGE_SOURCE=\"https://github.com/symmatree/coordinator\"" \
      "ORG_OPENCONTAINERS_IMAGE_REVISION=\"${GIT_SHA}\"" \
      "FLEET_SOURCE_REF=\"${GIT_REF}\"" \
      "FLEET_UNIT=\"oak-capture\"" \
      > /etc/container-image

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/usr/local/bin/oak-capture"]
