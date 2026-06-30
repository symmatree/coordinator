#!/usr/bin/env python3
"""Isolation test for router.py -- proves the proxy in isolation (no FC needed).

Spawns router.py with its serial pointed at a pty, feeds a known pose into the
unix socket, and asserts the emitted bytes decode to ATT_POS_MOCAP +
VISION_SPEED_ESTIMATE with the right values and the (x, -y, -z) axis flip, and
that the router replies to a TIMESYNC request. Run directly (needs pymavlink):

    python3 test_router.py

It is also run at image build time so a wrong proxy fails the build.
"""
import os
import pty
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time

from pymavlink import mavutil

ROUTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "router.py")


def read_for(fd, seconds):
    buf = b""
    end = time.time() + seconds
    while time.time() < end:
        if select.select([fd], [], [], 0.1)[0]:
            chunk = os.read(fd, 4096)
            if chunk:
                buf += chunk
    return buf


def decode(data):
    mav = mavutil.mavlink.MAVLink(None)
    mav.robust_parsing = True
    return mav.parse_buffer(data) or []


def main():
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    sockpath = os.path.join(tempfile.mkdtemp(), "chobits_server")

    proc = subprocess.Popen(
        [sys.executable, ROUTER, "--device", slave_name, "--baud", "115200",
         "--socket", sockpath, "--source-system", "1", "--source-component", "197"],
        stderr=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(100):
            if os.path.exists(sockpath):
                break
            time.sleep(0.05)
        else:
            print("FAIL: router never bound the socket")
            return 1

        pose = struct.pack("<10f", 1.0, 0.0, 0.0, 0.0, 7.0, 2.0, 3.0, 0.5, 0.6, 0.7)
        socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM).sendto(pose, sockpath)

        by_type = {m.get_type(): m for m in decode(read_for(master_fd, 1.0))}
        print("emitted:", sorted(by_type))
        ok = True

        apm = by_type.get("ATT_POS_MOCAP")
        if apm is None:
            print("FAIL: no ATT_POS_MOCAP")
            ok = False
        else:
            print(f"  ATT_POS_MOCAP q={list(apm.q)} pos=({apm.x},{apm.y},{apm.z})")
            if not (abs(apm.x - 7.0) < 1e-4 and abs(apm.y + 2.0) < 1e-4 and abs(apm.z + 3.0) < 1e-4):
                print("  FAIL: position axis flip wrong (expected 7,-2,-3)")
                ok = False
            if not (abs(apm.q[0] - 1.0) < 1e-4 and abs(apm.q[1]) < 1e-4):
                print("  FAIL: quaternion not passed through")
                ok = False

        vse = by_type.get("VISION_SPEED_ESTIMATE")
        if vse is None:
            print("FAIL: no VISION_SPEED_ESTIMATE")
            ok = False
        else:
            print(f"  VISION_SPEED_ESTIMATE vel=({vse.x},{vse.y},{vse.z})")
            if not (abs(vse.x - 0.5) < 1e-4 and abs(vse.y + 0.6) < 1e-4 and abs(vse.z + 0.7) < 1e-4):
                print("  FAIL: velocity axis flip wrong (expected 0.5,-0.6,-0.7)")
                ok = False

        tx = mavutil.mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
        os.write(master_fd, tx.timesync_encode(0, 12345).pack(tx))
        reply = [m for m in decode(read_for(master_fd, 1.0)) if m.get_type() == "TIMESYNC"]
        if reply and reply[0].tc1 != 0 and reply[0].ts1 == 12345:
            print(f"  TIMESYNC reply ok: ts1={reply[0].ts1}")
        else:
            print(f"  FAIL: no valid TIMESYNC reply ({[(r.tc1, r.ts1) for r in reply]})")
            ok = False

        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
