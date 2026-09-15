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
# It is a Go binary rather than python3 adxl345.py because a FIFO drain has to be
# one ioctl; see accel/spidev.go. It owns its own capture thread, batch pool and
# writer goroutines, and it fsyncs only on shutdown -- so a slow SD card can no
# longer delay a drain, which is what was costing ~2% of samples.
#
# Supervised separately on purpose: a missing sensor, an unset dtparam=spi=on, or
# a bad solder joint must never cost us the frames. The restart is also the only
# recovery path -- each run re-probes, so a sensor connected mid-session is picked
# up within 30 s.
#
# The converse now holds too, which it did not before: capture is still exec'd in
# the foreground, so the container lives and dies with it, but a missing CAMERA no
# longer kills it -- capture.py waits and re-probes on the same 30 s cadence
# instead of exiting. Before that, an absent camera restarted the container every
# couple of minutes and took these accelerometers down with it. The consequence to
# know: the container being Up no longer implies the camera is present. The log
# says which, and says it once a miss plus a heartbeat, not on a loop.
(
	while true; do
		/opt/campod/campod-accel || echo "accel: exited $?, retrying in 30s"
		sleep 30
	done
) &

exec python3 /opt/campod/capture.py
