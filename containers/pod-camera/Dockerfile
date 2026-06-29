# coordinator-pod-camera: picamera2 + libcamera still capture (arm64 / Pi Zero 2 W).
#
# Uses Raspberry Pi's apt archive (NOT stock Debian) so libcamera + picamera2 are
# the matched, Pi-pipeline-aware versions -- the one real gotcha for camera-in-
# container (stock Debian libcamera enumerates "no cameras"). Base suite tracks
# the host Pi OS (bookworm).
#
# Local build on the Zero (or arm64 box):
#   docker build -t coordinator-pod-camera:local containers/pod-camera
# Cross-build (slow; QEMU):
#   docker buildx build --platform linux/arm64 -t coordinator-pod-camera:local containers/pod-camera

ARG BASE_IMAGE=debian:bookworm-slim
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
# Raspberry Pi apt suite; keep aligned with the host Pi OS release.
ARG RPI_SUITE=bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
  && curl -fsSL https://archive.raspberrypi.com/debian/raspberrypi.gpg.key \
       | gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg \
  && echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] http://archive.raspberrypi.com/debian/ ${RPI_SUITE} main" \
       > /etc/apt/sources.list.d/raspi.list \
  && apt-get update && apt-get install -y --no-install-recommends \
    dumb-init \
    python3-picamera2 \
    python3-simplejpeg \
  && apt-get purge -y curl gnupg \
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
