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
    # COORD_CAPTURES_DIR is the captures ROOT; the router derives
    # <root>/<node>/<boot-id>/ itself, because only it knows the boot id. So the test
    # asserts the DERIVED path rather than dictating one -- which is also the check
    # that the router and the capture writers land in the same session directory.
    captures = os.path.join(tmpdir, "captures")
    node = (open("/etc/host-hostname").readline().strip()
            if os.path.exists("/etc/host-hostname") else socket.gethostname())
    boot = open("/proc/sys/kernel/random/boot_id").readline().strip()
    session = os.path.join(captures, node, boot)
    ts_log = os.path.join(session, "timesync.jsonl")
    tlog = os.path.join(session, "vehicle.tlog")
    fc_time = os.path.join(tmpdir, "fc_time")
    fc_link = os.path.join(tmpdir, "fc_link")

    proc = subprocess.Popen(
        [sys.executable, ROUTER, "--device", slave_name, "--baud", "115200",
         "--socket", sockpath, "--source-system", "1", "--source-component", "197"],
        stderr=subprocess.PIPE, text=True,
        env={**os.environ, "COORD_CAPTURES_DIR": captures,
             "MAVLINK_FC_TIME_FILE": fc_time, "MAVLINK_FC_LINK_FILE": fc_link,
             "MAVLINK_TIMESYNC_PERIOD_S": "0.3"},
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
        # Matched by ts1: the router sends its own TIMESYNC requests (tc1 == 0) every
        # 0.3 s here, and one can arrive ahead of the reply.
        reply = [m for m in decode(read_for(master_fd, 1.0))
                 if m.get_type() == "TIMESYNC" and m.ts1 == 12345]
        if reply and reply[0].tc1 != 0 and reply[0].ts1 == 12345:
            print(f"  TIMESYNC reply ok: ts1={reply[0].ts1}")
        else:
            print(f"  FAIL: no valid TIMESYNC reply ({[(r.tc1, r.ts1) for r in reply]})")
            ok = False

        # #167: the exchange is also written to the JSONL sink, carrying both our
        # clocks alongside the FC's ts1 -- that pairing is the point of the file.
        # Both logs must be inside the session directory, not at the captures root --
        # that is what makes `coord sessions package` collect them (#386).
        if not os.path.isdir(session):
            print(f"  FAIL: router did not create the session dir {session}")
            ok = False
        if not os.path.exists(tlog):
            print(f"  FAIL: no vehicle.tlog in the session dir ({session})")
            ok = False
        lines = [json.loads(ln) for ln in open(ts_log)] if os.path.exists(ts_log) else []
        entry = next((e for e in lines if e.get("fc_ts1_ns") == 12345), None)
        if entry and entry["monotonic_ns"] > 0 and entry["tc1_realtime_ns"] > 0:
            print(f"  TIMESYNC logged ok: {entry}")
        else:
            print(f"  FAIL: TIMESYNC not logged to {ts_log} ({lines})")
            ok = False

        # #593: the value coord-rtc reads is UTC minus our CLOCK_MONOTONIC_RAW, not minus
        # realtime -- realtime follows the chip, so an offset against it goes stale the
        # moment it is used to write the chip. Play the FC: answer the router's own
        # TIMESYNC request with tc1 = our raw clock, so FC-monotonic minus our raw is ~0,
        # then send SYSTEM_TIME. The published value must then be UTC minus FC boot time.
        utc_us, boot_ms = 1792844369_000_000, 250_000
        answered = False
        # Short reads: the router sends a request every 0.3 s and only matches a reply
        # to its latest, so the answer has to go back before the next one is sent.
        for _ in range(100):
            reqs = [m for m in decode(read_for(master_fd, 0.03))
                    if m.get_type() == "TIMESYNC" and m.tc1 == 0]
            if reqs:
                tc1 = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
                os.write(master_fd, tx.timesync_encode(tc1, reqs[-1].ts1).pack(tx))
                os.write(master_fd, tx.system_time_encode(utc_us, boot_ms).pack(tx))
                answered = True
                break
        line = ""
        for _ in range(20):
            if os.path.exists(fc_time):
                line = open(fc_time).readline()
                break
            time.sleep(0.1)
        fields = line.split()
        if not answered:
            print("  FAIL: the router sent no TIMESYNC request of its own to answer")
            ok = False
        elif len(fields) != 4:
            print(f"  FAIL: fc_time is not 'utc_minus_raw rtt raw boot_id': {line!r}")
            ok = False
        else:
            got = int(fields[0])
            want = utc_us - boot_ms * 1000
            raw_age_s = time.clock_gettime(time.CLOCK_MONOTONIC_RAW) - int(fields[2]) / 1e6
            if abs(got - want) > 200_000:
                print(f"  FAIL: fc_time utc_minus_raw {got} is not ~{want} "
                      f"(off {(got - want) / 1e3:.1f} ms)")
                ok = False
            elif not 0 <= raw_age_s < 5:
                print(f"  FAIL: fc_time's raw stamp is not this raw clock (age {raw_age_s:.3f} s)")
                ok = False
            elif fields[3] != boot:
                print(f"  FAIL: fc_time boot id {fields[3]} != {boot}")
                ok = False
            else:
                print(f"  fc_time ok: utc_minus_raw off by {(got - want) / 1e3:.1f} ms, "
                      f"raw stamp {raw_age_s:.3f} s old, this boot")

        # #669: the FC-link file. Written at startup with no heartbeat, and by the time we
        # get here nothing above has sent one, so it must still say none; then one FC
        # heartbeat must stamp it with this boot and a monotonic time of about now.
        link = json.load(open(fc_link)) if os.path.exists(fc_link) else None
        if not link or link.get("last_heartbeat_mono_s") is not None or link.get("boot_id") != boot:
            print(f"  FAIL: fc_link before any heartbeat is not this boot with none: {link}")
            ok = False
        os.write(master_fd, tx.heartbeat_encode(
            mavutil.mavlink.MAV_TYPE_QUADROTOR, mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
            0, 0, mavutil.mavlink.MAV_STATE_STANDBY).pack(tx))
        link = None
        for _ in range(20):
            time.sleep(0.1)
            link = json.load(open(fc_link))
            if link.get("heartbeats"):
                break
        age = (time.monotonic() - link["last_heartbeat_mono_s"]
               if link.get("last_heartbeat_mono_s") is not None else None)
        if link.get("heartbeats") != 1 or age is None or not 0 <= age < 5:
            print(f"  FAIL: fc_link after one FC heartbeat: {link}")
            ok = False
        else:
            print(f"  fc_link ok: one heartbeat, {age:.3f} s old")

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
