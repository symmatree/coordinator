#!/usr/bin/env python3
"""FLIR Blackfly S still capture, over USB3 Vision through Aravis.

Free-runs the camera at a fixed rate on its own AcquisitionFrameRate, encodes each
frame as a single-channel JPEG and writes it with a JSON sidecar to local storage.
Written for the first rampod's BFS-U3-244S8M-C (IMX540 mono, 5320x4600, global
shutter), but nothing here is specific to that model beyond asking for Mono8.

The files it shares with the rest of the fleet are campod-camera's, on purpose, so
pod-link and session collection need nothing new: the capture flag it reads, the
status document it writes (pod-link reads `ready` from it), and the session directory
`<capture dir>/<node>/<boot id>/` with `manifests/` and `frames.jsonl`.

Config via environment:
  FLIR_CAPTURE_DIR     output dir (default: /captures)
  FLIR_FRAME_RATE_HZ   AcquisitionFrameRate to ask for; the camera clamps it and the
                       achieved value is logged and published (default: 1.0)
  FLIR_JPEG_QUALITY    1-100 (default: 90)
  FLIR_CAPTURE_FLAG    path whose contents gate writing frames (default:
                       /tmp/campod_capture, the file pod-link writes)
  FLIR_STATUS_FILE     path this writes its state to, for pod-link to publish
                       (default: /tmp/campod_camera_status, the file pod-link reads)
"""

import datetime as dt
import json
import os
import queue
import re
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import gi
import numpy as np
import simplejpeg

gi.require_version("Aravis", "0.8")
from gi.repository import Aravis  # noqa: E402

_stop = False


def _request_stop(signum, _frame):
    global _stop
    _stop = True
    print(f"capture: received signal {signum}, stopping", flush=True)


def _env_float(name, default):
    raw = os.getenv(name)
    return float(raw) if raw and raw.strip() else default


def _env_int(name, default):
    raw = os.getenv(name)
    return int(raw) if raw and raw.strip() else default


def _clock_triple():
    """boot/mono/wall read back to back: the same keys the accel reader and campod use."""
    return {
        "boot_ns": time.clock_gettime_ns(time.CLOCK_BOOTTIME),
        "mono_ns": time.monotonic_ns(),
        "wall_ns": time.time_ns(),
    }


def _capture_wanted(flag_path):
    """Same rule as campod-camera: the flag file's contents, read per frame. Absent is paused."""
    try:
        return flag_path.read_text().strip() in ("1", "true", "on", "yes")
    except OSError:
        return False


def _write_status(path, payload):
    """Atomic temp-and-rename on tmpfs. A status failure never stops capture."""
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except OSError:
        pass


MANIFEST_SOURCES = (
    (Path("/etc/container-image"), None),
    (Path("/etc/fleet-image"), "fleet-image"),
)


def _copy_manifests(session_dir):
    """Record what produced this session in <session>/manifests/, as campod-camera does."""
    out = session_dir / "manifests"
    out.mkdir(parents=True, exist_ok=True)
    for src, name in MANIFEST_SOURCES:
        try:
            body = src.read_text()
        except OSError as exc:
            print(f"capture: no {src} to record ({exc}); this session is unattributed", flush=True)
            continue
        if name is None:
            m = re.search(r'^FLEET_UNIT="([^"]*)"', body, re.M)
            name = m.group(1) if m else src.name
        tmp = out / f".{name}.{os.getpid()}"
        tmp.write_text(body)
        tmp.replace(out / name)


HOST_HOSTNAME_PATH = Path("/etc/host-hostname")


def _node_name():
    """The host's name from the bind-mounted /etc/hostname; the container's is its ID."""
    try:
        name = HOST_HOSTNAME_PATH.read_text(encoding="utf-8").strip()
        if name:
            return name
    except OSError:
        pass
    return socket.gethostname()


def _boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


# Re-probe cadence while no camera is attached: campod-camera's, so an operator learns one.
CAMERA_PROBE_INTERVAL_S = 30.0


def _wait_for_camera(heartbeat):
    """Block until Aravis lists a device, or until a stop signal. Returns its id or None.

    Waiting rather than exiting, for campod-camera's reason: a restart re-pays the
    import to rediscover the same absent camera. It also makes a reseated USB cable
    a non-event.
    """
    announced = False
    while not _stop:
        Aravis.update_device_list()
        if Aravis.get_n_devices() > 0:
            return Aravis.get_device_id(0)
        if not announced:
            print("capture: Aravis lists no camera. Check the USB3 cable and the camera's "
                  f"power; re-probing every {CAMERA_PROBE_INTERVAL_S:.0f}s", flush=True)
            announced = True
        heartbeat()
        deadline = time.monotonic() + CAMERA_PROBE_INTERVAL_S
        while not _stop and time.monotonic() < deadline:
            time.sleep(0.25)
    return None


