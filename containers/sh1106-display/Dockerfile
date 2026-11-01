# coordinator-sh1106-display: front-panel status OLED (arm64 / Pi 4B). #115.
#
# luma.oled drives the SH1106 (128x64) on the Pi's I2C (0x3C, /dev/i2c-1) with a compact
# status screen. The pure render path is self-tested at build (test_display.py); the
# luma/I2C device loop is hardware-gated and not exercised in CI.
#
# Local build:
#   docker build -t coordinator-sh1106-display:local containers/sh1106-display
# Cross-build (slow; QEMU):
#   docker buildx build --platform linux/arm64 -t coordinator-sh1106-display:local containers/sh1106-display

FROM debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4

ARG DEBIAN_FRONTEND=noninteractive

# python venv for the pinned luma.oled wheel; libjpeg/zlib for Pillow (luma dep).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    dumb-init \
    libjpeg62-turbo \
    python3 \
    python3-venv \
    zlib1g \
  && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt /opt/display/requirements.txt
RUN python3 -m venv "${VIRTUAL_ENV}" \
  && pip install --no-cache-dir -r /opt/display/requirements.txt

WORKDIR /opt/display
COPY display.py test_display.py entrypoint.sh /opt/display/
RUN chmod +x /opt/display/entrypoint.sh /opt/display/display.py

# Self-validating build: the render + status-gathering test (no hardware) must pass.
RUN python3 /opt/display/test_display.py

# Flush logs immediately under `coord logs -f sh1106-display`.
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/display/entrypoint.sh"]
