# coordinator-oak-still-capture: OAK-D RGB still capture (arm64 / Pi 4B). #72 phase 1.
#
# depthai-python + OpenCV; captures full-res color stills to disk with JSON sidecars.
# Owns the OAK-D, so it runs in the `capture` compose profile -- NOT alongside
# vio-tracker (single USB device).
#
# Local build:
#   docker build -t coordinator-oak-still-capture:local containers/oak-still-capture
# Cross-build (slow; QEMU):
#   docker buildx build --platform linux/arm64 -t coordinator-oak-still-capture:local containers/oak-still-capture

FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

# libusb for the OAK-D; python venv for pinned wheels (depthai/opencv ship arm64 wheels).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    dumb-init \
    libusb-1.0-0 \
    python3 \
    python3-venv \
  && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt /opt/oak/requirements.txt
RUN python3 -m venv "${VIRTUAL_ENV}" \
  && pip install --no-cache-dir -r /opt/oak/requirements.txt

WORKDIR /opt/oak
COPY capture.py test_capture.py entrypoint.sh /opt/oak/
RUN chmod +x /opt/oak/entrypoint.sh /opt/oak/capture.py

# Self-validating build: the writer test (synthetic frame -> JPEG + sidecar) must pass.
# depthai is not exercised here (hardware-gated); the frame->disk path is.
RUN python3 /opt/oak/test_capture.py

# Flush logs immediately under `coord logs -f oak-still-capture` (cf. vio-tracker).
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/oak/entrypoint.sh"]
