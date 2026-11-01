# coordinator-campod-camera: libcamera still capture (arm64 / Pi Zero 2 W), in C++
# (campod-capture) and in Python (capture.py, picamera2), plus the accelerometer reader.
#
# Uses Raspberry Pi's apt archive (NOT stock Debian) so libcamera + picamera2 are
# the matched, Pi-pipeline-aware versions -- the one real gotcha for camera-in-
# container (stock Debian libcamera enumerates "no cameras"). RPI_SUITE must match
# the suite the campod image is built from (dotfiles-symm/pi-image/build-image.sh):
# the container's libcamera talks to the host kernel, so the two move together.
#
# The build context is containers/, for the shared control loop in capture-common.
#
# Local build (arm64 box, or QEMU):
#   docker build -t coordinator-campod-camera:local -f containers/campod-camera/Dockerfile containers

# Declared before the first FROM so it is a GLOBAL arg: an ARG after a FROM
# belongs to that stage only, and the final `FROM ${BASE_IMAGE}` could not see it.
ARG BASE_IMAGE=debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4

# The accelerometer reader is Go, not Python, because it needs to build a proper
# multi-transfer SPI message with explicit inter-transfer delays -- which py-spidev
# cannot express -- and that is what makes running the bus above 1.6 MHz legal.
# Raising the clock from 1.5 to 3 MHz took overruns from 2516 to 3 per 30 s.
#
# NOT for batching. Batching a drain into one ioctl saves syscalls and measured no
# improvement at all (103.5 vs 105.5 us/sample); the part requires one SPI
# transaction per FIFO entry regardless. See accel/README notes and DrainFIFO.
#
# --platform=$BUILDPLATFORM so the compile and the tests run natively on the
# builder instead of under QEMU; only the output is arm64.
FROM --platform=$BUILDPLATFORM golang:1.26-trixie@sha256:a050dcceee6eb9bf110151662dfac7662f3bec40927c7dee48731e3d92083d42 AS accel-build
WORKDIR /src
COPY campod-camera/accel/ ./
# Hardware-free: a fake SPI part covers the register protocol, the signed decode
# across the 13-bit boundary, DEVID gating, the self-test verdict against the
# datasheet's signed per-axis limits, that a drain is exactly one ioctl, and that
# the batch pool never blocks the capture loop. A broken reader fails the build.
RUN go vet ./... && go test ./...
ARG TARGETARCH
# GIT_SHA is stamped into the binary and lands in every capture header. Without it
# a file records only that its reader was "the Go one", which identifies the
# language and not the build -- and which commit wrote a capture is provenance
# that cannot be recovered afterwards. Empty for a local build, which the reader
# reports as such rather than pretending.
ARG GIT_SHA=""
RUN CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH} \
      go build -trimpath -ldflags="-s -w -X main.buildSHA=${GIT_SHA}" -o /out/campod-accel .

# Debian with the Raspberry Pi archive configured, and nothing installed from it yet: the
# base of both the C++ build and the runtime, so the two resolve libcamera from the same
# archive in the same build.
FROM ${BASE_IMAGE} AS rpi-apt

