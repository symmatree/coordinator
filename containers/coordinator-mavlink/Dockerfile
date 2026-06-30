# coordinator-mavlink: forwards vins_fusion pose to the FC over UART as MAVLink2.
#
# Our own router (a faithful minimal port of chobitsfan mavlink-udp-proxy apm_wiki,
# minus its hardcoded GPS origin and planner/LAND path). Python + pymavlink; the
# router is low-rate (pose at image rate), so this is not CPU-bound.
#
# Local build:
#   docker build -t coordinator-mavlink:local containers/coordinator-mavlink

FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

# Pinned modern pymavlink in a venv, not the distro (Debian has no python3-pymavlink
# and is stale). DISABLE_MAVNATIVE forces the pure-python path (no C compiler);
# lxml/pyserial resolve as arm64 wheels. Versions are controlled in requirements.txt.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    dumb-init \
    python3 \
    python3-venv \
  && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt /opt/coordinator/requirements.txt
RUN python3 -m venv "${VIRTUAL_ENV}" \
  && DISABLE_MAVNATIVE=True pip install --no-cache-dir -r /opt/coordinator/requirements.txt

WORKDIR /opt/coordinator
COPY router.py test_router.py entrypoint.sh /opt/coordinator/
RUN chmod +x /opt/coordinator/entrypoint.sh /opt/coordinator/router.py

# Self-validating build: the isolation test (pose in -> decode MAVLink out) must
# pass against the venv's pymavlink, or the build fails.
RUN python3 /opt/coordinator/test_router.py

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/coordinator/entrypoint.sh"]
