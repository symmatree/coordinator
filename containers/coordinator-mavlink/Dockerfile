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

# pymavlink isn't in Debian; install via pip. DISABLE_MAVNATIVE forces the pure-
# python path (no C compiler needed); lxml/pyserial come as arm64 wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    dumb-init \
    python3 \
    python3-pip \
  && DISABLE_MAVNATIVE=True pip3 install --no-cache-dir --break-system-packages \
    pymavlink==2.4.49 pyserial==3.5 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/coordinator
COPY router.py test_router.py entrypoint.sh /opt/coordinator/
RUN chmod +x /opt/coordinator/entrypoint.sh /opt/coordinator/router.py

# Self-validating build: the isolation test (pose in -> decode MAVLink out) must
# pass against the image's own pymavlink, or the build fails.
RUN python3 /opt/coordinator/test_router.py

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/coordinator/entrypoint.sh"]
