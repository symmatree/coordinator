#!/usr/bin/env python3
"""coordinator-mavlink router (MVP).

Reads vins_fusion pose from the AF_UNIX dgram socket /tmp/chobits_server and
forwards it to the flight controller over UART as MAVLink2.

IPC pose contract (versioned, length-detected)
----------------------------------------------
  * v1 (40 bytes, float[10]): quat w,x,y,z + pos x,y,z + vel x,y,z.
  * v2 (48 bytes, float[12]): v1 + reset_counter + feature_count (appended, so a
    v1 reader still finds quat/pos/vel at the same offsets). Emitted by the
    coordinator estimator overlay (pubOdometry / main_offline.cpp). See
    docs/vio-integration.md and docs/coordinator-mavlink.md.
The two v2 health fields are the joint contract that lets us (a) forward a clean
EKF position reset and (b) send an honest, growing position covariance:
  * reset_counter -- bumped by the estimator on each failure re-init (a datum
    jump). Forwarded as VISION_POSITION_ESTIMATE.reset_counter so the FC does a
    clean ResetPositionNE instead of fighting the jump as a glitch (#67). This is
    why position moved off ATT_POS_MOCAP (which hard-codes reset_counter=0 at
    GCS_Common.cpp:4139) onto VISION_POSITION_ESTIMATE (which carries the field).
  * feature_count -- features tracked into the newest frame; the raw health
    signal available to the covariance model (currently a wired-but-disabled
    hook, POS_FEAT_K=0 -- the feature-count->covariance link is unproven, #124).

Messages sent per pose (MAVLink2):
    VISION_POSITION_ESTIMATE  position (x,-y,-z), euler attitude, position
                              covariance, reset_counter
    VISION_SPEED_ESTIMATE     dPos/dt velocity (x,-y,-z), velocity covariance

The (x,-y,-z) flip is the ENU/FLU -> NED/FRD convention from the chobits
reference. The router also replies to FC TIMESYNC requests so the link is a
cooperative time-sync endpoint.

Time reconciliation (#167)
--------------------------
Every TIMESYNC exchange is appended to a JSONL file (COORD_TIMESYNC_LOG). Each
line pairs the FC's clock with ours at one instant: the FC's ts1, the tc1 we
reply with (CLOCK_REALTIME), and CLOCK_MONOTONIC read next to the reply. The
monotonic reading is the load-bearing one -- the OAK-D capture sidecars stamp
frames with monotonic_ns, and both containers share the host kernel's clock
(network_mode: host), so these lines are what maps a still to a point on the FC
log's timeline. Without them the two clocks are only relatable through wall time,
which is where the ~5 s capture/flight-timeline discrepancy of #167 lives.

Attitude note: VISION_POSITION_ESTIMATE carries euler roll/pitch/yaw. We convert
the estimator quaternion directly (no NED frame correction). This is fusion-inert
under our config (EK3_SRC_YAW=compass -> VIO yaw unused; roll/pitch come from the
FC IMU). If VIO yaw is ever enabled, the correct NED attitude must be derived here.

Velocity + covariance
---------------------
The estimator's velocity field is IDENTICALLY ZERO in stereo-only, so we compute
velocity from dPos/dt between consecutive poses -- validated vs the FC EKF to
~0.15 m/s 1sigma with a stationary (drift-free) error, so a fixed covariance is
defensible (#62).

Covariance (verified against the ArduPilot Copter-4.7.0 tag):
  * POSITION is a per-sample honest channel. The FC consumes the message
    covariance: posErr = sqrt(cov[0]+cov[6]+cov[11]) (GCS_Common.cpp:4134),
    floored at VISO_POS_M_NSE, capped at 100 m, then used as the position
    observation noise. VIO position uncertainty GROWS with distance travelled
    since the last anchor/reset (stereo VO drifts ~1-3% of path length, E11), so
    we send a growing posErr = base + drift_k * path_len, zeroed on each reset.
    4.7's clamp widening (10 m -> 100 m) is what makes this expressible. See
    position_covariance() for the /3 encoding the sqrt-collapse needs.
  * VELOCITY covariance is IGNORED by the FC. handle_vision_speed_estimate
    forwards no covariance; the MAV backend fuses at the FC param VISO_VEL_M_NSE
    (writeExtNavVelData, AP_VisualOdom_MAV.cpp:79) -- confirmed by upstream PR
    #14516 ("I do not send covariance from mavlink msg"). So the velocity noise
    the EKF uses is VISO_VEL_M_NSE, not anything we send. We still emit a
    covariance (logged, harmless); to change fused velocity noise set
    VISO_VEL_M_NSE on the FC to match MAVLINK_VEL_NSE.
We do NO bridge-side signal filtering of ordinary spikes -- the FC innovation
gate rejects them. The one exception is a known reset: on the sample where
reset_counter bumps we suppress the (spurious) dPos/dt velocity and do not count
the datum jump as travelled distance, because the reset_counter already tells the
FC to reset cleanly.
"""

