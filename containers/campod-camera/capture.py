#!/usr/bin/env python3
"""Rekon campod still-capture loop.

Captures JPEG stills from the Camera Module 3 (IMX708) at a fixed cadence and
writes each frame plus a JSON metadata sidecar to local storage. Standalone
(Phase 2): no network, no coordination. The frame-sync hooks (pacesetter/server
+ clients) are present but default off -- they are exercised once multiple campods
share a network and time base (#24). See docs/campod.md.

Config via environment (all optional):
  CAMPOD_NODE_NAME       node label in filenames/metadata (default: hostname)
  CAMPOD_CAPTURE_DIR     output dir (default: /captures)
  CAMPOD_CAPTURE_HZ      captures per second (default: 1.0)
  CAMPOD_CAPTURE_WIDTH   frame width  (default: 0 = sensor full resolution)
  CAMPOD_CAPTURE_HEIGHT  frame height (default: 0 = sensor full resolution)
  CAMPOD_JPEG_QUALITY    1-100 (default: 90)
  CAMPOD_STILL_MAX_EXPOSURE_US
                      cap the shutter (default: 5000; 0 = uncapped AE)
  CAMPOD_STILL_FOCUS     auto | infinity | <dioptres> (default: auto)
  CAMPOD_SYNC_MODE       off | server | client (default: off) -- see note below
"""

import datetime as dt
import json
import os
import re
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
    # Not "after current frame": this also fires while waiting for a camera,
    # when there is no frame in flight and never has been.
    print(f"capture: received signal {signum}, stopping", flush=True)


def _env_int(name, default):
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name, default):
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _apply_exposure_cap(picam2, max_us):
    """Cap the shutter, letting auto-exposure trade to gain instead.

    Motion blur was the killer on the OAK-D stills: auto-exposure ran to ~30 ms
    (T9/E18 in analysis/vio-quality-experiments.md), which is why the OAK-D side
    ships OAK_STILL_MAX_EXPOSURE_US=5000. The IMX708 makes it worse, not better --
    full-res readout is 68 ms (2592 rows x 26.29 us line time, from the imx708
    driver's line_length_pix=0x3d20 / pixel_rate=595200000), so a long exposure
    stacks blur on top of a long rolling-shutter window.

    libcamera has no "maximum AE exposure" control, and FrameDurationLimits cannot
    do it either: at 4608x2592 the minimum frame duration is already ~70 ms. The
    control that does work is the exposure/gain mode split added in libcamera 0.4:
    ExposureTimeMode=Manual pins the shutter while AnalogueGainMode=Auto leaves the
    AEGC free to make up the light in gain. That is exactly "cap the shutter, trade
    to ISO".

    Hardware-unverified (no Zero + CM3 has run this yet), so it is best-effort: if
    the split is unsupported the frames still capture, with a warning, and the
    per-frame sidecar records what the sensor actually did.
    """
    if max_us <= 0:
        return None
    try:
        from libcamera import controls  # noqa: PLC0415  (version-dependent)

        picam2.set_controls(
            {
                "ExposureTimeMode": controls.ExposureTimeModeEnum.Manual,
                "ExposureTime": max_us,
                "AnalogueGainMode": controls.AnalogueGainModeEnum.Auto,
            }
        )
        print(f"capture: exposure pinned to {max_us} us, gain left on AEGC", flush=True)
        return max_us
    except Exception as exc:  # noqa: BLE001  the cap is best-effort, capture is not
        print(
            f"capture: WARNING could not pin exposure to {max_us} us: {exc}. "
            "Continuing with full auto-exposure -- expect motion blur in flight; "
            "check the sidecar exposure_us against this cap.",
            flush=True,
        )
        return None


def _apply_focus(picam2, focus):
    """Fix the lens, or leave autofocus running.

    The CM3 focuses with a voice coil, so AF can hunt mid-flight and the lens is
    free to move under vibration (docs/campod.md: prefer a locked lens, and
    do not let AF move right before a shot on a vibrating airframe).

    Units are NOT the OAK-D's 0-255 lens scale: libcamera LensPosition is in
    DIOPTRES, the reciprocal of focus distance in metres. 0.0 is infinity, 0.5 is
    2 m, 2.0 is 0.5 m. Copying OAK_STILL_FOCUS=125 here would ask for 8 mm.

    Default is `auto`, deliberately: a wrong fixed position is worse than AF, and
    the bench calibration that would justify a number (X17's campod equivalent) has
    not been run. Set it once the flight distance is known.
    """
    if focus in ("", "auto"):
        return "auto"
    try:
        from libcamera import controls  # noqa: PLC0415  (version-dependent)

        dioptres = 0.0 if focus == "infinity" else float(focus)
        picam2.set_controls(
            {"AfMode": controls.AfModeEnum.Manual, "LensPosition": dioptres}
        )
        distance = "infinity" if dioptres <= 0 else f"{1.0 / dioptres:.2f} m"
        print(f"capture: lens fixed at {dioptres} dioptres ({distance}), AF off", flush=True)
        return dioptres
    except Exception as exc:  # noqa: BLE001  a failed lock leaves AF running
        print(
            f"capture: WARNING could not fix focus to {focus!r}: {exc}. "
            "Continuing with autofocus.",
            flush=True,
        )
        return "auto"


