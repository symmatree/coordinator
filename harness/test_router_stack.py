#!/usr/bin/env python3
"""test_router_stack -- integration test for the router <-> FC seam (coordinator #35).

The router half of the batch VIO harness, driven end to end with zero hardware:

    pose datagrams -> [REAL router.py] --udpout--> MAVLink --> [fake FC (udpin)]

Spawns the *real* ``coordinator-mavlink`` router pointed at a fake FC over UDP,
replays a sequence of distinct poses into its ``chobits_server`` socket, and
asserts the FC receives ATT_POS_MOCAP + VISION_SPEED_ESTIMATE with the correct
values and (x,-y,-z) axis flip for every pose, then that the TIMESYNC handshake
completes. This exercises the two software seams the router owns -- the pose
socket byte-contract and the outgoing MAVLink -- without a wire or an FC.

    python3 test_router_stack.py            # built-in synthetic poses
    python3 test_router_stack.py FILE       # also replay a vio-pose-tap capture

Router is located via $ROUTER_PY, else the repo tree, else /opt/coordinator/router.py
(its path inside the coordinator-mavlink image, for the stack-smoke workflow).
"""

import os
import socket
import subprocess
import sys
import time

from fake_fc import FakeFC, check_att_pos_mocap, check_vision_speed
from pose_replayer import read_poses, send_pose

_HERE = os.path.dirname(os.path.abspath(__file__))


def find_router():
    env = os.environ.get("ROUTER_PY")
    if env:
        return env
    repo = os.path.join(_HERE, "..", "containers", "coordinator-mavlink", "router.py")
    if os.path.exists(repo):
        return os.path.abspath(repo)
    return "/opt/coordinator/router.py"


# Distinct, asymmetric poses: a wrong axis, dropped flip, or swapped field is caught.
# (quat w,x,y,z), (pos x,y,z), (vel x,y,z)
SYNTHETIC = [
    ((1.0, 0.0, 0.0, 0.0), (7.0, 2.0, 3.0), (0.5, 0.6, 0.7)),
    ((0.966, 0.259, 0.0, 0.0), (-1.5, 4.0, -2.5), (-0.3, 0.2, -0.9)),
    ((0.707, 0.0, 0.707, 0.0), (10.0, -8.0, 0.5), (1.1, -1.2, 1.3)),
    ((0.5, 0.5, 0.5, 0.5), (0.0, 0.0, -12.0), (-0.01, 0.0, 0.04)),
]


def drive_one(fc, sock, sockpath, quat, pos, vel):
    """Send one pose; wait for its ATT_POS_MOCAP + VISION_SPEED_ESTIMATE; check them."""
    send_pose(sock, sockpath, (*quat, *pos, *vel))
    apm = vse = None
    end = time.time() + 1.0
    while time.time() < end and (apm is None or vse is None):
        m = fc.recv(timeout=0.2)
        if m is None:
            continue
        if m.get_type() == "ATT_POS_MOCAP":
            apm = m
        elif m.get_type() == "VISION_SPEED_ESTIMATE":
            vse = m
    errs = []
    if apm is None:
        errs.append("no ATT_POS_MOCAP")
    else:
        errs += check_att_pos_mocap(apm, quat, pos)
    if vse is None:
        errs.append("no VISION_SPEED_ESTIMATE")
    else:
        errs += check_vision_speed(vse, vel)
    return errs


def main():
    router = find_router()
    if not os.path.exists(router):
        print(f"FAIL: router not found at {router}")
        return 1

    port = 14577
    sockdir = os.path.join(_HERE, ".smoke")
    os.makedirs(sockdir, exist_ok=True)
    sockpath = os.path.join(sockdir, "chobits_server")

    # Clear any socket left by a previous run: otherwise os.path.exists() below
    # sees the stale node and we start sending before the new router has re-bound.
    try:
        os.unlink(sockpath)
    except FileNotFoundError:
        pass

    fc = FakeFC(port=port)
    proc = subprocess.Popen(
        [sys.executable, router, "--device", f"udpout:127.0.0.1:{port}",
         "--socket", sockpath, "--source-system", "1", "--source-component", "197"],
        stderr=subprocess.PIPE, text=True,
    )
    ok = True
    try:
        for _ in range(100):
            if os.path.exists(sockpath):
                break
            time.sleep(0.05)
        else:
            print("FAIL: router never bound the socket")
            return 1

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

        print(f"driving {len(SYNTHETIC)} synthetic poses through {os.path.basename(router)}")
        for i, (quat, pos, vel) in enumerate(SYNTHETIC):
            errs = drive_one(fc, sock, sockpath, quat, pos, vel)
            if errs:
                ok = False
                print(f"  pose {i}: FAIL -- " + "; ".join(errs))
            else:
                print(f"  pose {i}: ok (pos flip + quaternion + velocity flip)")

        # Optional: sanity-replay a real capture -- assert it doesn't crash the router
        # and that pose/velocity keep flowing (values are motion, not fixed asserts).
        if len(sys.argv) > 1:
            path = sys.argv[1]
            samples = list(read_poses(path))
            print(f"replaying {len(samples)} captured samples from {os.path.basename(path)}")
            for _, vals in samples:
                send_pose(sock, sockpath, vals)
            got = fc.collect(1.0)
            n_apm = len(got.get("ATT_POS_MOCAP", []))
            if n_apm > 0:
                print(f"  captured replay: ok ({n_apm} ATT_POS_MOCAP forwarded)")
            else:
                ok = False
                print("  captured replay: FAIL -- router forwarded nothing")

        # TIMESYNC handshake: FC initiates (tc1=0), router must reply (tc1!=0, ts1 echoed).
        reply = fc.request_timesync(ts1=424242)
        if reply is not None:
            print(f"  timesync: ok (reply tc1={reply.tc1}, ts1={reply.ts1})")
        else:
            ok = False
            print("  timesync: FAIL -- no valid reply")

        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        fc.close()
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
