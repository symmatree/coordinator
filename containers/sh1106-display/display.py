#!/usr/bin/env python3
"""Coordinator front-panel status display on the SH1106 OLED (coordinator #115).

Drives the SH1106 (128x64) on the Pi's I2C (0x3C, /dev/i2c-1) with a compact status
screen. This first cut shows the coordinator's own *observable* state -- node, UTC
clock, OAK-D id, and capture activity -- with no dependency on data the coordinator
does not yet expose. Layout is modeled on ArduPilot's onboard OLED; the richer
FC/VIO fields (flight mode, GPS, battery via the MAVLink router) and the alternating
screens are the documented follow-up in #115.

The render path (`draw_status`) is pure and hardware-free, unit-tested in
`test_display.py` (which the Docker build runs), so a broken layout fails the build.
The luma/I2C device is only touched in `main`.
"""

import datetime as dt
import os
import signal
import socket
import time
from pathlib import Path

_stop = False


def _request_stop(signum, _frame):
    global _stop
    _stop = True


def _env(name, default):
    v = os.getenv(name)
    return v if v not in (None, "") else default


def gather_status(captures_dir, node):
    """Collect the coordinator's observable state (no hardware, no FC). Returns a dict.

    Capture activity is inferred from the newest file under `captures_dir`
    (`captures/<oak-mxid>/<session>/...`, #32): a fresh file means the tracker is
    actively writing, i.e. recording.
    """
    now = dt.datetime.now(dt.timezone.utc)
    status = {"node": node, "time": now.strftime("%H:%M:%SZ"),
              "cam": None, "capturing": False, "cap_age": None}
    root = Path(captures_dir)
    newest_mtime, newest_cam = None, None
    if root.is_dir():
        for cam in root.iterdir():
            if not cam.is_dir():
                continue
            files = [f for f in cam.rglob("*") if f.is_file()]
            if not files:
                continue
            f = max(files, key=lambda p: p.stat().st_mtime)
            m = f.stat().st_mtime
            if newest_mtime is None or m > newest_mtime:
                newest_mtime, newest_cam = m, cam.name
    if newest_mtime is not None:
        age = max(0.0, time.time() - newest_mtime)
        status["cam"] = newest_cam
        status["cap_age"] = age
        status["capturing"] = age < 5.0
    return status


def draw_status(draw, width, height, status):
    """Render the status screen onto a PIL ImageDraw. Pure -- no hardware, no I/O."""
    draw.rectangle((0, 0, width - 1, height - 1), outline="white")
    draw.text((4, 1), "COORDINATOR", fill="white")
    draw.text((4, 15), f"{status['node'][:10]:<10} {status['time']}", fill="white")
    draw.text((4, 30), f"cam {(status['cam'] or '--')[:14]}", fill="white")
    if status["capturing"]:
        cap = "REC"
    elif status["cap_age"] is not None:
        cap = f"idle {int(status['cap_age'])}s"
    else:
        cap = "idle"
    draw.text((4, 45), f"cap {cap}", fill="white")


def main():
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    node = _env("SH1106_NODE", socket.gethostname())
    port = int(_env("SH1106_I2C_PORT", "1"))
    addr = int(_env("SH1106_I2C_ADDR", "0x3C"), 0)
    captures = _env("SH1106_CAPTURES_DIR", "/captures")
    refresh = float(_env("SH1106_REFRESH_SEC", "1.0"))

    # Imported here so the render path (draw_status) stays testable without luma/I2C.
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import sh1106

    device = sh1106(i2c(port=port, address=addr))
    device.persist = True  # leave the last frame up on SIGTERM instead of blanking
    print(f"sh1106-display: node={node} i2c={port}@{hex(addr)} refresh={refresh}s", flush=True)

    while not _stop:
        status = gather_status(captures, node)
        with canvas(device) as draw:
            draw_status(draw, device.width, device.height, status)
        end = time.monotonic() + refresh
        while not _stop and time.monotonic() < end:
            time.sleep(min(0.2, max(0.0, end - time.monotonic())))

    print("sh1106-display: stopped", flush=True)


if __name__ == "__main__":
    main()
