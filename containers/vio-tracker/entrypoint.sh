#!/bin/sh
set -eu

# chobitsfan feature_tracker uses fixed paths under /tmp.
for sock in /tmp/chobits_2222 /tmp/chobits_imu /tmp/chobits_features; do
	rm -f "${sock}"
done

exec /opt/coordinator/bin/feature_tracker "$@"