ARG DEBIAN_FRONTEND=noninteractive
# Raspberry Pi apt suite; keep aligned with the host Pi OS release.
ARG RPI_SUITE=trixie
# Take the archive key from the KEYRING PACKAGE, not from the raw
# archive.raspberrypi.com/debian/raspberrypi.gpg.key URL.
#
# Same key, same fingerprint -- but the raw URL still carries the original 2012
# SHA-1 self-signatures (digest algo 2), and trixie's apt verifies with Sequoia
# (`sqv`), whose policy has rejected SHA-1 since 2026-02-01. Result on a trixie
# base: "Signing key on CF8A1AF5... is not bound ... SHA1 is not considered
# secure", and the repo is treated as unsigned. The 2025 keyring package ships the
# same key re-signed with SHA-512 (digest algo 10), which sqv accepts.
#
# So this is the fix, not a workaround -- no verification is relaxed anywhere. The
# extracted keyring's fingerprint is asserted below, which is strictly more than
# the raw-URL fetch used to do.
ARG RPI_KEYRING_DEB=pool/main/r/raspberrypi-archive-keyring/raspberrypi-archive-keyring_2025.1+rpt1_all.deb
ARG RPI_KEY_FPR="CF8A 1AF5 02A2 AA2D 763B AE7E 82B1 2992 7FA3 303E"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gpg \
  && curl -fsSL "https://archive.raspberrypi.com/debian/${RPI_KEYRING_DEB}" -o /tmp/rpi-keyring.deb \
  && dpkg-deb --fsys-tarfile /tmp/rpi-keyring.deb \
       | tar -xOf - ./usr/share/keyrings/raspberrypi-archive-keyring.pgp \
       > /usr/share/keyrings/raspberrypi-archive-keyring.gpg \
  && gpg --show-keys --with-colons --with-fingerprint \
       /usr/share/keyrings/raspberrypi-archive-keyring.gpg \
       | grep -q "^fpr:::::::::$(echo "${RPI_KEY_FPR}" | tr -d " "):" \
  && rm -f /tmp/rpi-keyring.deb \
  && echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] http://archive.raspberrypi.com/debian/ ${RPI_SUITE} main" \
       > /etc/apt/sources.list.d/raspi.list \
  && apt-get purge -y curl gpg \
  && apt-get autoremove -y \
  && rm -rf /var/lib/apt/lists/*

# campod-capture, the C++ capture, against the archive's libcamera. The tests are
# hardware-free and a failure fails the build.
FROM rpi-apt AS capture-build
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake \
    g++ \
    libcamera-dev \
    libturbojpeg0-dev \
    ninja-build \
    nlohmann-json3-dev \
    pkg-config \
  && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY capture-common/ capture-common/
COPY campod-camera/CMakeLists.txt campod-camera/
COPY campod-camera/src/ campod-camera/src/
RUN cmake -S campod-camera -B build -GNinja -DCMAKE_BUILD_TYPE=Release \
  && cmake --build build --parallel "$(nproc)" \
  && ./build/test_campod \
  && ./build/capture-common/test_conventions \
  && ./build/capture-common/test_session

FROM rpi-apt
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    dumb-init \
    libturbojpeg0 \
    python3-picamera2 \
    python3-simplejpeg \
  && rm -rf /var/lib/apt/lists/*

# The build provenance, as a file the payload can read without knowing it runs in
# a container. The same values the workflow attaches as OCI labels, which are
# recoverable with `docker inspect` but only by something that knows to ask
# docker. The captures and campod-accel copy this into each session's manifests/.
#
# Format per #326: a flat table of double-quoted strings, which is valid TOML AND
# a sourceable shell file AND a systemd EnvironmentFile at once. Two rules, both
# easy to break by accident -- NO whitespace around `=`, because `source` would
# read it as a command; and no `$` in a value, because sourcing expands it.
#
# Controlled keys are the OCI label names with dots to underscores, uppercased.
# FLEET_SOURCE_REF is ours and fully qualified (refs/heads/main), per #336: the
# OCI spec scopes ref.name to image layouts, so it never meant the source branch.
# FLEET_UNIT is artifact-specific and uncontrolled: it names the output file when
# this is copied into a session, since the OCI title is the repo's, not the
# image's.
#
# GIT_SHA is re-declared here: it is passed to the accel-build stage above, and an
# ARG belongs to the stage that declares it. Both are empty for a local build,
# which yields empty strings rather than absent keys.
ARG GIT_SHA=""
ARG GIT_REF=""
RUN printf '%s\n' \
      "# campod-camera build manifest, written by its Dockerfile." \
      "ORG_OPENCONTAINERS_IMAGE_SOURCE=\"https://github.com/symmatree/coordinator\"" \
      "ORG_OPENCONTAINERS_IMAGE_REVISION=\"${GIT_SHA}\"" \
      "FLEET_SOURCE_REF=\"${GIT_REF}\"" \
      "FLEET_UNIT=\"campod-camera\"" \
      > /etc/container-image

COPY campod-camera/capture.py /opt/campod/capture.py
COPY --from=accel-build /out/campod-accel /opt/campod/campod-accel
COPY --from=capture-build /src/build/campod-capture /opt/campod/campod-capture
# The runtime's libcamera is whatever picamera2 pulled from the archive; this fails the
# build if that is not the soname the C++ capture was linked against.
RUN ! ldd /opt/campod/campod-capture | grep "not found"
COPY campod-camera/test_capture_wait.py /opt/campod/test_capture_wait.py
RUN cd /opt/campod && python3 test_capture_wait.py --require-manifest && rm test_capture_wait.py
# Which pod paces the others. Runs at build time like the wait test above, so a change
# that makes every pod a client -- sync that looks on while nothing is synchronised --
# fails the build rather than the flight.
COPY campod-camera/test_sync_leader.py /opt/campod/test_sync_leader.py
RUN cd /opt/campod && python3 test_sync_leader.py && rm test_sync_leader.py
# libcamera's sync parameters live in the sensor tuning JSON. These are the installed
# imx708 files with rpi.sync's ready_frame and sync_period set; libcamera searches
# LIBCAMERA_IPA_CONFIG_PATH before its own directory. Generated from the files this
# build installed, so they cannot fall behind the libcamera that reads them.
COPY campod-camera/sync_tuning.py /opt/campod/sync_tuning.py
RUN python3 /opt/campod/sync_tuning.py && rm /opt/campod/sync_tuning.py
ENV LIBCAMERA_IPA_CONFIG_PATH=/opt/campod/libcamera-ipa

# Flush logs immediately so `coord logs -f campod-camera` is live (cf. the
# vio-tracker stdbuf line-buffering fix).
ENV PYTHONUNBUFFERED=1
WORKDIR /opt/campod

# One image, two services: the stack runs this twice with different commands
# (see stacks/campod/compose.yaml). dumb-init is PID 1 in each, with exactly one
# child, so a signal has one place to go.
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["python3", "/opt/campod/capture.py"]
