#!/usr/bin/env python3
"""Isolation test for router.py -- proves the proxy in isolation (no FC needed).

Spawns router.py with its serial pointed at a pty, feeds two known poses into the
unix socket, and asserts the emitted bytes decode to ATT_POS_MOCAP +
VISION_SPEED_ESTIMATE with the right values -- the (x, -y, -z) axis flip, the
dPos/dt velocity (computed by the router, #62), and the honest covariances -- and
that the router replies to a TIMESYNC request. Run directly (needs pymavlink):

    python3 test_router.py

It is also run at image build time so a wrong proxy fails the build.
"""
import math
import os
import pty
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time

# Match the router: v2.0 dialect exposes the covariance/reset_counter extensions
# so we can decode and assert them. Must precede the pymavlink import.
os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil  # noqa: E402

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

        # Two poses a known interval apart: the router derives velocity from the
        # position delta / dt, so a single pose emits no VISION_SPEED_ESTIMATE.
        # Estimator velocity field is set nonzero to prove it is IGNORED (zero in
        # stereo-only anyway) -- the reported velocity must be dPos/dt, not it.
        usock_tx = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        dt = 0.2
        p1 = (7.0, 2.0, 3.0)
        p2 = (7.2, 2.4, 2.4)  # dPos = (+0.2, +0.4, -0.6) -> raw vel (1.0, 2.0, -3.0)
        exp_v = tuple((b - a) / dt for a, b in zip(p1, p2))
        usock_tx.sendto(struct.pack("<10f", 1.0, 0, 0, 0, *p1, 9.0, 9.0, 9.0), sockpath)
        time.sleep(dt)
        usock_tx.sendto(struct.pack("<10f", 1.0, 0, 0, 0, *p2, 9.0, 9.0, 9.0), sockpath)

        msgs = decode(read_for(master_fd, 1.0))
        apms = [m for m in msgs if m.get_type() == "ATT_POS_MOCAP"]
        vses = [m for m in msgs if m.get_type() == "VISION_SPEED_ESTIMATE"]
        print("emitted:", sorted({m.get_type() for m in msgs}), f"({len(apms)} APM, {len(vses)} VSE)")
        ok = True

        if not apms:
            print("FAIL: no ATT_POS_MOCAP")
            ok = False
        else:
            apm = apms[-1]  # last pose = p2
            print(f"  ATT_POS_MOCAP q={list(apm.q)} pos=({apm.x},{apm.y},{apm.z}) cov0={apm.covariance[0]}")
            if not (abs(apm.x - p2[0]) < 1e-4 and abs(apm.y + p2[1]) < 1e-4 and abs(apm.z + p2[2]) < 1e-4):
                print(f"  FAIL: position axis flip wrong (expected {p2[0]},{-p2[1]},{-p2[2]})")
                ok = False
            if not (abs(apm.q[0] - 1.0) < 1e-4 and abs(apm.q[1]) < 1e-4):
                print("  FAIL: quaternion not passed through")
                ok = False
            if not abs(apm.covariance[0] - 0.30**2) < 1e-4:
                print(f"  FAIL: position covariance {apm.covariance[0]} != 0.30^2")
                ok = False

        if not vses:
            print("FAIL: no VISION_SPEED_ESTIMATE (dPos/dt velocity not emitted)")
            ok = False
        else:
            vse = vses[-1]
            # Expected sent velocity: raw dPos/dt with the (x,-y,-z) flip.
            ex, ey, ez = exp_v[0], -exp_v[1], -exp_v[2]
            fc_err = math.sqrt(vse.covariance[0] + vse.covariance[4] + vse.covariance[8])
            print(f"  VISION_SPEED_ESTIMATE vel=({vse.x:.3f},{vse.y:.3f},{vse.z:.3f}) "
                  f"expected~({ex:.3f},{ey:.3f},{ez:.3f}) fc_err={fc_err:.3f}")
            # 20% band absorbs dt jitter (router times receipt with monotonic()).
            if not all(abs(g - e) < 0.2 * abs(e) + 1e-3 for g, e in ((vse.x, ex), (vse.y, ey), (vse.z, ez))):
                print("  FAIL: velocity != dPos/dt with (x,-y,-z) flip")
                ok = False
            if not abs(fc_err - 0.15) < 1e-3:
                print(f"  FAIL: velocity covariance FC-scalar {fc_err} != 0.15")
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