class _FrameLedger:
    """One line per buffer the stream hands back, kept or not. Buffered, never fsynced."""

    def __init__(self, path):
        self._fh = open(path, "a", buffering=1 << 16)

    def record(self, rec):
        try:
            self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except OSError as exc:
            print(f"capture: frame ledger write failed: {exc}", flush=True)

    def close(self):
        try:
            self._fh.close()
        except OSError:
            pass


def _encode_mono(frame, quality):
    """A single-channel JPEG: simplejpeg's GRAY colorspace with Gray subsampling."""
    return simplejpeg.encode_jpeg(frame, quality, "GRAY", "Gray")


class _Writer:
    """Encodes and writes kept frames on one thread, off the stream.

    The capture thread has already copied the frame out of the Aravis buffer and
    given the buffer back, so a slow encode or a card stall costs queue depth here,
    not frames at the camera. The queue is bounded at QUEUE_FRAMES raw frames
    (24.5 MB each at full resolution); past that `put` blocks and the wait is counted.
    """

    QUEUE_FRAMES = 2

    def __init__(self, quality):
        self._quality = quality
        self._queue = queue.Queue(maxsize=self.QUEUE_FRAMES)
        self.waits = 0
        self.errors = 0
        self._thread = threading.Thread(target=self._loop, name="writer", daemon=True)
        self._thread.start()

    def put(self, frame, jpeg_path, sidecar_path, sidecar):
        job = (frame, jpeg_path, sidecar_path, sidecar)
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self.waits += 1
            self._queue.put(job)

    def _loop(self):
        while True:
            job = self._queue.get()
            if job is None:
                return
            frame, jpeg_path, sidecar_path, sidecar = job
            try:
                jpeg_path.write_bytes(_encode_mono(frame, self._quality))
                # The sidecar goes last, so its existence marks the frame complete.
                sidecar_path.write_bytes(sidecar)
            except Exception as exc:  # noqa: BLE001
                self.errors += 1
                print(f"capture: writing {jpeg_path.name} failed: {exc}", flush=True)

    def close(self):
        """Write everything queued, then stop."""
        self._queue.put(None)
        self._thread.join()


# Buffers handed to the Aravis stream. Each is one full frame of host memory; the board
# has 2 GB, and the camera holds frames in its own 240 MB while the host is behind.
STREAM_BUFFERS = 3


