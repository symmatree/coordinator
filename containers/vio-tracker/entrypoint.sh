#!/bin/sh
set -eu

# chobitsfan feature_tracker uses fixed paths under /tmp.
for sock in /tmp/chobits_2222 /tmp/chobits_imu /tmp/chobits_features; do
	rm -f "${sock}"
done

# feature_tracker writes diagnostics ("Usb speed", "Device name", "N features"
# every ~60 frames) via std::cout. Under `docker logs` stdout is a pipe, so glibc
# uses full 8 KB block buffering instead of line buffering -- at the tracker's
# trickle output rate the buffer takes ~20 min to flush and is lost entirely on a
# crash, so the logs look empty. stdbuf forces line buffering (works because the
# binary keeps sync_with_stdio on, routing std::cout through C stdio).
exec stdbuf -oL -eL /opt/coordinator/bin/feature_tracker "$@"