def _maybe_apply_sync(picam2, mode):
    """Best-effort libcamera camera-sync configuration.

    The CM3 has no XVS hardware trigger, so multi-campod alignment uses libcamera's
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


# A campod with no camera waits for one; it does not exit. Exiting means docker's
# `restart: unless-stopped` brings us straight back, and every restart re-pays the
# picamera2 import only to rediscover the same absent camera. Measured on campod-se
# with no camera attached: 60 restarts in two hours -- one every ~2 min -- at a
# sustained load average of ~8. The Zero 2 W is quad-core, so that is roughly 2x
# oversubscribed rather than 8x, but it is 2x oversubscribed doing nothing.
#
# Worse, the entrypoint runs the accelerometer reader as a child and `exec`s us in
# the foreground, so the container's life is our life. A missing camera was
# therefore killing a working pair of ADXL345s every couple of minutes, chopping
# their record into fragments (~70 s, on the one session captured off that node).
# The entrypoint states the principle for the other half -- "a missing sensor must
# never cost us the frames" -- and this is the converse it did not cover.
#
# Waiting also makes the camera hot-pluggable, which the accelerometer reader
# already is: each probe is a fresh look, so a ribbon reseated on the bench is
# picked up without a restart. The 30 s cadence is the accelerometer retry's, for
# no stronger reason than that one operator-visible retry period beats two.
CAMERA_PROBE_INTERVAL_S = 30.0
# Log cadence while waiting, in probes. The first miss prints immediately; after
# that a heartbeat every 10th probe (5 min) keeps the journal honest about a pod
# that is up but blind, without spamming it.
CAMERA_WAIT_HEARTBEAT_PROBES = 10


def _wait_for_camera():
    """Return libcamera's camera list, blocking until one appears.

    Returns None if a stop signal arrives while waiting.

    Probing BEFORE constructing Picamera2() is load-bearing: Picamera2() on a node
    with no camera raises picamera2's own `IndexError: list index out of range`
    from inside global_camera_info() -- it indexes an empty list. Observed on
    campod-sw during bring-up, where libcamera itself initialised fine (v0.5.2)
    and the only thing in the log was that traceback, which says nothing about
    cameras. The accelerometer reader beside us already reports what it probed and
    what it expected (DEVID 0x00, expected 0xE5) per chip select; this is the
    camera half of the same courtesy. On an arm-mounted pod the likely fault is a
    badly seated ribbon, not an absent module, and an operator needs to tell those
    apart from the log alone.
    """
    cameras = Picamera2.global_camera_info()
    if cameras:
        return cameras

    print(
        "capture: libcamera reports NO cameras. Check the ribbon is seated "
        "(both ends, contacts toward the board) and that this is a campod "
        "image -- camera_auto_detect=1 comes from the vendor config. Waiting "
        f"for a camera, re-probing every {CAMERA_PROBE_INTERVAL_S:.0f}s.",
        flush=True,
    )

    probes = 0
    waited_start = time.monotonic()
    while not _stop:
        # Sleep in slices so SIGTERM stops us promptly rather than up to a full
        # probe interval later -- docker stop's grace period is 10 s.
        deadline = time.monotonic() + CAMERA_PROBE_INTERVAL_S
        while not _stop and time.monotonic() < deadline:
            time.sleep(0.25)
        if _stop:
            break

        cameras = Picamera2.global_camera_info()
        probes += 1
        if cameras:
            print(
                "capture: camera appeared after "
                f"{time.monotonic() - waited_start:.0f}s of waiting",
                flush=True,
            )
            return cameras
        if probes % CAMERA_WAIT_HEARTBEAT_PROBES == 0:
            print(
                "capture: still no camera after "
                f"{(time.monotonic() - waited_start) / 60:.0f} min",
                flush=True,
            )

    print("capture: stop requested while waiting for a camera", flush=True)
    return None


# Where the stack file mounts the host's own /etc/hostname.
HOST_HOSTNAME_PATH = Path("/etc/host-hostname")


# Baked into the image at build time; a module var so the test can redirect it.
MANIFEST_SOURCES = (
    (Path("/etc/container-image"), None),
    (Path("/etc/fleet-image"), "fleet-image"),
)


def _copy_manifests(session_dir: Path) -> None:
    """Record what produced this session, in <session>/manifests/.

    /etc/container-image is baked in at image build time and carries the commit
    the payload was built from; /etc/fleet-image is baked into the card image.
    Copying both means a capture directory says what wrote it without anyone
    having to ask docker, or correlate against a registry that may have moved on.

    Written at session start. Both containers do this and may race, so each file
    goes to a temp name and is renamed into place.

    A missing manifest is logged, not fatal: losing the capture would be a worse
    outcome than an unattributed one, and the log says which.
    """
    out = session_dir / "manifests"
    out.mkdir(parents=True, exist_ok=True)
    for src, name in MANIFEST_SOURCES:
        try:
            body = src.read_text()
        except OSError as exc:
            print(f"capture: no {src} to record ({exc}); this session is unattributed", flush=True)
            continue
        if name is None:
            m = re.search(r"^NAME=(.*)$", body, re.M)
            name = m.group(1).strip() if m else src.name
        tmp = out / f".{name}.{os.getpid()}"
        tmp.write_text(body)
        tmp.replace(out / name)


def _boot_id() -> str:
    """The kernel's boot id, which names the session."""
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _node_name():
    """The name of the HOST, not of the container.

    socket.gethostname() in a container is the container ID -- measured on
    campod-se: e2e7f038824a, while the host was campod-se -- and it changes on
    every recreate. So it cannot be the primary: it would scatter one pod's
    captures across a new directory per restart, which is worse than collecting
    them under one wrong name.

    The stack file bind-mounts the host's /etc/hostname read-only. That makes the
    host the single source of truth and is byte-identical on all four pods --
    there is no per-unit value in a shared file to get wrong, which is exactly
    what #272 was: one literal in a file describing four machines.

    gethostname stays as a last resort, and outside a container it is correct.
    """
    override = os.getenv("CAMPOD_NODE_NAME")
    if override:
        return override
    try:
        name = HOST_HOSTNAME_PATH.read_text(encoding="utf-8").strip()
        if name:
            return name
    except OSError:
        pass
    return socket.gethostname()