import argparse
import json
import math
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

# Versioned pose contract: v1 = float[10] (40 B), v2 = float[12] (48 B). We read
# whichever arrives (length-detected); v1 poses get reset_counter=0, no feature
# health. The two extra floats are appended, so the first 10 are identical.
POSE_FMT_V1 = "<10f"
POSE_FMT_V2 = "<12f"
POSE_SIZE_V1 = struct.calcsize(POSE_FMT_V1)  # 40
POSE_SIZE_V2 = struct.calcsize(POSE_FMT_V2)  # 48
# Inside the container ${COORDINATOR_IPC_DIR} is mounted at /tmp.
DEFAULT_SOCKET = "/tmp/chobits_server"

# Honest measurement noise sent to the FC (1sigma), overridable by env.
#  * Velocity: 0.15 m/s -- MEASURED. dPos/dt tracks the FC EKF velocity to ~0.15 m/s
#    1sigma (median 8 cm/s), stationary error -> fixed covariance (#62). The FC IGNORES
#    the velocity covariance and fuses at VISO_VEL_M_NSE, so set that FC param to match.
#  * Position: a GROWING model, posErr = base + drift_k * path_len (metres travelled
#    since the last reset/anchor), capped. base 0.30 m is a conservative floor above
#    VISO_POS_M_NSE (0.2); drift_k 0.02 (~2%/m) seeds from the E11 drift-vs-distance
#    K-sweep -- refine both from flight residuals (#62 Part 2, #64).
DEFAULT_VEL_NSE = float(os.environ.get("MAVLINK_VEL_NSE", "0.15"))
# MAVLINK_POS_NSE kept as a back-compat alias for the base term.
DEFAULT_POS_NSE_BASE = float(
    os.environ.get("MAVLINK_POS_NSE_BASE", os.environ.get("MAVLINK_POS_NSE", "0.30"))
)
DEFAULT_POS_DRIFT_K = float(os.environ.get("MAVLINK_POS_DRIFT_K", "0.02"))
DEFAULT_POS_NSE_MAX = float(os.environ.get("MAVLINK_POS_NSE_MAX", "100.0"))
# Feature-starvation covariance inflation: DISABLED by default (0). The causal
# link feature-count -> position error is unproven (features surviving RANSAC may
# be robust, #124); this is a hook to be set from the observed residual
# distribution, not asserted now. When >0: posErr += k / max(feature_count, 1).
DEFAULT_POS_FEAT_K = float(os.environ.get("MAVLINK_POS_FEAT_K", "0.0"))

# Below this dt (s) between poses, dPos/dt amplifies position noise into a bogus
# velocity (duplicate/too-close samples) -- skip velocity for that sample. This is
# numerical hygiene, not signal filtering: real motion spikes still pass through.
MIN_DT = 1e-3

# Emit a HEARTBEAT at this cadence so the FC recognizes the coordinator as a MAVLink
# component and streams telemetry to SERIAL4 (MAV2), and so the router is a well-behaved
# node. ONBOARD_CONTROLLER (not GCS) so there's no GCS-failsafe coupling on the FC.
HEARTBEAT_INTERVAL_S = 1.0


def velocity_covariance(vel_nse):
    """9-element row-major 3x3 for VISION_SPEED_ESTIMATE.

    NOTE: ArduPilot (through Copter-4.7.0) does NOT consume this -- the FC fuses
    velocity at VISO_VEL_M_NSE regardless (see module docstring). We fill it so that
    IF a future FC reads it via the sqrt(cov[0]+cov[4]+cov[8]) collapse (the position
    path's convention), the effective per-axis noise would equal vel_nse -- hence
    vel_nse**2 / 3 on each diagonal, not vel_nse**2. Today it is advisory/logged only.
    """
    cov = [0.0] * 9
    cov[0] = cov[4] = cov[8] = (vel_nse * vel_nse) / 3.0
    return cov


