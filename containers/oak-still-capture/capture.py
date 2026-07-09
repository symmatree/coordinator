#!/usr/bin/env python3
"""OAK-D color still-capture loop (coordinator #72, phase 1).

Captures full-resolution JPEG stills from the OAK-D **RGB** camera at a fixed
cadence and writes each frame plus a JSON metadata sidecar to disk. Standalone: it
owns the OAK-D, so it runs in its own `capture` compose profile, NOT alongside the
`vio-tracker` (single USB device -- they cannot share it). Simultaneous VIO + stills
is the harder phase-3 integration into the tracker pipeline.

Feeds the mapping / imagery products in fables `Drones/rekon10/mapping.md`: optical
SfM (OpenDroneMap), timelapse, and high-res single stills. FC geotagging
(CAMERA_TRIGGER / logged CAM) is phase 2 -- not wired here.

Config via environment (all optional):
  OAK_NODE_NAME       node label in filenames/metadata (default: hostname)
  OAK_CAPTURE_DIR     output dir (default: /captures)
  OAK_CAPTURE_HZ      captures per second (default: 0.5)
  OAK_RESOLUTION      12mp | 4k | 1080p (default: 12mp -- full IMX378)
  OAK_JPEG_QUALITY    1-100 (default: 92)

The frame->disk path (`write_frame` / `build_sidecar`) is pure and unit-tested
(`test_capture.py`); the depthai device loop is hardware-gated and imported lazily
so the test runs without depthai or an OAK-D.
"""

import datetime as dt
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

import cv2
import numpy as np

# (node socket, still size (w,h)) per OAK_RESOLUTION.
RESOLUTIONS = {
    "12mp": (4056, 3040),
    "4k": (3840, 2160),
    "1080p": (1920, 1080),
}
DEFAULT_RESOLUTION = "12mp"

_stop = False


def _request_stop(signum, _frame):
    global _stop
    _stop = True
    print(f"capture: signal {signum}, stopping after current frame", flush=True)


def _env_float(name, default):
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def build_sidecar(node, seq, filename, wall, mono_ns, frame_meta):
    """Provenance sidecar for one still. `frame_meta` carries the device-side facts
    (None when unavailable). `sensor_timestamp_ns` is the device-clock capture time --
    the field that anchors PPK-style interpolation against ArduPilot pose (times may
    need later correction; see #72)."""
    return {
        "node": node,
        "seq": seq,
        "file": filename,
        "wall_clock_utc": wall.isoformat().replace("+00:00", "Z"),
        "wall_clock_unix": wall.timestamp(),
        "monotonic_ns": mono_ns,
        "sensor_timestamp_ns": frame_meta.get("sensor_timestamp_ns"),
        "device_seq": frame_meta.get("device_seq"),
        "exposure_us": frame_meta.get("exposure_us"),
        "iso": frame_meta.get("iso"),
        "width": frame_meta.get("width"),
        "height": frame_meta.get("height"),
    }


def write_frame(session_dir, node, seq, img_bgr, wall, mono_ns, frame_meta, quality):
    """Encode `img_bgr` (H,W,3 uint8 BGR) to JPEG and write it plus its JSON sidecar.
    Pure w.r.t. hardware -- takes a numpy frame, not a depthai object. Returns the
    JPEG path."""
    stem = f"{node}_{seq:08d}_{wall.strftime('%Y%m%dT%H%M%S_%f')}Z"
    jpeg_path = Path(session_dir) / f"{stem}.jpg"
    ok, buf = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError(f"JPEG encode failed for seq {seq}")
    jpeg_path.write_bytes(buf.tobytes())
    meta = dict(frame_meta)
    meta.setdefault("width", img_bgr.shape[1])
    meta.setdefault("height", img_bgr.shape[0])
    sidecar = build_sidecar(node, seq, jpeg_path.name, wall, mono_ns, meta)
    (Path(session_dir) / f"{stem}.json").write_text(json.dumps(sidecar))
    return jpeg_path


def main():
    node = os.getenv("OAK_NODE_NAME") or socket.gethostname()
    out_dir = Path(os.getenv("OAK_CAPTURE_DIR", "/captures"))
    hz = _env_float("OAK_CAPTURE_HZ", 0.5)
    quality = int(_env_float("OAK_JPEG_QUALITY", 92))
    res_key = (os.getenv("OAK_RESOLUTION") or DEFAULT_RESOLUTION).strip().lower()
    if res_key not in RESOLUTIONS:
        sys.exit(f"capture: OAK_RESOLUTION must be one of {sorted(RESOLUTIONS)} (got {res_key!r})")
    still_w, still_h = RESOLUTIONS[res_key]
    interval = 1.0 / hz if hz > 0 else 2.0

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    session = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_dir = out_dir / node / session
    session_dir.mkdir(parents=True, exist_ok=True)

    # Lazy import: keeps write_frame/build_sidecar testable without depthai/hardware.
    import depthai as dai

    pipeline = dai.Pipeline()
    cam = pipeline.create(dai.node.ColorCamera)
    cam.setBoardSocket(dai.CameraBoardSocket.CAM_A)  # RGB / center camera
    sensor_res = {
        "12mp": dai.ColorCameraProperties.SensorResolution.THE_12_MP,
        "4k": dai.ColorCameraProperties.SensorResolution.THE_4_K,
        "1080p": dai.ColorCameraProperties.SensorResolution.THE_1080_P,
    }[res_key]
    cam.setResolution(sensor_res)
    cam.setStillSize(still_w, still_h)

    xout = pipeline.create(dai.node.XLinkOut)
    xout.setStreamName("still")
    cam.still.link(xout.input)
    xin = pipeline.create(dai.node.XLinkIn)
    xin.setStreamName("control")
    xin.out.link(cam.inputControl)

    print(
        f"capture: node={node} dir={session_dir} res={res_key} "
        f"still={still_w}x{still_h} hz={hz} quality={quality}",
        flush=True,
    )

    seq = 0
    with dai.Device(pipeline) as device:
        q_still = device.getOutputQueue("still", maxSize=2, blocking=False)
        q_ctrl = device.getInputQueue("control")
        next_tick = time.monotonic()
        while not _stop:
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.1))
                continue
            next_tick += interval

            q_ctrl.send(dai.CameraControl().setCaptureStill(True))
            frame = q_still.get()  # blocks for the triggered still
            wall = dt.datetime.now(dt.timezone.utc)
            img = frame.getCvFrame()
            frame_meta = {
                "sensor_timestamp_ns": int(frame.getTimestampDevice().total_seconds() * 1e9),
                "device_seq": frame.getSequenceNum(),
                "exposure_us": int(frame.getExposureTime().total_seconds() * 1e6),
                "iso": frame.getSensitivity(),
                "width": img.shape[1],
                "height": img.shape[0],
            }
            write_frame(session_dir, node, seq, img, wall, time.monotonic_ns(), frame_meta, quality)
            seq += 1
            if seq % 10 == 0:
                print(f"capture: {seq} stills -> {session_dir}", flush=True)

    print(f"capture: stopped after {seq} stills", flush=True)


if __name__ == "__main__":
    main()
