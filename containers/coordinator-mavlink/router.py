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

Velocity + covariance (coordinator #62 Part 1)
----------------------------------------------
The estimator's velocity field (vx,vy,vz in the datagram) is IDENTICALLY ZERO in
the recommended stereo-only config, so forwarding it is useless. Instead we
compute velocity from dPos/dt between consecutive pose datagrams -- the bridge
owns this, and dPos/dt was validated against the FC EKF velocity to ~0.15 m/s 1sigma
with a stationary (drift-free) error, so a fixed covariance is defensible (#62).

We also send an honest per-sample covariance on both messages. The EKF uses it,
floored by VISO_VEL_M_NSE / VISO_POS_M_NSE (NOT the EK3_*_M_NSE params) -- see
docs/ardupilot-extnav-fusion.md. Omitting it (the old behaviour) let the FC floor
the noise to 0.1, over-trusting the source. We do NO bridge-side signal filtering:
dPos/dt spikes pass through for the FC innovation gate to reject.

Not yet done here: propagating the VINS reset counter (clean EKF position reset on
each ice-hole re-init). The float[10] IPC datagram carries no reset counter and
ATT_POS_MOCAP has no reset_counter field -- both need upstream changes, so it stays
a follow-up (docs/ardupilot-extnav-fusion.md).

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

# The covariance/reset_counter fields on these messages are MAVLink2 extensions.
# pymavlink only exposes them on the v2.0 dialect, which is selected by MAVLINK20
# at import time -- set it before importing pymavlink. (This also makes the wire
# frames v2 so the extension bytes actually serialize.)
os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil  # noqa: E402

POSE_FMT = "<10f"
POSE_SIZE = struct.calcsize(POSE_FMT)  # 40
# Inside the container ${COORDINATOR_IPC_DIR} is mounted at /tmp.
DEFAULT_SOCKET = "/tmp/chobits_server"

# Honest measurement noise sent to the FC (1sigma), overridable by env.
#  * Velocity: 0.15 m/s -- MEASURED. dPos/dt tracks the FC EKF velocity to ~0.15 m/s
#    1sigma (median 8 cm/s), stationary error -> fixed covariance (#62).
#  * Position: 0.30 m -- CONSERVATIVE PLACEHOLDER, not independently measured. Kept
#    above the VISO_POS_M_NSE 0.2 m floor so it is the binding value, not the floor.
#    Refine via SITL / flight tuning (#62 Part 2, #64).
DEFAULT_VEL_NSE = float(os.environ.get("MAVLINK_VEL_NSE", "0.15"))
DEFAULT_POS_NSE = float(os.environ.get("MAVLINK_POS_NSE", "0.30"))

# Below this dt (s) between poses, dPos/dt amplifies position noise into a bogus
# velocity (duplicate/too-close samples) -- skip velocity for that sample. This is
# numerical hygiene, not signal filtering: real motion spikes still pass through.
MIN_DT = 1e-3


def velocity_covariance(vel_nse):
    """9-element row-major 3x3 for VISION_SPEED_ESTIMATE.

    The FC collapses this to a scalar velErr = sqrt(cov[0]+cov[4]+cov[8]) and uses
    it as the per-axis velocity noise (GCS_Common.cpp:4167, docs/ardupilot-extnav-fusion.md).
    So to make the FC's effective noise equal vel_nse, the three diagonal entries
    must SUM to vel_nse**2 -- hence vel_nse**2 / 3 each, not vel_nse**2 each.
    """
    cov = [0.0] * 9
    cov[0] = cov[4] = cov[8] = (vel_nse * vel_nse) / 3.0
    return cov


def position_covariance(pos_nse):
    """21-element row-major upper-triangle of the 6x6 pose covariance
    (states x,y,z,roll,pitch,yaw). Diagonal indices: x=0, y=6, z=11.

    The FC derives posErr from this (GCS_Common.cpp:4148, floored at VISO_POS_M_NSE).
    We fill only the position variances; attitude entries stay 0 (mocap yaw is unused
    with EK3_SRC_YAW=compass).
    """
    cov = [0.0] * 21
    cov[0] = cov[6] = cov[11] = pos_nse * pos_nse
    return cov


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
    ap.add_argument("--vel-nse", type=float, default=DEFAULT_VEL_NSE,
                    help="velocity 1sigma sent to the FC (m/s)")
    ap.add_argument("--pos-nse", type=float, default=DEFAULT_POS_NSE,
                    help="position 1sigma sent to the FC (m)")
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
        f"(sysid {args.source_system}, comp {args.source_component}) "
        f"vel_nse={args.vel_nse} pos_nse={args.pos_nse}",
        file=sys.stderr,
        flush=True,
    )

    vel_cov = velocity_covariance(args.vel_nse)
    pos_cov = position_covariance(args.pos_nse)
    serial_fd = mav.port.fileno()

    # Previous pose for dPos/dt velocity: (px, py, pz, monotonic_ts). The estimator's
    # own velocity field is ignored (zero in stereo-only); we derive it here.
    prev = None

    while True:
        readable, _, _ = select.select([usock, serial_fd], [], [], 1.0)

        if usock in readable:
            data = usock.recv(256)
            if len(data) >= POSE_SIZE:
                qw, qx, qy, qz, px, py, pz, _vx, _vy, _vz = struct.unpack(POSE_FMT, data[:POSE_SIZE])
                usec = int(time.time() * 1e6)
                now = time.monotonic()
                mav.mav.att_pos_mocap_send(usec, [qw, qx, qy, qz], px, -py, -pz, pos_cov)

                # Velocity = dPos/dt in the estimator frame, then the same (x,-y,-z)
                # flip as position. Skip the first sample and any dt too small to
                # differentiate safely.
                if prev is not None:
                    dt = now - prev[3]
                    if dt >= MIN_DT:
                        vx = (px - prev[0]) / dt
                        vy = (py - prev[1]) / dt
                        vz = (pz - prev[2]) / dt
                        mav.mav.vision_speed_estimate_send(usec, vx, -vy, -vz, vel_cov)
                prev = (px, py, pz, now)

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