def main():
    node = _node_name()
    out_dir = Path(os.getenv("FLIR_CAPTURE_DIR", "/captures"))
    rate_hz = _env_float("FLIR_FRAME_RATE_HZ", 1.0)
    quality = _env_int("FLIR_JPEG_QUALITY", 90)
    flag_path = Path(os.getenv("FLIR_CAPTURE_FLAG", "/tmp/campod_capture"))
    status_path = Path(os.getenv("FLIR_STATUS_FILE", "/tmp/campod_camera_status"))

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    session = _boot_id()
    session_dir = out_dir / node / session
    session_dir.mkdir(parents=True, exist_ok=True)
    _copy_manifests(session_dir)

    # `ready` is the arm answer, as on a campod: warm, and frames arriving. There is no
    # sync on this camera yet, so nothing else goes into it.
    state = {
        "node": node,
        "session": session,
        "camera": "waiting",
        "phase": "startup",
        "ready": False,
        "ready_at_mono_s": None,
        "warm": False,
        "warm_at_mono_s": None,
        "capturing": False,
        "frames": 0,
        "frames_seen": 0,
        "last_frame_mono_s": None,
        "frame_rate_hz": None,
    }

    def publish():
        state["ready"] = state["warm"] and state["camera"] == "streaming"
        if state["ready"] and state["ready_at_mono_s"] is None:
            state["ready_at_mono_s"] = round(time.monotonic(), 3)
        state["as_of_mono_s"] = round(time.monotonic(), 3)
        _write_status(status_path, state)

    publish()
    device_id = _wait_for_camera(heartbeat=publish)
    if device_id is None:
        state["camera"] = "stopped-while-waiting"
        publish()
        return 0

    camera = Aravis.Camera.new(device_id)
    camera.set_pixel_format(Aravis.PIXEL_FORMAT_MONO_8)
    # Aravis's FLIR branch sets AcquisitionFrameRateEnable and turns AcquisitionFrameRateAuto
    # off before writing the rate, and clamps to the camera's bounds; read back what it got.
    camera.set_frame_rate(rate_hz)
    achieved_hz = camera.get_frame_rate()
    camera.set_acquisition_mode(Aravis.AcquisitionMode.CONTINUOUS)
    payload = camera.get_payload()
    region = camera.get_region()
    state["camera"] = "present"
    state["frame_rate_hz"] = achieved_hz
    publish()
    print(f"capture: {camera.get_vendor_name()} {camera.get_model_name()} "
          f"serial {camera.get_device_serial_number()} as {device_id}; "
          f"{region.width}x{region.height} Mono8, asked {rate_hz} Hz, camera runs "
          f"{achieved_hz} Hz; node={node} dir={session_dir} quality={quality}", flush=True)

    stream = camera.create_stream(None, None)
    for _ in range(STREAM_BUFFERS):
        stream.push_buffer(Aravis.Buffer.new_allocate(payload))

    writer = _Writer(quality)
    ledger = _FrameLedger(session_dir / "frames.jsonl")
    # Three periods, so one late frame is not a timeout, and never under a second.
    pop_timeout_us = int(max(3.0 / achieved_hz, 1.0) * 1e6)
    seq = 0
    timeouts = 0
    failures = 0
    state["phase"] = "warmup"
    publish()
    camera.start_acquisition()
    try:
        while not _stop:
            buf = stream.timeout_pop_buffer(pop_timeout_us)
            if buf is None:
                timeouts += 1
                if state["camera"] != "no-frames":
                    print(f"capture: no frame within {pop_timeout_us / 1e6:.1f}s", flush=True)
                state["camera"] = "no-frames"
                publish()
                continue
            clk = _clock_triple()
            status = buf.get_status()
            if status != Aravis.BufferStatus.SUCCESS:
                failures += 1
                ledger.record({"t": "f", "seen": state["frames_seen"],
                               "decision": f"status_{status.value_nick}", "clk": clk})
                stream.push_buffer(buf)
                continue

            width, height = buf.get_image_width(), buf.get_image_height()
            device_ts = buf.get_timestamp()
            system_ts = buf.get_system_timestamp()
            frame_id = buf.get_frame_id()
            # get_data() is a copy, which is what lets the buffer go straight back.
            data = buf.get_data()
            stream.push_buffer(buf)
            frame = np.frombuffer(data, dtype=np.uint8)[: width * height].reshape(height, width, 1)

            if state["camera"] != "streaming":
                state["camera"] = "streaming"
            if not state["warm"]:
                # Warm is the encode and a card write exercised once on a real frame, so
                # the first armed frame does not pay for them.
                probe = session_dir / ".warmup-probe.jpg"
                probe.write_bytes(_encode_mono(frame, quality))
                size_kb = probe.stat().st_size // 1024
                probe.unlink()
                state["warm"] = True
                state["warm_at_mono_s"] = round(time.monotonic(), 3)
                state["phase"] = "capture"
                print(f"capture: WARM; warmup exercised the encode and a card write "
                      f"({frame.nbytes // 1024} kB in, {size_kb} kB out)", flush=True)

            wanted = _capture_wanted(flag_path)
            if wanted != state["capturing"]:
                print(f"capture: {'capturing' if wanted else 'paused'} "
                      f"({seq} frames written)", flush=True)
                state["capturing"] = wanted

            ledger.record({
                "t": "f",
                "seen": state["frames_seen"],
                "decision": "kept" if wanted else "paused",
                "frame_id": frame_id,
                "device_timestamp_ns": device_ts,
                "system_timestamp_ns": system_ts,
                "clk": clk,
                "kept_seq": seq if wanted else None,
            })
            state["frames_seen"] += 1

            if wanted:
                wall = dt.datetime.fromtimestamp(system_ts / 1e9, dt.timezone.utc)
                stem = f"{node}_{seq:08d}_{wall.strftime('%Y%m%dT%H%M%S_%f')}Z"
                sidecar = {
                    "node": node,
                    "seq": seq,
                    "file": f"{stem}.jpg",
                    "frame_id": frame_id,
                    # The camera's own clock, from the stream's leader packet. Not yet
                    # related to host time; latching it against PPS is what would.
                    "device_timestamp_ns": device_ts,
                    # Host CLOCK_REALTIME when Aravis received the frame's leader --
                    # after the exposure by an unmeasured transfer latency.
                    "system_timestamp_ns": system_ts,
                    "clk": clk,
                    "size": [width, height],
                    "pixel_format": "Mono8",
                    "frame_rate_hz": achieved_hz,
                    "camera_serial": camera.get_device_serial_number(),
                }
                writer.put(frame, session_dir / f"{stem}.jpg", session_dir / f"{stem}.json",
                           json.dumps(sidecar).encode())
                seq += 1
                state["frames"] = seq
                state["last_frame_mono_s"] = round(clk["mono_ns"] / 1e9, 3)
                if seq % 30 == 0:
                    print(f"capture: {seq} frames -> {session_dir}", flush=True)
            publish()
    finally:
        camera.stop_acquisition()
        writer.close()
        ledger.close()
        completed, stream_failures, underruns = stream.get_statistics()
        state["phase"] = "stopped"
        state["warm"] = False
        state["capturing"] = False
        publish()
        print(f"capture: stopped after {seq} frames kept of {state['frames_seen']} seen; "
              f"stream completed {completed}, failed {stream_failures}, "
              f"underran {underruns}; {timeouts} timeouts, {failures} bad buffers, "
              f"{writer.waits} waits on the writer, {writer.errors} write errors",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
