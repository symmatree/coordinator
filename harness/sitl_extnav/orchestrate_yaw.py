#!/usr/bin/env python3
"""Does the FC yaw-align an ExtNav frame -- VISO_TYPE=1 (MAV) vs 2 (IntelT265)?

Stereo-only VINS has no heading reference, so its world-frame yaw is arbitrary.
The MAV backend passes the pose through (position-translation-anchored only); the
IntelT265 backend auto-aligns the sensor's reported yaw to the vehicle AHRS on the
first pose (_align_yaw defaults true) and rotates all subsequent position into the
vehicle frame. Both eat the same VISION_POSITION_ESTIMATE MAVLink.

Test: feed an identical pose stream through the real router -- attitude carrying a
fixed yaw, position ramped along the sensor's X -- to SITL under each VISO_TYPE, and
measure the bearing of the resulting EKF displacement (LOCAL_POSITION_NED) plus any
align STATUSTEXT. Prediction: type 1 tracks the fed direction; type 2 rotates it by
the sensor<->AHRS yaw trim.

This is a mechanism check (synthetic pose, exact SITL truth), not a real-flight result.
"""
import json
import math
import os
import socket
import struct
import subprocess
import sys
import time

os.environ["MAVLINK20"] = "1"
from pymavlink import mavutil  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
ARDUCOPTER = os.path.expanduser("~/ardupilot/build/sitl/bin/arducopter")
COPTER_PARM = os.path.expanduser("~/ardupilot/Tools/autotest/default_params/copter.parm")
EXTNAV_PARM = os.path.join(HERE, "extnav.parm")
ROUTER = os.path.join(REPO, "containers", "coordinator-mavlink", "router.py")
HOME = "-35.363261,149.165230,584,0"  # yaw 0 (north) for a clean AHRS reference
ORIGIN = (int(-35.363261e7), int(149.165230e7), int(584e3))

SENSOR_YAW_DEG = 90.0   # yaw baked into the ExtNav attitude quaternion
RAMP_M = 3.0            # ExtNav position ramp along sensor-X (smooth -> real motion, not a glitch)
HOLD_S, RAMP_S, POST_S = 7.0, 6.0, 4.0
RATE_HZ = 20.0