def position_covariance(pos_nse):
    """21-element row-major upper-triangle of the 6x6 pose covariance
    (states x,y,z,roll,pitch,yaw). Diagonal indices: x=0, y=6, z=11.

    The FC collapses this to a scalar posErr = sqrt(cov[0]+cov[6]+cov[11])
    (GCS_Common.cpp:4134, verified against the Copter-4.7.0 tag) and uses it as the
    per-axis position observation noise, floored at VISO_POS_M_NSE and capped at
    100 m. So to make the FC's effective per-axis noise equal pos_nse, the three
    diagonal entries must SUM to pos_nse**2 -- hence pos_nse**2 / 3 each, the SAME
    collapse the velocity path uses (velocity_covariance).

    NOTE (4.6.3 -> 4.7 change): 4.6.3 used a dimensionally-broken
    cbrtf(sq(cov[0])+sq(cov[6])+sq(cov[11])), under which pos_nse**2-per-axis landed
    near pos_nse by luck; 4.7 fixed it to sqrt(sum-of-variances), under which the old
    encoding yields sqrt(3)*pos_nse. See docs/ardupilot-extnav-fusion.md.

    We fill only the position variances; attitude entries stay 0 (mocap yaw is unused
    with EK3_SRC_YAW=compass).
    """
    cov = [0.0] * 21
    cov[0] = cov[6] = cov[11] = (pos_nse * pos_nse) / 3.0
    return cov


def quat_to_euler(w, x, y, z):
    """Quaternion (w,x,y,z) -> (roll, pitch, yaw) in radians, aerospace ZYX.

    Fusion-inert under our config (EK3_SRC_YAW=compass) -- carried on
    VISION_POSITION_ESTIMATE for completeness. Not NED-frame-corrected; see the
    module docstring before enabling VIO yaw.
    """
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return roll, pitch, yaw


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
    ap.add_argument("--pos-nse-base", type=float, default=DEFAULT_POS_NSE_BASE,
                    help="position 1sigma at a fresh anchor/reset (m)")
    ap.add_argument("--pos-drift-k", type=float, default=DEFAULT_POS_DRIFT_K,
                    help="position 1sigma growth per metre travelled since reset")
    ap.add_argument("--pos-nse-max", type=float, default=DEFAULT_POS_NSE_MAX,
                    help="cap on position 1sigma (m); FC honours up to 100")
    ap.add_argument("--pos-feat-k", type=float, default=DEFAULT_POS_FEAT_K,
                    help="feature-starvation inflation gain (0 = disabled)")
    return ap.parse_args()


def write_arm_file(path, armed):
    """#88: publish FC arm state to a small shared file the capturers read.

    Atomic (write temp + rename) so a reader never sees a torn value; "1"/"0".
    """
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("1\n" if armed else "0\n")
    os.replace(tmp, path)


