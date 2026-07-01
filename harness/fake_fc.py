#!/usr/bin/env python3
"""fake_fc -- a fake flight controller for the VIO harness (coordinator #35).

Stands in for the real FC at the far end of the router. The router already speaks
``udpout:`` (``mavutil.mavlink_connection`` dispatches on the device string), so no
serial or pty is needed -- the fake FC is just a MAVLink UDP endpoint (``udpin:``).

It plays the FC's side of the visual-odometry link:
  * receives ATT_POS_MOCAP / VISION_SPEED_ESTIMATE and exposes them for assertion,
  * runs the TIMESYNC handshake as the *initiator* (FC sends tc1==0, expects a
    reply with tc1!=0 and ts1 echoed) -- the router replies to exactly this,
  * emits HEARTBEAT so a real router/FC would consider the link alive.

NOTE: a ``udpin`` endpoint can only transmit once it has received a packet (that
is how it learns the peer's ephemeral address). So drive at least one pose through
the router before calling ``heartbeat()`` / ``request_timesync()``.

Run standalone to watch a live/replayed stream (Ctrl-C to stop):

    python3 fake_fc.py --port 14550
"""

import argparse
import sys
import time

from pymavlink import mavutil


class FakeFC:
    def __init__(self, port=14550, host="127.0.0.1", source_system=1, source_component=1):
        self.conn = mavutil.mavlink_connection(
            f"udpin:{host}:{port}",
            dialect="ardupilotmega",
            source_system=source_system,
            source_component=source_component,
        )

    def fileno(self):
        return self.conn.port.fileno()

    def recv(self, timeout=0.2):
        """Return the next decoded message within timeout, or None."""
        return self.conn.recv_match(blocking=True, timeout=timeout)

    def collect(self, seconds):
        """Drain messages for a window; return dict type -> list[msg]."""
        out = {}
        end = time.time() + seconds
        while time.time() < end:
            m = self.recv(timeout=0.1)
            if m is not None:
                out.setdefault(m.get_type(), []).append(m)
        return out

    def heartbeat(self):
        self.conn.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0
        )

    def request_timesync(self, ts1=None, timeout=2.0):
        """Send a TIMESYNC request (tc1=0) and return the router's reply, or None."""
        if ts1 is None:
            ts1 = int(time.time() * 1e9)
        self.conn.mav.timesync_send(0, ts1)
        end = time.time() + timeout
        while time.time() < end:
            m = self.conn.recv_match(type="TIMESYNC", blocking=True, timeout=0.2)
            if m is not None and m.tc1 != 0 and m.ts1 == ts1:
                return m
        return None

    def close(self):
        self.conn.close()


AXIS_FLIP_TOL = 1e-4


def check_att_pos_mocap(msg, q, pos):
    """Verify ATT_POS_MOCAP passes the quaternion and applies the (x,-y,-z) flip."""
    ex, ey, ez = pos[0], -pos[1], -pos[2]
    errs = []
    if not (abs(msg.x - ex) < AXIS_FLIP_TOL and abs(msg.y - ey) < AXIS_FLIP_TOL
            and abs(msg.z - ez) < AXIS_FLIP_TOL):
        errs.append(f"position ({msg.x},{msg.y},{msg.z}) != expected ({ex},{ey},{ez})")
    if not all(abs(a - b) < AXIS_FLIP_TOL for a, b in zip(msg.q, q)):
        errs.append(f"quaternion {list(msg.q)} != expected {list(q)}")
    return errs


def check_vision_speed(msg, vel):
    """Verify VISION_SPEED_ESTIMATE applies the (x,-y,-z) flip."""
    ex, ey, ez = vel[0], -vel[1], -vel[2]
    if not (abs(msg.x - ex) < AXIS_FLIP_TOL and abs(msg.y - ey) < AXIS_FLIP_TOL
            and abs(msg.z - ez) < AXIS_FLIP_TOL):
        return [f"velocity ({msg.x},{msg.y},{msg.z}) != expected ({ex},{ey},{ez})"]
    return []


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=14550, help="UDP port to listen on (default 14550)")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    # Line-buffer stdout so received messages appear live under a pipe/redirect
    # (block buffering otherwise hides them until exit -- see container entrypoints).
    sys.stdout.reconfigure(line_buffering=True)

    fc = FakeFC(port=args.port, host=args.host)
    print(f"fake_fc: listening on udpin:{args.host}:{args.port}", file=sys.stderr)
    seen = 0
    try:
        while True:
            m = fc.recv(timeout=0.5)
            if m is None:
                continue
            t = m.get_type()
            if t == "ATT_POS_MOCAP":
                print(f"ATT_POS_MOCAP q={list(m.q)} pos=({m.x:+.3f},{m.y:+.3f},{m.z:+.3f})")
            elif t == "VISION_SPEED_ESTIMATE":
                print(f"VISION_SPEED_ESTIMATE vel=({m.x:+.3f},{m.y:+.3f},{m.z:+.3f})")
            elif t == "TIMESYNC":
                print(f"TIMESYNC tc1={m.tc1} ts1={m.ts1}")
            else:
                continue
            seen += 1
    except KeyboardInterrupt:
        print(f"\nfake_fc: {seen} messages", file=sys.stderr)
    finally:
        fc.close()


if __name__ == "__main__":
    main()
