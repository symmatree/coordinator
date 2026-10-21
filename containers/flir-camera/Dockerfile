# coordinator-flir-camera: FLIR Blackfly S still capture over USB3 Vision (arm64 / rampod).
#
# Aravis, PyGObject and numpy are Debian trixie's own packages; Aravis reaches the
# camera through libusb, so there is no kernel module and no vendor SDK. simplejpeg is
# not packaged in Debian, so it is the one pinned wheel, in a venv that can see the
# system packages.
#
# The capture loop is tested at build against Aravis's built-in simulated camera
# (test_capture.py), so a broken loop fails the build rather than the first flight.
#
# Local build:
#   docker build -t coordinator-flir-camera:local containers/flir-camera

FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132

ARG DEBIAN_FRONTEND=noninteractive

# aravis-tools-cli is arv-tool-0.8, for listing the camera and reading its features at
# the bench.
RUN apt-get update && apt-get install -y --no-install-recommends \
    aravis-tools-cli \
    dumb-init \
    gir1.2-aravis-0.8 \
    python3 \
    python3-gi \
    python3-numpy \
    python3-venv \
  && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt /opt/flir/requirements.txt
RUN python3 -m venv --system-site-packages "${VIRTUAL_ENV}" \
  && pip install --no-cache-dir --no-deps --require-hashes -r /opt/flir/requirements.txt

# Build provenance, in campod-camera's format (#326): valid TOML, a sourceable shell
# file and a systemd EnvironmentFile at once, so no whitespace around `=` and no `$`.
# capture.py copies it into each session's manifests/ under FLEET_UNIT's name.
ARG GIT_SHA=""
ARG GIT_REF=""
RUN printf '%s\n' \
      "# flir-camera build manifest, written by its Dockerfile." \
      "ORG_OPENCONTAINERS_IMAGE_SOURCE=\"https://github.com/symmatree/coordinator\"" \
      "ORG_OPENCONTAINERS_IMAGE_REVISION=\"${GIT_SHA}\"" \
      "FLEET_SOURCE_REF=\"${GIT_REF}\"" \
      "FLEET_UNIT=\"flir-camera\"" \
      > /etc/container-image

COPY capture.py /opt/flir/capture.py
COPY test_capture.py /opt/flir/test_capture.py
RUN cd /opt/flir && python3 test_capture.py --require-manifest && rm test_capture.py

ENV PYTHONUNBUFFERED=1
WORKDIR /opt/flir

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["python3", "/opt/flir/capture.py"]
