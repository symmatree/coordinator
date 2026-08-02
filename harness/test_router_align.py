#!/usr/bin/env python3
"""test_router_align -- the router's VisOdom yaw re-align kick (#175).

Spawns the *real* coordinator-mavlink router against a fake FC over UDP and asserts:
  * before any heartbeat (arm state unknown) the router does NOT kick;
  * while DISARMED it periodically sends MAV_CMD_DO_AUX_FUNCTION for VISODOM_ALIGN
    (aux 80) at switch HIGH -- the re-align kick that clears the "VisOdom: yaw diff
    >10" pre-arm gate;
  * once the FC reports ARMED the kicks stop (never re-align in flight -- it would
    snap the VIO frame).

Zero hardware: a single pose datagram bootstraps the router->FC UDP address, then the
fake FC drives heartbeats and watches the returned command stream.

    python3 test_router_align.py

Router located via $ROUTER_PY, else the repo tree, else /opt/coordinator/router.py
(its path inside the coordinator-mavlink image, for the stack-smoke workflow).
"""

import os
import socket
import subprocess
import sys
import time

os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil  # noqa: E402
from pose_replayer import send_pose  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
DO_AUX = mavutil.mavlink.MAV_CMD_DO_AUX_FUNCTION
VISODOM_ALIGN = 80  # ArduPilot RC_Channel AUX_FUNC::VISODOM_ALIGN
SWITCH_HIGH = 2

# The router polls its align timer once per select() cycle; select's 1 s timeout
# dominates the short --align-interval here, so kicks land at ~1 s cadence. Size the
# windows to that, not to the interval.
DISARMED_WINDOW = 2.5   # expect >=1 kick
ARMED_WINDOW = 2.5      # expect 0
PRE_HB_WINDOW = 1.3     # > one select cycle; expect 0 (gated on seen_heartbeat)


def find_router():
    env = os.environ.get("ROUTER_PY")
    if env:
        return env
    repo = os.path.join(_HERE, "..", "containers", "coordinator-mavlink", "router.py")
    return os.path.abspath(repo) if os.path.exists(repo) else "/opt/coordinator/router.py"


def is_align_kick(m):
    return (m is not None and m.get_type() == "COMMAND_LONG" and m.command == DO_AUX
            and int(m.param1) == VISODOM_ALIGN and int(m.param2) == SWITCH_HIGH)


def send_heartbeat(fc, armed):
    base = mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED if armed else 0
    fc.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_QUADROTOR,
        mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA, base, 0,
        mavutil.mavlink.MAV_STATE_STANDBY)


def count_kicks(fc, seconds):
    end = time.time() + seconds
    n = 0
    while time.time() < end:
        if is_align_kick(fc.recv_match(blocking=True, timeout=0.2)):
            n += 1
    return n


def main():
    router = find_router()
    if not os.path.exists(router):
        print(f"FAIL: router not found at {router}")
        return 1

    port = 14578
    sockdir = os.path.join(_HERE, ".smoke", "align")  # under the git-ignored .smoke/
    os.makedirs(sockdir, exist_ok=True)
    sockpath = os.path.join(sockdir, "chobits_server")
    try:
        os.unlink(sockpath)
    except FileNotFoundError:
        pass

    fc = mavutil.mavlink_connection(
        f"udpin:127.0.0.1:{port}", source_system=1, source_component=1)
    proc = subprocess.Popen(
        [sys.executable, router, "--device", f"udpout:127.0.0.1:{port}",
         "--socket", sockpath, "--source-system", "1", "--source-component", "197",
         "--align-interval", "0.2"],
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

        usock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

        # Bootstrap: one pose -> router emits VISION_POSITION_ESTIMATE, so our udpin
        # learns the router's source address and can send heartbeats back to it.
        send_pose(usock, sockpath, (1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 40))
        end = time.time() + 2.0
        while time.time() < end:
            m = fc.recv_match(blocking=True, timeout=0.2)
            if m is not None and m.get_type() == "VISION_POSITION_ESTIMATE":
                break
        else:
            print("FAIL: no VISION_POSITION_ESTIMATE to bootstrap FC->router address")
            return 1

        # Arm state unknown (no heartbeat yet) -> the router must not kick.
        pre = count_kicks(fc, PRE_HB_WINDOW)
        if pre:
            ok = False
            print(f"FAIL: {pre} align kick(s) before any heartbeat (expected 0)")
        else:
            print("ok: no kicks before arm state is known")

        # DISARMED -> kicks should start.
        send_heartbeat(fc, armed=False)
        disarmed = count_kicks(fc, DISARMED_WINDOW)
        if disarmed >= 1:
            print(f"ok: {disarmed} VISODOM_ALIGN kick(s) while disarmed")
        else:
            ok = False
            print("FAIL: no VISODOM_ALIGN kick while disarmed")

        # ARMED -> kicks must stop.
        send_heartbeat(fc, armed=True)
        time.sleep(0.3)  # let the router process the heartbeat
        while fc.recv_match(blocking=False) is not None:
            pass  # drain anything already queued
        armed = count_kicks(fc, ARMED_WINDOW)
        if armed == 0:
            print("ok: no kicks while armed")
        else:
            ok = False
            print(f"FAIL: {armed} align kick(s) while armed (must be 0)")

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
