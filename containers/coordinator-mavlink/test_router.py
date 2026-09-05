#!/usr/bin/env python3
"""Isolation test for router.py -- proves the proxy in isolation (no FC needed).

Spawns router.py with its serial pointed at a pty, feeds three known v2 poses into
the unix socket (two in one reset epoch, one that bumps the reset counter), and
asserts the emitted bytes decode to VISION_POSITION_ESTIMATE + VISION_SPEED_ESTIMATE
with the right values -- the (x, -y, -z) axis flip, the dPos/dt velocity (computed
by the router, #62), the growing position covariance zeroed at the reset, the
forwarded reset_counter (#67), and velocity suppressed on the reset sample -- and
that the router replies to a TIMESYNC request and logs the exchange (#167). Run
directly (needs pymavlink):

    python3 test_router.py

It is also run at image build time so a wrong proxy fails the build.
"""
import json
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
    tmpdir = tempfile.mkdtemp()
    sockpath = os.path.join(tmpdir, "chobits_server")
    ts_log = os.path.join(tmpdir, "timesync", "timesync.jsonl")  # nested: dir is created

    proc = subprocess.Popen(
        [sys.executable, ROUTER, "--device", slave_name, "--baud", "115200",
         "--socket", sockpath, "--source-system", "1", "--source-component", "197"],
        stderr=subprocess.PIPE, text=True,
        env={**os.environ, "COORD_TIMESYNC_LOG": ts_log},
    )
    try:
        for _ in range(100):
            if os.path.exists(sockpath):
                break
            time.sleep(0.05)
        else:
            print("FAIL: router never bound the socket")
            return 1

        # Three poses: p1,p2 share a reset epoch; p3 bumps reset_counter. The router
        # derives velocity from dPos/dt (p1 emits none), grows the position
        # covariance with distance travelled, and on the reset forwards the new
        # counter, zeroes the covariance to base, and suppresses the jump velocity.
        # The estimator velocity field is nonzero to prove it is IGNORED (zero in
        # stereo-only anyway); feature_count is carried (v2) but unused by default.
        usock_tx = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        dt = 0.2
        base, drift_k = 0.30, 0.02  # router DEFAULT_POS_NSE_BASE / DEFAULT_POS_DRIFT_K
        p1 = (7.0, 2.0, 3.0)
        p2 = (7.2, 2.4, 2.4)  # dPos = (+0.2, +0.4, -0.6) -> raw vel (1.0, 2.0, -3.0)
        p3 = (9.0, 9.0, 9.0)  # datum jump at the reset -- distance NOT counted
        exp_v = tuple((b - a) / dt for a, b in zip(p1, p2))
        exp_err_p2 = base + drift_k * math.dist(p1, p2)  # covariance grew this far

        def pk(pos, rst, feat):  # v2 datagram: <12f> quat, pos, junk-vel, rst, feat
            return struct.pack("<12f", 1.0, 0, 0, 0, *pos, 9.0, 9.0, 9.0, rst, feat)

        usock_tx.sendto(pk(p1, 0, 40), sockpath)
        time.sleep(dt)
        usock_tx.sendto(pk(p2, 0, 38), sockpath)
        time.sleep(dt)
        usock_tx.sendto(pk(p3, 1, 6), sockpath)

        msgs = decode(read_for(master_fd, 1.0))
        vpes = [m for m in msgs if m.get_type() == "VISION_POSITION_ESTIMATE"]
        vses = [m for m in msgs if m.get_type() == "VISION_SPEED_ESTIMATE"]
        print("emitted:", sorted({m.get_type() for m in msgs}), f"({len(vpes)} VPE, {len(vses)} VSE)")
        ok = True

        def fc_scalar(m, idx):
            return math.sqrt(m.covariance[idx[0]] + m.covariance[idx[1]] + m.covariance[idx[2]])

        if len(vpes) < 3:
            print(f"FAIL: expected 3 VISION_POSITION_ESTIMATE, got {len(vpes)}")
            ok = False
        else:
            vp1, vp2, vp3 = vpes[0], vpes[1], vpes[2]
            perr = [fc_scalar(m, (0, 6, 11)) for m in (vp1, vp2, vp3)]
            print(f"  VPE posErr p1={perr[0]:.3f} p2={perr[1]:.3f} p3={perr[2]:.3f}; "
                  f"reset_counter p2={vp2.reset_counter} p3={vp3.reset_counter}")
            if not (abs(vp2.x - p2[0]) < 1e-4 and abs(vp2.y + p2[1]) < 1e-4 and abs(vp2.z + p2[2]) < 1e-4):
                print(f"  FAIL: p2 position axis flip wrong (expected {p2[0]},{-p2[1]},{-p2[2]})")
                ok = False
            if abs(perr[0] - base) > 1e-3:
                print(f"  FAIL: p1 posErr {perr[0]:.4f} != base {base} (fresh anchor)")
                ok = False
            if abs(perr[1] - exp_err_p2) > 1e-3:
                print(f"  FAIL: p2 posErr {perr[1]:.4f} != grown {exp_err_p2:.4f}")
                ok = False
            if vp3.reset_counter != 1:
                print(f"  FAIL: p3 reset_counter {vp3.reset_counter} != 1 (not forwarded)")
                ok = False
            if abs(perr[2] - base) > 1e-3:
                print(f"  FAIL: p3 posErr {perr[2]:.4f} != base {base} (not zeroed on reset)")
                ok = False

        # Exactly one VSE: p2's dPos/dt (p1 has no prior; p3 is a reset -> suppressed).
        if len(vses) != 1:
            print(f"FAIL: expected exactly 1 VISION_SPEED_ESTIMATE (p2 only), got {len(vses)}")
            ok = False
        else:
            vse = vses[0]
            ex, ey, ez = exp_v[0], -exp_v[1], -exp_v[2]  # dPos/dt with the (x,-y,-z) flip
            fc_err = fc_scalar(vse, (0, 4, 8))
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

        # #167: the exchange is also written to the JSONL sink, carrying both our
        # clocks alongside the FC's ts1 -- that pairing is the point of the file.
        lines = [json.loads(ln) for ln in open(ts_log)] if os.path.exists(ts_log) else []
        entry = next((e for e in lines if e.get("fc_ts1_ns") == 12345), None)
        if entry and entry["monotonic_ns"] > 0 and entry["tc1_realtime_ns"] > 0:
            print(f"  TIMESYNC logged ok: {entry}")
        else:
            print(f"  FAIL: TIMESYNC not logged to {ts_log} ({lines})")
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
