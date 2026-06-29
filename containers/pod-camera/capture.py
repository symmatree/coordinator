#!/usr/bin/env python3
"""Rekon pod still-capture loop.

Captures JPEG stills from the Camera Module 3 (IMX708) at a fixed cadence and
writes each frame plus a JSON metadata sidecar to local storage. Standalone
(Phase 2): no network, no coordination. The frame-sync hooks (pacesetter/server
+ clients) are present but default off -- they are exercised once multiple pods
share a network and time base (Phase 3, #24). See docs/pi-zero-bringup.md.

Config via environment (all optional):
  POD_NODE_NAME       node label in filenames/metadata (default: hostname)
  POD_CAPTURE_DIR     output dir (default: /captures)
  POD_CAPTURE_HZ      captures per second (default: 1.0)
  POD_CAPTURE_WIDTH   frame width  (default: 0 = sensor full resolution)
  POD_CAPTURE_HEIGHT  frame height (default: 0 = sensor full resolution)
  POD_JPEG_QUALITY    1-100 (default: 90)
  POD_SYNC_MODE       off | server | client (default: off) -- see note below
"""

import datetime as dt
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

from picamera2 import Picamera2

_stop = False


def _request_stop(signum, _frame):
    global _stop
    _stop = True
    print(f"capture: received signal {signum}, stopping after current frame", flush=True)


def _env_int(name, default):
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name, default):
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _maybe_apply_sync(picam2, mode):
    """Best-effort libcamera camera-sync configuration.

    The CM3 has no XVS hardware trigger, so multi-pod alignment uses libcamera's
    software sync (one server/pacesetter, the rest clients). The exact picamera2
    control surface is NOT verified on hardware yet, so this is guarded: a wrong
    control name logs a warning instead of killing capture. Confirm against the
    installed picamera2/libcamera and wire properly in Phase 3 (#24). With the
    default (off) this code path never runs, so standalone capture is unaffected.
    """
    if mode == "off":
        return None
    try:
        from libcamera import controls  # noqa: PLC0415  (optional, version-dependent)

        sync_enum = {
            "server": controls.rpi.SyncModeEnum.Server,
            "client": controls.rpi.SyncModeEnum.Client,
        }[mode]
        picam2.set_controls({"SyncMode": sync_enum})
        print(f"capture: sync mode set to {mode}", flush=True)
        return mode
    except Exception as exc:  # noqa: BLE001  intentionally broad; sync is optional
        print(
            f"capture: WARNING could not set sync mode {mode!r}: {exc}. "
            "Continuing unsynced; verify control surface on hardware (#24).",
            flush=True,
        )
        return None


def main():
    node = os.getenv("POD_NODE_NAME") or socket.gethostname()
    out_dir = Path(os.getenv("POD_CAPTURE_DIR", "/captures"))
    hz = _env_float("POD_CAPTURE_HZ", 1.0)
    width = _env_int("POD_CAPTURE_WIDTH", 0)
    height = _env_int("POD_CAPTURE_HEIGHT", 0)
    quality = _env_int("POD_JPEG_QUALITY", 90)
    sync_mode = (os.getenv("POD_SYNC_MODE") or "off").strip().lower()

    interval = 1.0 / hz if hz > 0 else 1.0

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    # Per-process session dir so reboots/restarts don't interleave sequences.
    session = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_dir = out_dir / node / session
    session_dir.mkdir(parents=True, exist_ok=True)

    picam2 = Picamera2()
    size = (width, height) if width and height else picam2.sensor_resolution
    config = picam2.create_still_configuration(main={"size": size})
    picam2.configure(config)
    picam2.options["quality"] = quality

    sync_active = _maybe_apply_sync(picam2, sync_mode)
    picam2.start()

    print(
        f"capture: node={node} dir={session_dir} size={size[0]}x{size[1]} "
        f"hz={hz} quality={quality} sync={sync_active or 'off'}",
        flush=True,
    )

    seq = 0
    next_tick = time.monotonic()
    try:
        while not _stop:
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.1))
                continue
            next_tick += interval

            wall = dt.datetime.now(dt.timezone.utc)
            stem = f"{node}_{seq:08d}_{wall.strftime('%Y%m%dT%H%M%S_%f')}Z"
            jpeg_path = session_dir / f"{stem}.jpg"

            metadata = picam2.capture_file(str(jpeg_path), format="jpeg")

            sidecar = {
                "node": node,
                "seq": seq,
                "file": jpeg_path.name,
                "wall_clock_utc": wall.isoformat().replace("+00:00", "Z"),
                "wall_clock_unix": wall.timestamp(),
                "monotonic_ns": time.monotonic_ns(),
                # SensorTimestamp is CLOCK_BOOTTIME ns at exposure -- the field
                # that anchors PPK-style interpolation against ArduPilot pose.
                "sensor_timestamp_ns": metadata.get("SensorTimestamp"),
                "exposure_us": metadata.get("ExposureTime"),
                "analogue_gain": metadata.get("AnalogueGain"),
                "sync_mode": sync_active or "off",
            }
            (session_dir / f"{stem}.json").write_text(json.dumps(sidecar))
            seq += 1
            if seq % 30 == 0:
                print(f"capture: {seq} frames -> {session_dir}", flush=True)
    finally:
        picam2.stop()
        print(f"capture: stopped after {seq} frames", flush=True)


if __name__ == "__main__":
    sys.exit(main())
