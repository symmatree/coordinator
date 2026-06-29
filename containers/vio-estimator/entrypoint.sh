#!/bin/sh
set -eu

# vins_fusion binds these AF_UNIX dgram sockets at startup (and registerPub binds
# /tmp/chobits_1234 for its outbound pose datagrams to /tmp/chobits_server). Clear
# stale nodes first so a restart after an unclean exit can re-bind.
for sock in /tmp/chobits_imu /tmp/chobits_features /tmp/chobits_1234; do
	rm -f "${sock}"
done

# OUTPUT_FOLDER (output_path in the config) must exist: vins writes
# extrinsic_parameter.csv and trajectory dumps there.
mkdir -p /tmp/vins

# Config is required as argv[1] (vins_fusion exits with usage text otherwise).
# Override with VINS_CONFIG; default matches the read-only /config mount.
CONFIG="${VINS_CONFIG:-/config/oak_d.yaml}"

# Like feature_tracker, vins_fusion trickles std::cout diagnostics; under
# `docker logs` stdout is a pipe so glibc block-buffers and the logs look empty.
# stdbuf forces line buffering. See containers/vio-tracker/entrypoint.sh.
exec stdbuf -oL -eL /opt/coordinator/bin/vins_fusion "${CONFIG}"