def main():
    node = _node_name()
    out_dir = Path(os.getenv("CAMPOD_CAPTURE_DIR", "/captures"))
    hz = _env_float("CAMPOD_CAPTURE_HZ", 1.0)
    width = _env_int("CAMPOD_CAPTURE_WIDTH", 0)
    height = _env_int("CAMPOD_CAPTURE_HEIGHT", 0)
    quality = _env_int("CAMPOD_JPEG_QUALITY", 90)
    max_exposure_us = _env_int("CAMPOD_STILL_MAX_EXPOSURE_US", 5000)
    focus = (os.getenv("CAMPOD_STILL_FOCUS") or "auto").strip().lower()
    sync_mode = (os.getenv("CAMPOD_SYNC_MODE") or "off").strip().lower()

    interval = 1.0 / hz if hz > 0 else 1.0

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    # The session is the boot id: this binary and the accel binary both use it to
    # agree on an output path (#211). No fallback, this is linux functionality.
    session = _boot_id()
    session_dir = out_dir / node / session
    session_dir.mkdir(parents=True, exist_ok=True)
    _copy_manifests(session_dir)

    cameras = _wait_for_camera()
    if cameras is None:  # SIGTERM while waiting
        return 0
    print(
        "capture: libcamera sees "
        + ", ".join(
            f"{c.get('Model', '?')} @ {c.get('Id', '?')}" for c in cameras
        ),
        flush=True,
    )

    picam2 = Picamera2()
    size = (width, height) if width and height else picam2.sensor_resolution
    config = picam2.create_still_configuration(main={"size": size})
    picam2.configure(config)
    picam2.options["quality"] = quality

    sync_active = _maybe_apply_sync(picam2, sync_mode)
    picam2.start()

    # After start(): controls are applied to the running camera, and a failed
    # control must not prevent the camera coming up at all.
    exposure_cap = _apply_exposure_cap(picam2, max_exposure_us)
    focus_active = _apply_focus(picam2, focus)

    print(
        f"capture: node={node} dir={session_dir} size={size[0]}x{size[1]} "
        f"hz={hz} quality={quality} exposure_cap={exposure_cap or 'none'} "
        f"focus={focus_active} sync={sync_active or 'off'}",
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
                "digital_gain": metadata.get("DigitalGain"),
                # What was asked for vs what the sensor did -- if the control
                # split is unsupported these disagree, and the frame says so.
                "exposure_cap_us": exposure_cap,
                "lens_position": metadata.get("LensPosition"),
                "af_state": metadata.get("AfState"),
                # Rolling-shutter window. Band pitch in rows -> Hz needs the
                # line time; see containers/campod-camera/README.md.
                "frame_duration_us": metadata.get("FrameDuration"),
                "size": [size[0], size[1]],
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
