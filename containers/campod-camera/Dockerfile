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

ARG BASE_IMAGE=debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171
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
    python3-spidev \
  && apt-get purge -y curl gpg \
  && apt-get autoremove -y \
  && rm -rf /var/lib/apt/lists/*

COPY capture.py /opt/campod/capture.py
COPY adxl345.py /opt/campod/adxl345.py
COPY entrypoint.sh /opt/campod/entrypoint.sh
RUN chmod +x /opt/campod/entrypoint.sh

# Hardware-free check of the ADXL345 register protocol, decode, and JSONL shape
# against a fake spidev -- a broken reader fails the build instead of a flight.
COPY test_adxl345.py /opt/campod/test_adxl345.py
RUN cd /opt/campod && python3 test_adxl345.py && rm test_adxl345.py

# Flush logs immediately so `coord logs -f campod-camera` is live (cf. the
# vio-tracker stdbuf line-buffering fix).
ENV PYTHONUNBUFFERED=1
WORKDIR /opt/campod

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/campod/entrypoint.sh"]