def open_timesync_log(path):
    """Append-mode JSONL sink for TIMESYNC exchanges (#167).

    Line-buffered so a power cut keeps everything up to the last exchange, and the
    directory is created if absent so the router runs the same standalone (tests,
    stack-smoke) as it does with /captures mounted.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return open(path, "a", buffering=1)


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
        f"vel_nse={args.vel_nse} pos_nse_base={args.pos_nse_base} "
        f"pos_drift_k={args.pos_drift_k}",
        file=sys.stderr,
        flush=True,
    )

    vel_cov = velocity_covariance(args.vel_nse)
    serial_fd = mav.port.fileno()

    # Previous pose for dPos/dt velocity: (px, py, pz, monotonic_ts). The estimator's
    # own velocity field is ignored (zero in stereo-only); we derive it here.
    prev = None
    # Growing-covariance state: path length travelled since the last reset/anchor,
    # and the reset_counter of the current anchor epoch.
    path_len = 0.0
    last_reset = None

    # #88: FC arm state -> shared file. vio-tracker gates image capture on this; and we
    # sync() the filesystem at disarm so the flight's captures are durable through the
    # power cut that usually follows. Path shared via OAK_ARM_FILE (both containers read it).
    arm_file = os.environ.get("OAK_ARM_FILE", "/tmp/fc_armed")
    armed = False
    write_arm_file(arm_file, armed)

    # #167: FC-clock <-> our-clock pairs, for joining captures to the FC log.
    ts_log = open_timesync_log(
        os.environ.get("COORD_TIMESYNC_LOG", "/tmp/timesync.jsonl"))
    last_hb = 0.0  # monotonic ts of the last HEARTBEAT we sent

    while True:
        readable, _, _ = select.select([usock, serial_fd], [], [], 1.0)

        now_mono = time.monotonic()
        if now_mono - last_hb >= HEARTBEAT_INTERVAL_S:
            mav.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0,
                mavutil.mavlink.MAV_STATE_ACTIVE)
            last_hb = now_mono

        if usock in readable:
            data = usock.recv(256)
            if len(data) >= POSE_SIZE_V2:
                (qw, qx, qy, qz, px, py, pz, _vx, _vy, _vz,
                 reset_f, feat_f) = struct.unpack(POSE_FMT_V2, data[:POSE_SIZE_V2])
                reset_counter = int(reset_f)
                feature_count = int(feat_f)
            elif len(data) >= POSE_SIZE_V1:
                qw, qx, qy, qz, px, py, pz, _vx, _vy, _vz = struct.unpack(
                    POSE_FMT_V1, data[:POSE_SIZE_V1])
                reset_counter = 0       # v1: no counter -> never resets
                feature_count = -1      # v1: unknown
            else:
                continue

            usec = int(time.time() * 1e6)
            now = time.monotonic()

            # Reset handling: a bumped counter is a datum discontinuity, not travel.
            is_reset = last_reset is not None and reset_counter != last_reset
            if is_reset:
                path_len = 0.0
            last_reset = reset_counter

            # Accumulate real distance travelled since the anchor (not the jump).
            if prev is not None and not is_reset:
                path_len += math.sqrt(
                    (px - prev[0]) ** 2 + (py - prev[1]) ** 2 + (pz - prev[2]) ** 2)

            # Honest, growing position uncertainty; optional feature inflation (off
            # by default). Capped to what the FC honours (100 m on 4.7).
            pos_err = args.pos_nse_base + args.pos_drift_k * path_len
            if args.pos_feat_k > 0 and feature_count >= 0:
                pos_err += args.pos_feat_k / max(feature_count, 1)
            pos_err = min(pos_err, args.pos_nse_max)
            pos_cov = position_covariance(pos_err)

            roll, pitch, yaw = quat_to_euler(qw, qx, qy, qz)
            mav.mav.vision_position_estimate_send(
                usec, px, -py, -pz, roll, pitch, yaw,
                covariance=pos_cov, reset_counter=reset_counter & 0xFF)

            # Velocity = dPos/dt, same (x,-y,-z) flip. Skip the first sample, any dt
            # too small to differentiate, and reset samples (the jump is not motion).
            if prev is not None and not is_reset:
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
                    # Read both clocks as close to the reply as possible: tc1 is what
                    # the FC sees, mono is what the capture sidecars are stamped in.
                    mono_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                    tc1 = time.time_ns()
                    mav.mav.timesync_send(tc1, msg.ts1)
                    ts_log.write(json.dumps({
                        "fc_ts1_ns": msg.ts1,
                        "tc1_realtime_ns": tc1,
                        "monotonic_ns": mono_ns,
                    }) + "\n")
                elif (msg.get_type() == "HEARTBEAT"
                      and msg.type != mavutil.mavlink.MAV_TYPE_GCS):
                    # #88: FC arm/disarm -> shared file; sync() the FS at disarm so the
                    # flight's captures survive the power cut that usually follows.
                    now_armed = bool(
                        msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                    if now_armed != armed:
                        armed = now_armed
                        write_arm_file(arm_file, armed)
                        print(f"coordinator-mavlink: FC {'ARMED' if armed else 'DISARMED'}",
                              file=sys.stderr, flush=True)
                        if not armed:
                            os.sync()  # flush captured data to disk at disarm


if __name__ == "__main__":
    main()
