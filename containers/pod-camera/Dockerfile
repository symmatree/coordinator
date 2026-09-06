# coordinator-pod-camera: picamera2 + libcamera still capture (arm64 / Pi Zero 2 W).
#
# Uses Raspberry Pi's apt archive (NOT stock Debian) so libcamera + picamera2 are
# the matched, Pi-pipeline-aware versions -- the one real gotcha for camera-in-
# container (stock Debian libcamera enumerates "no cameras"). Base suite tracks
# the host Pi OS, which is trixie: current Pi OS Lite arm64 (2026-06-18) is Debian
# 13 on kernel 6.18.x. Trixie also carries libcamera 0.7.x, which is where the
# ExposureTimeMode / AnalogueGainMode split capture.py needs lives.
#
# Local build on the Zero (or arm64 box):
#   docker build -t coordinator-pod-camera:local containers/pod-camera
# Cross-build (slow; QEMU):
#   docker buildx build --platform linux/arm64 -t coordinator-pod-camera:local containers/pod-camera

ARG BASE_IMAGE=debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132
FROM ${BASE_IMAGE}

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
  && apt-get update && apt-get install -y --no-install-recommends \
    dumb-init \
    python3-picamera2 \
    python3-simplejpeg \
  && apt-get purge -y curl gpg \
  && apt-get autoremove -y \
  && rm -rf /var/lib/apt/lists/*

COPY capture.py /opt/pod/capture.py
COPY entrypoint.sh /opt/pod/entrypoint.sh
RUN chmod +x /opt/pod/entrypoint.sh

# Flush logs immediately so `coord logs -f pod-camera` is live (cf. the
# vio-tracker stdbuf line-buffering fix).
ENV PYTHONUNBUFFERED=1
WORKDIR /opt/pod

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/pod/entrypoint.sh"]