def wait_heartbeat(port, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            c = mavutil.mavlink_connection(f"tcp:127.0.0.1:{port}")
        except Exception:
            time.sleep(1); continue
        if c.wait_heartbeat(timeout=5):
            return c
        c.close(); time.sleep(1)
    raise RuntimeError(f"no heartbeat on {port}")


def run_arm(viso_type, instance):
    port0, port5 = 5760 + 10 * instance, 5765 + 10 * instance
    rundir = os.path.join(HERE, "run", f"yaw_t{viso_type}")
    os.makedirs(rundir, exist_ok=True)
    try:
        os.remove(os.path.join(rundir, "eeprom.bin"))
    except OSError:
        pass
    # per-arm VISO_TYPE override (last defaults file wins)
    vt = os.path.join(rundir, "viso_type.parm")
    open(vt, "w").write(f"VISO_TYPE {viso_type}\n")
    sockpath = f"/tmp/yaw_t{viso_type}.sock"

    sitl = subprocess.Popen(
        [ARDUCOPTER, "--model", "quad", "--home", HOME,
         "--defaults", f"{COPTER_PARM},{EXTNAV_PARM},{vt}",
         f"--serial5=tcp:{port5}", "-I", str(instance)],
        cwd=rundir, stdout=open(os.path.join(rundir, "sitl.log"), "w"), stderr=subprocess.STDOUT)
    router = None
    try:
        c = wait_heartbeat(port0)
        # Settle past the param-load reboot + lockstep clock re-sync BEFORE anything
        # connects to serial5 -- connecting during that window kills SITL (observed).
        t = time.time()
        while time.time() - t < 12:
            c.recv_match(blocking=True, timeout=0.5)
            if sitl.poll() is not None:
                raise RuntimeError("SITL exited during settle")
        c.mav.set_gps_global_origin_send(c.target_system, *ORIGIN)
        for mid, hz in [(mavutil.mavlink.MAVLINK_MSG_ID_LOCAL_POSITION_NED, RATE_HZ),
                        (mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE, 10)]:
            c.mav.command_long_send(c.target_system, c.target_component,
                                    mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                                    mid, int(1e6 / hz), 0, 0, 0, 0, 0)
        # AHRS yaw (vehicle heading) -- the reference the T265 backend aligns to
        ahrs_yaw = None
        t = time.time()
        while time.time() - t < 3 and ahrs_yaw is None:
            m = c.recv_match(type="ATTITUDE", blocking=True, timeout=0.5)
            if m:
                ahrs_yaw = math.degrees(m.yaw)

        router = subprocess.Popen(
            [sys.executable, ROUTER, "--device", f"tcp:127.0.0.1:{port5}",
             "--socket", sockpath, "--source-system", "1", "--source-component", "197"],
            stdout=open(os.path.join(rundir, "router.log"), "w"), stderr=subprocess.STDOUT)
        for _ in range(100):
            if os.path.exists(sockpath):
                break
            time.sleep(0.05)
        time.sleep(1.5)

        tx = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        psi = math.radians(SENSOR_YAW_DEG)
        qw, qz = math.cos(psi / 2), math.sin(psi / 2)  # pure-yaw quaternion

        def send(x):
            tx.sendto(struct.pack("<12f", qw, 0, 0, qz, x, 0, 0, 9, 9, 9, 0, 30), sockpath)

        lp = []          # (t, N, E)
        align_txt = []
        t0 = time.time()
        while True:
            now = time.time() - t0
            if now < HOLD_S:
                x = 0.0
            elif now < HOLD_S + RAMP_S:
                x = RAMP_M * (now - HOLD_S) / RAMP_S
            elif now < HOLD_S + RAMP_S + POST_S:
                x = RAMP_M
            else:
                break
            send(x)
            m = c.recv_match(blocking=True, timeout=1.0 / RATE_HZ)
            if m is None:
                continue
            if m.get_type() == "LOCAL_POSITION_NED":
                lp.append((now, m.x, m.y))
            elif m.get_type() == "STATUSTEXT" and ("align" in m.text.lower() or "viso" in m.text.lower()):
                align_txt.append(m.text)

        # displacement bearing: median of last 1s of hold vs last 1s of post
        def med(lo, hi):
            pts = [(n, e) for tt, n, e in lp if lo <= tt <= hi]
            if not pts:
                return None
            pts.sort()
            mid = pts[len(pts) // 2]
            return mid
        p0 = med(HOLD_S - 1, HOLD_S)
        p1 = med(HOLD_S + RAMP_S + POST_S - 1.5, 1e9)
        bearing = mag = None
        if p0 and p1:
            dN, dE = p1[0] - p0[0], p1[1] - p0[1]
            bearing = round(math.degrees(math.atan2(dE, dN)), 1)
            mag = round(math.hypot(dN, dE), 2)
        return {
            "viso_type": viso_type,
            "backend": "IntelT265" if viso_type in (2, 3) else "MAV",
            "ahrs_yaw_deg": round(ahrs_yaw, 1) if ahrs_yaw is not None else None,
            "sensor_yaw_deg": SENSOR_YAW_DEG,
            "ekf_disp_bearing_deg": bearing, "ekf_disp_m": mag,
            "align_statustext": align_txt[:3],
        }
    finally:
        for p in (router, sitl):
            if p is not None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        try:
            os.remove(sockpath)
        except OSError:
            pass
        time.sleep(2)


def main():
    results = {}
    for vt, inst in [(1, 0), (2, 1)]:
        r = run_arm(vt, inst)
        results[f"type{vt}"] = r
        print(json.dumps(r), flush=True)
    with open(os.path.join(HERE, "run", "yaw_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    print("wrote run/yaw_results.json", flush=True)


if __name__ == "__main__":
    main()
