#!/usr/bin/env bash
# campod-camera entrypoint: one session id shared by the camera loop and the
# accelerometer reader, so a session directory holds the frames and the vibration
# record over the same interval on the same clock (#211).
set -euo pipefail

CAPTURE_DIR="${CAMPOD_CAPTURE_DIR:-/captures}"
mkdir -p "${CAPTURE_DIR}"

export CAMPOD_SESSION="${CAMPOD_SESSION:-$(date -u +%Y%m%dT%H%M%SZ)}"
echo "campod: session ${CAMPOD_SESSION}"

# The accelerometer reader always runs; it probes both chip selects and logs
# whichever answers. There is nothing to enable, because SPI has no enumeration --
# the spidev nodes exist whether or not a sensor is wired, so only a DEVID read
# tells you anything, and the reader has to do that regardless.
#
# Supervised separately on purpose: a missing sensor, an unset dtparam=spi=on, or
# a bad solder joint must never cost us the frames. capture stays in the
# foreground so the container's health is the camera's health. The restart is also
# the only recovery path -- each run re-probes, so a sensor connected mid-session
# is picked up within 30 s.
(
	while true; do
		python3 /opt/campod/adxl345.py || echo "accel: exited $?, retrying in 30s"
		sleep 30
	done
) &

exec python3 /opt/campod/capture.py
