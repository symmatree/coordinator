#!/usr/bin/env python3
"""test_router_stack -- integration test for the router <-> FC seam (coordinator #35).

The router half of the batch VIO harness, driven end to end with zero hardware:

    pose datagrams -> [REAL router.py] --udpout--> MAVLink --> [fake FC (udpin)]

Spawns the *real* ``coordinator-mavlink`` router pointed at a fake FC over UDP,
replays a sequence of distinct poses (contract v2, including a mid-stream reset)
into its ``chobits_server`` socket, and asserts the FC receives
VISION_POSITION_ESTIMATE + VISION_SPEED_ESTIMATE with the correct values and
(x,-y,-z) axis flip, the forwarded reset_counter (#67), the growing position
covariance (zeroed at the reset), and velocity suppressed on the reset sample,
then that the TIMESYNC handshake completes. This exercises the two software seams
the router owns -- the pose socket byte-contract and the outgoing MAVLink --
without a wire or an FC.

    python3 test_router_stack.py            # built-in synthetic poses
    python3 test_router_stack.py FILE       # also replay a vio-pose-tap capture

Router is located via $ROUTER_PY, else the repo tree, else /opt/coordinator/router.py
(its path inside the coordinator-mavlink image, for the stack-smoke workflow).
"""

import math
import os
import socket
import subprocess
import sys
import time

os.environ.setdefault("MAVLINK20", "1")  # match the router (covariance extensions)

from fake_fc import FakeFC, check_vision_position_estimate, check_vision_speed  # noqa: E402
from pose_replayer import read_poses, send_pose  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))

# Router covariance-model defaults (router run with no --pos-* args) -- mirrored
# here to assert the growing posErr exactly. See router.py DEFAULT_POS_*.
POS_NSE_BASE = 0.30
POS_DRIFT_K = 0.02


def find_router():
    env = os.environ.get("ROUTER_PY")
    if env:
        return env
    repo = os.path.join(_HERE, "..", "containers", "coordinator-mavlink", "router.py")
    if os.path.exists(repo):
        return os.path.abspath(repo)
    return "/opt/coordinator/router.py"


# Distinct, asymmetric poses with strictly nonzero per-step position deltas so the
# dPos/dt velocity has a determinate sign on every axis (a dropped flip or a
# not-computed velocity is caught). The estimator velocity field is deliberately
# junk -- the router ignores it and derives velocity from dPos/dt (#62). The last
# pose bumps reset_counter (0 -> 1): the router must forward it, zero the growing
# covariance back to the base, and suppress the (spurious) reset-jump velocity.
# (quat w,x,y,z), (pos x,y,z), (junk vel x,y,z), reset_counter, feature_count
SYNTHETIC = [
    ((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (9.0, 9.0, 9.0), 0, 40),
    ((0.966, 0.259, 0.0, 0.0), (1.0, -1.0, 2.0), (9.0, 9.0, 9.0), 0, 38),   # d=(+1,-1,+2)
    ((0.707, 0.0, 0.707, 0.0), (3.0, 1.0, -1.0), (9.0, 9.0, 9.0), 0, 35),   # d=(+2,+2,-3)
    ((0.5, 0.5, 0.5, 0.5), (2.0, 4.0, 1.0), (9.0, 9.0, 9.0), 1, 8),         # RESET (rst 0->1)
]


# Space poses like a real camera cadence (tens of ms), not sub-millisecond bursts:
# the router skips dPos/dt below MIN_DT=1ms (duplicate-sample hygiene), which a
# back-to-back test send would trip spuriously.
POSE_SPACING_S = 0.02


def drive_one(fc, sock, sockpath, quat, pos, vel, reset, feat, dpos, is_reset, exp_pos_err):
    """Send one v2 pose; check its VISION_POSITION_ESTIMATE (position flip, the
    forwarded reset_counter, and the exact growing covariance) and its dPos/dt
    VISION_SPEED_ESTIMATE -- expected only when a previous pose exists AND this is
    not a reset sample (the reset jump is a datum change, not real motion)."""
    if dpos is not None:
        time.sleep(POSE_SPACING_S)
    send_pose(sock, sockpath, (*quat, *pos, *vel, reset, feat))
    vpe = vse = None
    want_vse = dpos is not None and not is_reset
    end = time.time() + 1.0
    while time.time() < end and (vpe is None or (want_vse and vse is None)):
        m = fc.recv(timeout=0.2)
        if m is None:
            continue
        if m.get_type() == "VISION_POSITION_ESTIMATE":
            vpe = m
        elif m.get_type() == "VISION_SPEED_ESTIMATE":
            vse = m
    errs = []
    if vpe is None:
        errs.append("no VISION_POSITION_ESTIMATE")
    else:
        errs += check_vision_position_estimate(vpe, pos, reset_counter=reset, pos_err=exp_pos_err)
    if want_vse:
        if vse is None:
            errs.append("no VISION_SPEED_ESTIMATE (dPos/dt velocity not emitted)")
        else:
            errs += check_vision_speed(vse, dpos)
    elif vse is not None:
        why = "reset sample (jump is not motion)" if is_reset else "first pose (no prior for dPos/dt)"
        errs.append(f"unexpected VISION_SPEED_ESTIMATE on {why}")
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
        prev_pos = prev_reset = None
        path_len = 0.0
        for i, (quat, pos, vel, reset, feat) in enumerate(SYNTHETIC):
            is_reset = prev_reset is not None and reset != prev_reset
            if is_reset:
                path_len = 0.0
            if prev_pos is not None and not is_reset:
                path_len += math.dist(prev_pos, pos)
            exp_pos_err = min(POS_NSE_BASE + POS_DRIFT_K * path_len, 100.0)
            dpos = None if prev_pos is None else tuple(b - a for a, b in zip(prev_pos, pos))
            errs = drive_one(fc, sock, sockpath, quat, pos, vel, reset, feat,
                             dpos, is_reset, exp_pos_err)
            if errs:
                ok = False
                print(f"  pose {i}: FAIL -- " + "; ".join(errs))
            else:
                tags = ["pos flip", f"rst={reset}", f"posErr~{exp_pos_err:.3f}"]
                if dpos is not None and not is_reset:
                    tags.append("dPos/dt vel")
                if is_reset:
                    tags.append("RESET: cov zeroed + vel suppressed")
                print(f"  pose {i}: ok ({', '.join(tags)})")
            prev_pos, prev_reset = pos, reset

        # Optional: sanity-replay a real capture -- assert it doesn't crash the router
        # and that pose/velocity keep flowing (values are motion, not fixed asserts).
        if len(sys.argv) > 1:
            path = sys.argv[1]
            samples = list(read_poses(path))
            print(f"replaying {len(samples)} captured samples from {os.path.basename(path)}")
            for _, vals in samples:
                send_pose(sock, sockpath, vals)
            got = fc.collect(1.0)
            n_vpe = len(got.get("VISION_POSITION_ESTIMATE", []))
            if n_vpe > 0:
                print(f"  captured replay: ok ({n_vpe} VISION_POSITION_ESTIMATE forwarded)")
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
