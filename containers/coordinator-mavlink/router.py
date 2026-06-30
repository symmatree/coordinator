#!/usr/bin/env python3
"""coordinator-mavlink router (MVP).

Reads vins_fusion pose from the AF_UNIX dgram socket /tmp/chobits_server
(float[10]: quat w,x,y,z + pos x,y,z + vel x,y,z) and forwards it to the flight
controller over UART as MAVLink2, using the documented-working message set from
the ArduPilot OAK-D wiki / chobitsfan mavlink-udp-proxy (apm_wiki):

    ATT_POS_MOCAP          quaternion as-is, position (x, -y, -z)
    VISION_SPEED_ESTIMATE  velocity (x, -y, -z)

The (x, -y, -z) flip is the ENU/FLU -> NED/FRD convention from the reference.
The router also replies to FC TIMESYNC requests so the link is a cooperative
time-sync endpoint.

This is a FAITHFUL MINIMAL PORT of the proven reference, not a redesign. It
intentionally DROPS the reference's SET_GPS_GLOBAL_ORIGIN (hardcoded foreign
coordinates -- wrong for us; flying GPS-primary the FC already has an origin) and
its planner/LAND command path. The SYSTEM_TIME->chrony feed, in-flight pose
logging (#30), and a GPS-denied origin handshake are deliberate follow-ups.
"""

import argparse
import os
import select
import socket
import struct
import sys
import time

from pymavlink import mavutil

POSE_FMT = "<10f"
POSE_SIZE = struct.calcsize(POSE_FMT)  # 40
# Inside the container ${COORDINATOR_IPC_DIR} is mounted at /tmp.
DEFAULT_SOCKET = "/tmp/chobits_server"


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", default=os.environ.get("MAVLINK_DEVICE", "/dev/serial0"))
    ap.add_argument("--baud", type=int, default=int(os.environ.get("MAVLINK_BAUD", "1500000")))
    ap.add_argument("--socket", default=os.environ.get("MAVLINK_POSE_SOCKET", DEFAULT_SOCKET))
    ap.add_argument(
        "--source-system", type=int, default=int(os.environ.get("MAVLINK_SRC_SYSTEM", "1"))
    )
    ap.add_argument(
        "--source-component",
        type=int,
        default=int(
            os.environ.get(
                "MAVLINK_SRC_COMPONENT",
                str(mavutil.mavlink.MAV_COMP_ID_VISUAL_INERTIAL_ODOMETRY),
            )
        ),
    )
    return ap.parse_args()


def main():
    args = parse_args()

    # The pose consumer binds the dgram socket; clear a stale node first.
    try:
        os.unlink(args.socket)
    except FileNotFoundError:
        pass
    usock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    usock.bind(args.socket)

    mav = mavutil.mavlink_connection(
        args.device,
        baud=args.baud,
        source_system=args.source_system,
        source_component=args.source_component,
        dialect="ardupilotmega",
    )
    print(
        f"coordinator-mavlink: {args.socket} -> {args.device}@{args.baud} "
        f"(sysid {args.source_system}, comp {args.source_component})",
        file=sys.stderr,
        flush=True,
    )

    # The covariance/reset_counter fields on these messages are MAVLink extensions;
    # we omit them (older pymavlink lacks the args, and a receiver zero-fills missing
    # extension fields -> "covariance unknown", which is what we want at the FC).
    serial_fd = mav.port.fileno()

    while True:
        readable, _, _ = select.select([usock, serial_fd], [], [], 1.0)

        if usock in readable:
            data = usock.recv(256)
            if len(data) >= POSE_SIZE:
                qw, qx, qy, qz, px, py, pz, vx, vy, vz = struct.unpack(POSE_FMT, data[:POSE_SIZE])
                usec = int(time.time() * 1e6)
                mav.mav.att_pos_mocap_send(usec, [qw, qx, qy, qz], px, -py, -pz)
                mav.mav.vision_speed_estimate_send(usec, vx, -vy, -vz)

        if serial_fd in readable:
            # Drain whatever arrived; reply to FC TIMESYNC requests (tc1 == 0).
            while True:
                msg = mav.recv_match(blocking=False)
                if msg is None:
                    break
                if msg.get_type() == "TIMESYNC" and msg.tc1 == 0:
                    mav.mav.timesync_send(int(time.time() * 1e9), msg.ts1)


if __name__ == "__main__":
    main()
