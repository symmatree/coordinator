#!/usr/bin/env python3
"""test_router_telem -- the router's coordinator-side tlog.

No serial port, no FC: build real MAVLink frames, run them through `log_frame`, then read
the file back with pymavlink and assert it round-trips.

The round-trip is the point. An earlier version of this logged a hand-picked projection of
fields, which is smaller but encodes today's guess about what matters and fails silently
when a field is renamed upstream. Writing raw frames means the test that matters is "can a
standard tool read this back and get the same messages", not "did we copy the right keys".

Run: python3 harness/test_router_telem.py   (exit 0 = pass)
"""

import os
import struct
import sys
import tempfile

from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as mavlink

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "containers", "coordinator-mavlink"))

import router  # noqa: E402


def _frames():
    """A few real encoded messages, as they would arrive off the wire."""
    mav = mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
    out = []
    m = mav.system_time_encode(1786712345000000, 4242); m.pack(mav); out.append(m)
    m = mav.heartbeat_encode(mavutil.mavlink.MAV_TYPE_QUADROTOR,
                             mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                             81, 5, 4); m.pack(mav); out.append(m)
    m = mav.vibration_encode(0, 1.5, 2.5, 3.5, 0, 0, 0); m.pack(mav); out.append(m)
    return out


def test_frames_round_trip_through_pymavlink():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "sub", "vehicle.tlog")     # nested: dir must be created
        fh = router.open_tlog(path)
        for i, m in enumerate(_frames()):
            router.log_frame(fh, m, 1786712345000000 + i)
        fh.close()

        conn = mavutil.mavlink_connection(path)
        got = []
        while True:
            m = conn.recv_match(blocking=False)
            if m is None:
                break
            got.append(m)
        types = [m.get_type() for m in got]
        assert types == ["SYSTEM_TIME", "HEARTBEAT", "VIBRATION"], types
        # and the fields survived, including ones no whitelist would have thought to keep
        assert got[0].time_unix_usec == 1786712345000000
        assert got[1].custom_mode == 5
        assert abs(got[2].vibration_z - 3.5) < 1e-6


def test_timestamp_is_standard_tlog_format():
    """8-byte big-endian microseconds, then the frame -- what every tlog reader expects."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "vehicle.tlog")
        fh = router.open_tlog(path)
        router.log_frame(fh, _frames()[0], 1786712345000000)
        fh.close()
        raw = open(path, "rb").read()
        (stamp,) = struct.unpack(">Q", raw[:8])
        assert stamp == 1786712345000000
        assert raw[8] in (0xFD, 0xFE), "frame should start with a MAVLink magic byte"


def test_unpacked_message_is_skipped_not_crashed():
    """A message with no wire buffer must be ignored rather than raising in the drain loop."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "vehicle.tlog")
        fh = router.open_tlog(path)
        mav = mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
        router.log_frame(fh, mav.system_time_encode(1, 2), 1)   # never .pack()ed
        fh.close()
        assert os.path.getsize(path) == 0


def main():
    import traceback
    failed = []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("  ok   %s" % name)
        except Exception:
            failed.append(name)
            print("  FAIL %s" % name)
            traceback.print_exc()
    print("RESULT: %s" % ("PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
