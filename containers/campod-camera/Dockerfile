# coordinator-campod-camera: picamera2 + libcamera still capture (arm64 / Pi Zero 2 W).
#
# Uses Raspberry Pi's apt archive (NOT stock Debian) so libcamera + picamera2 are
# the matched, Pi-pipeline-aware versions -- the one real gotcha for camera-in-
# container (stock Debian libcamera enumerates "no cameras"). Base suite tracks
# the host Pi OS, and the campod image is pinned to BOOKWORM -- 2025-05-13, the
# last Bookworm Lite release, deliberately pinned by URL+sha in
# dotfiles-symm/pi-image/build-image.sh. It is not stock current Pi OS.
#
# This was briefly bumped to trixie on the premise that the host would be current
# stock Pi OS (2026-06-18, Debian 13). That premise was wrong: the campods boot
# the btrfs image, which is Bookworm. Bookworm's Pi archive carries libcamera
# 0.5.2, which already has the ExposureTimeMode / AnalogueGainMode split capture.py
# needs, so nothing is lost by matching the host.
#
# Local build on the Zero (or arm64 box):
#   docker build -t coordinator-campod-camera:local containers/campod-camera
# Cross-build (slow; QEMU):
#   docker buildx build --platform linux/arm64 -t coordinator-campod-camera:local containers/campod-camera

# Declared before the first FROM so it is a GLOBAL arg: an ARG after a FROM
# belongs to that stage only, and the final `FROM ${BASE_IMAGE}` could not see it.
ARG BASE_IMAGE=debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

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
FROM --platform=$BUILDPLATFORM golang:1.26-bookworm@sha256:9fdc884aacc3bec89b20ffc69f4bb369c78210e3e4f600387b5128b12c199f81 AS accel-build
WORKDIR /src
COPY accel/ ./
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

FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
# Raspberry Pi apt suite; keep aligned with the host Pi OS release.
ARG RPI_SUITE=bookworm
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
  && apt-get update && apt-get install -y --no-install-recommends \
    dumb-init \
    python3-picamera2 \
    python3-simplejpeg \
  && apt-get purge -y curl gpg \
  && apt-get autoremove -y \
  && rm -rf /var/lib/apt/lists/*

COPY capture.py /opt/campod/capture.py
COPY --from=accel-build /out/campod-accel /opt/campod/campod-accel
COPY test_capture_wait.py /opt/campod/test_capture_wait.py
RUN cd /opt/campod && python3 test_capture_wait.py && rm test_capture_wait.py

# Flush logs immediately so `coord logs -f campod-camera` is live (cf. the
# vio-tracker stdbuf line-buffering fix).
ENV PYTHONUNBUFFERED=1
WORKDIR /opt/campod

# One image, two services: the stack runs this twice with different commands
# (see stacks/campod/compose.yaml). dumb-init is PID 1 in each, with exactly one
# child, so a signal has one place to go.
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["python3", "/opt/campod/capture.py"]
