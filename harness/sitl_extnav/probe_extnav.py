#!/usr/bin/env python3
"""Feasibility probe: does SITL EKF3 navigate on our injected VISION_POSITION_ESTIMATE?

Connects to SITL serial0 (tcp:5760), sets the EKF origin with no GPS, feeds a
ramping ExtNav position, and checks LOCAL_POSITION_NED follows it -- i.e. the EKF
is actually fusing our external nav. Bounded run; prints a verdict.
"""
import os
import time

os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil  # noqa: E402

HOME_LAT, HOME_LON, HOME_ALT = -35.363261, 149.165230, 584.0

m = mavutil.mavlink_connection("tcp:127.0.0.1:5760")
print("waiting for heartbeat...", flush=True)
m.wait_heartbeat(timeout=30)
print(f"heartbeat: sys={m.target_system} comp={m.target_component}", flush=True)


def req(msgid, hz):
    m.mav.command_long_send(m.target_system, m.target_component,
                            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                            msgid, int(1e6 / hz), 0, 0, 0, 0, 0)


# Origin without GPS -- the ViconPosition autotest recipe.
m.mav.set_gps_global_origin_send(
    m.target_system, int(HOME_LAT * 1e7), int(HOME_LON * 1e7), int(HOME_ALT * 1000))
req(mavutil.mavlink.MAVLINK_MSG_ID_LOCAL_POSITION_NED, 10)
req(mavutil.mavlink.MAVLINK_MSG_ID_EKF_STATUS_REPORT, 5)
req(mavutil.mavlink.MAVLINK_MSG_ID_GPS_GLOBAL_ORIGIN, 2)

t0 = time.time()
origin_set = False
last_lp = None
last_flags = None
max_x = -1e9
while time.time() - t0 < 14:
    # Ramp ExtNav north 0 -> ~5 m over the window; y=z=0. NED frame.
    x = min(5.0, 0.5 * (time.time() - t0))
    usec = int(time.time() * 1e6)
    m.mav.vision_position_estimate_send(usec, x, 0.0, 0.0, 0.0, 0.0, 0.0)
    msg = m.recv_match(blocking=True, timeout=0.05)
    if msg is None:
        continue
    t = msg.get_type()
    if t == "GPS_GLOBAL_ORIGIN":
        origin_set = True
    elif t == "LOCAL_POSITION_NED":
        last_lp = (msg.x, msg.y, msg.z)
        max_x = max(max_x, msg.x)
    elif t == "EKF_STATUS_REPORT":
        last_flags = msg.flags

print(f"origin_set={origin_set}  ekf_flags={last_flags}  last LOCAL_POSITION_NED={last_lp}",
      flush=True)
print(f"max EKF north = {max_x:.2f} m (fed ExtNav ramped to 5.0 m north)", flush=True)
# EKF_ATTITUDE|VELOCITY_HORIZ|POS_HORIZ_REL|POS_HORIZ_ABS bits indicate aiding.
ok = origin_set and max_x > 2.0
print("VERDICT:", "PASS -- EKF navigates on injected ExtNav" if ok
      else "INCONCLUSIVE -- EKF did not track injected ExtNav north", flush=True)
