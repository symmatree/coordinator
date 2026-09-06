#!/usr/bin/env bash
# pod-camera entrypoint: one session id shared by the camera loop and the
# accelerometer reader, so a session directory holds the frames and the vibration
# record over the same interval on the same clock (#211).
set -euo pipefail

CAPTURE_DIR="${POD_CAPTURE_DIR:-/captures}"
mkdir -p "${CAPTURE_DIR}"

export POD_SESSION="${POD_SESSION:-$(date -u +%Y%m%dT%H%M%SZ)}"
echo "pod: session ${POD_SESSION}"

# The accelerometer reader is opt-in (POD_ACCEL_DEVICES) and supervised
# separately on purpose: a missing sensor, an unset dtparam=spi=on, or a bad
# solder joint must never cost us the frames. It restarts on its own; capture
# stays in the foreground so the container's health is the camera's health.
if [ -n "${POD_ACCEL_DEVICES:-}" ]; then
	(
		while true; do
			python3 /opt/pod/adxl345.py || echo "accel: exited $?, retrying in 30s"
			sleep 30
		done
	) &
fi

exec python3 /opt/pod/capture.py
