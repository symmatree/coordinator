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

Coordinator-side tlog (COORD_TLOG)
---------------------------------
The FC already streams state to us and we were dropping all of it. The coordinator is
**MAV2** (MAVLink channels are assigned in SERIALn order over the MAVLink ports:
SERIAL0 USB, SERIAL4 coordinator, SERIAL6 ELRS, SERIAL9 OTG), and MAV2 has EXT_STAT,
POSITION and EXTRA1/2/3 all at 2 Hz -- about 41 messages/s measured on 260814. So
SYSTEM_TIME (on EXTRA3), GPS_RTK, EKF_STATUS_REPORT, VIBRATION, ESC_TELEMETRY,
ATTITUDE and the rest arrive without any parameter change.

We log **everything that arrives**, in standard tlog format: an 8-byte big-endian
microsecond timestamp followed by the raw MAVLink frame. Reasons to prefer that over a
curated projection:

  * **It is small.** 41 msg/s is ~0.7 MB for a six-minute flight, against 192 MB of
    captures and an 83 MB dataflash log for 260814. Selectivity buys nothing here.
  * **A whitelist encodes today's beliefs about what matters**, and those have been
    wrong repeatedly -- the RTCM correction counters, the per-channel link stats and
    the FC's own TIMESYNC records were all "noise" until they were the answer.
  * **A whitelist fails silently.** Rename a field upstream and it stops being logged
    with no error; a raw frame records whatever actually arrived.
  * It is the same format mavproxy writes on the ground, so one set of tools reads
    both, and the vehicle-side and ground-side records are directly comparable.

The timestamp is wall clock, for tool compatibility -- which means it inherits the
coordinator's untrustworthy wall clock (see below and docs/flight-data-interpretation.md).
COORD_TIMESYNC_LOG remains the bridge: it pairs realtime with CLOCK_MONOTONIC and the
FC's own clock, and that pairing is *computed*, not received, so it cannot be recovered
from the tlog.

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


def write_fc_link(path, boot_id, heartbeat_mono_s, heartbeats):
    """#669: when this router last heard the FC, for the front panel.

    The arm file cannot say this: it reads "0" both for a disarmed FC and for a wire
    that carries nothing, and on 2026-10-30 two flights went by with the panel showing
    the second as the first. pod-link passes this document through on the coordinator's
    bus status and the panel draws it.

    JSON, because pod-link embeds it verbatim. The heartbeat time is time.monotonic(),
    the clock pod-link stamps `as_of_boot_s` with, so a reader differences the two. The
    boot id goes with it because this file is on the card and outlives the boot whose
    clock it is in. `null` until the first heartbeat: written at startup so a router
    that is up and hearing nothing says so, rather than leaving last boot's file.
    """
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"boot_id": boot_id, "last_heartbeat_mono_s": heartbeat_mono_s,
                   "heartbeats": heartbeats}, f)
    os.replace(tmp, path)


def write_fc_time(path, utc_minus_raw_us, rtt_us, raw_us, boot_id):
    """Publish UTC against this box's CLOCK_MONOTONIC_RAW for coord-rtc to read (#11).

    Separate from the session log below because they have different jobs: this is a
    current value, replaced atomically, that coord-rtc uses to correct the DS3234's
    epoch while the vehicle is disarmed. The log is the per-flight record.

    RAW, NOT REALTIME (#593). The realtime clock is disciplined to the chip, so a chip
    write makes chrony slew it, and an offset measured against realtime goes stale the
    moment it is used to write the chip -- the slew then reads as chip error and the
    chip is rewritten every few seconds. MONOTONIC_RAW is the one clock chrony does not
    touch; plain MONOTONIC is slewed along with realtime. The boot id goes with it
    because the raw clock restarts at boot and this file does not.

    NOT a chrony input. chrony's reference is the chip itself -- see coord-rtc's
    cmd_feed for why the reference and the pulse have to be the same clock.
    """
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"{utc_minus_raw_us} {rtt_us} {raw_us} {boot_id}\n")
    os.replace(tmp, path)


def log_fc_time(fh, offset_us, rtt_us, boot_us, unix_us, local_us):
    """Record this box's offset from UTC, as measured through the FC (#11).

    A LOG, NOT A CURRENT VALUE, and nothing on the device reads it. UTC is a label on
    the vehicle's time base rather than the base itself: the base is the DS3234's 1 Hz,
    which every pod and the FC record directly, and chrony's reference is the chip --
    so absolute UTC never enters the alignment. What it is good for is afterwards,
    putting a date on a flight and relating it to things like sun angle, which wants
    "good to tens of milliseconds" and not better.

    WHY TWO MESSAGES AND NOT SYSTEM_TIME ALONE. From ArduPilot's GCS_Common.cpp:

      send_system_time() sends AP::rtc().get_utc_usec() alongside AP_HAL::millis().
      Both are the FC's own clocks, read together, so UTC-minus-FC-monotonic carries
      NO link latency.

      timesync_receive_timestamp_ns() returns _port->receive_time_constraint_us(),
      which is corrected for serial transmission at the UART, on the same monotonic
      clock -- and that clock is what the dataflash log stamps TimeUS with.

    So TIMESYNC measures our clock against the FC's monotonic, and SYSTEM_TIME maps
    that monotonic to UTC for free. Composing them puts only the TIMESYNC residual in
    the answer. Comparing SYSTEM_TIME's UTC against our own receive time instead -- the
    obvious one-liner -- eats the whole one-way delay, which is buffering and
    scheduling rather than the ~100 us of wire time at 1.5 Mbaud.

    rtt_us is carried because a reader should weigh the sample rather than trust it,
    and it is otherwise not measured at all. flight-analysis measured the FC's RTC
    agreeing with its own GPS to ~19 ms rms when healthy, so that is the floor on this
    route regardless of the round trip.
    """
    fh.write(json.dumps({
        "offset_us": offset_us,
        "rtt_us": rtt_us,
        "fc_boot_us": boot_us,
        "fc_unix_us": unix_us,
        "local_us": local_us,
    }) + "\n")
    fh.flush()


def read_line(path):
    """First line of a file, stripped, or "" -- for /etc/host-hostname and boot_id."""
    try:
        with open(path) as f:
            return f.readline().strip()
    except OSError:
        return ""


def session_dir(root):
    """<root>/<node>/<boot-id>, created. The session this boot's data belongs to.

    THE SESSION IS A BOOT and three writers agree on it without any of them handing it
    to the others: vio-tracker, and this router's two logs. Each reads the same boot id
    and the same host name, which is the pattern capture.py and campod-accel already use
    on a campod. docs/flight-data-layout.md has always placed timesync.jsonl and
    vehicle.tlog inside the session; they were being written to the captures ROOT
    instead, which is what made them look like append-only streams that could not be
    collected with the session they belong to (#386).

    The node is the HOST's name: ours in here is an ephemeral docker id, so the stack
    bind-mounts /etc/hostname the way it does for vio-tracker and campod-camera.
    """
    node = read_line("/etc/host-hostname") or socket.gethostname()
    boot = read_line("/proc/sys/kernel/random/boot_id")
    if not boot:
        # No fallback. A made-up session name is one nothing else agrees on, and the
        # whole point is that the writers agree without coordinating.
        raise RuntimeError("cannot read /proc/sys/kernel/random/boot_id")
    d = os.path.join(root, node, boot)
    os.makedirs(d, exist_ok=True)
    return d


def open_timesync_log(path):
    """Append-mode JSONL sink for TIMESYNC exchanges (#167).

    Line-buffered so a power cut keeps everything up to the last exchange, and the
    directory is created if absent so the router runs the same standalone (tests,
    stack-smoke) as it does with /captures mounted.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return open(path, "a", buffering=1)


TLOG_STAMP = struct.Struct(">Q")  # standard tlog: 8-byte BE microseconds, then the frame


def open_tlog(path):
    """Append-mode binary tlog sink. Directory created; unbuffered enough to survive a cut."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return open(path, "ab", buffering=0)


def log_frame(fh, msg, when_us):
    """Append one received MAVLink frame in tlog format.

    Writes the raw bytes as they arrived -- no decode, no projection, so nothing is lost
    and nothing depends on this dialect version being the one that reads it back.
    """
    buf = msg.get_msgbuf()
    if not buf:
        return
    fh.write(TLOG_STAMP.pack(when_us) + bytes(buf))


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
    # GPS-derived UTC for the host's clock discipline (#11). Written at a low rate:
    # SYSTEM_TIME arrives at 2 Hz and nothing needs it that often, and this is a file
    # on the SD card rather than tmpfs.
    fc_time_file = os.environ.get("MAVLINK_FC_TIME_FILE", "/tmp/fc_time")
    fc_time_period_s = float(os.environ.get("MAVLINK_FC_TIME_PERIOD_S", "16"))
    fc_time_written = 0.0
    # Our clock against the FC's monotonic, from TIMESYNC. mono_offset_us is
    # fc_monotonic - ours; rtt_us is how well we know it.
    mono_offset_us = None
    mono_rtt_us = None
    # The same exchange against our CLOCK_MONOTONIC_RAW: fc_monotonic - our raw clock.
    # This is what coord-rtc gets; mono_offset_us stays for the session log (#593).
    raw_offset_us = None
    boot_id = read_line("/proc/sys/kernel/random/boot_id")
    # Monotonic, not time.time(): chrony steps the wall clock, which would make an age
    # computed from it meaningless at exactly the moment it matters.
    mono_at = -1e9
    mono_max_age_s = float(os.environ.get("MAVLINK_TIMESYNC_MAX_AGE_S", "60"))
    # The FC's own monotonic-to-UTC mapping, from SYSTEM_TIME. Latency-free: both
    # fields are read inside the FC at the same instant.
    utc_minus_boot_us = None
    # Outstanding TIMESYNC request we sent, so a reply can be matched and timed.
    ts_req_ts1 = None
    ts_req_raw_ns = None
    ts_req_at = -1e9  # so the first request goes out immediately
    ts_req_period_s = float(os.environ.get("MAVLINK_TIMESYNC_PERIOD_S", "8"))
    armed = False
    write_arm_file(arm_file, armed)
    # #669: whether the FC is talking at all, which the arm file cannot say.
    fc_link_file = os.environ.get("MAVLINK_FC_LINK_FILE", "/tmp/fc_link")
    fc_heartbeats = 0
    write_fc_link(fc_link_file, boot_id, None, fc_heartbeats)

    # #167: FC-clock <-> our-clock pairs, for joining captures to the FC log.
    # Both logs land in THIS BOOT's session directory, so collecting the session
    # collects them. COORD_CAPTURES_DIR is the captures root, not a file path: the
    # router derives <root>/<node>/<boot-id>/ itself, because a full path handed in
    # cannot know the boot id. Empty disables both.
    captures_root = os.environ.get("COORD_CAPTURES_DIR", "")
    if captures_root:
        sess = session_dir(captures_root)
        ts_log = open_timesync_log(os.path.join(sess, "timesync.jsonl"))
        # The UTC label for this session. Same shape and lifetime as timesync.jsonl.
        fc_time_log = open_timesync_log(os.path.join(sess, "fc_time.jsonl"))
        tlog = open_tlog(os.path.join(sess, "vehicle.tlog"))
        print(f"coordinator-mavlink: session {sess}", file=sys.stderr, flush=True)
    else:
        ts_log = open_timesync_log("/dev/null")
        fc_time_log = None
        tlog = None

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

        # Our own TIMESYNC request. The FC sends its own, but those only give a one-way
        # FC-to-us measurement; a request of ours comes back with both our send time and
        # the FC's, so the round trip cancels the one-way delay -- buffers and scheduling,
        # not just the ~100 us of wire time. The FC replies to any tc1 == 0 (ArduPilot's
        # handle_timesync), and echoes ts1 unchanged so the reply can be matched.
        if now_mono - ts_req_at >= ts_req_period_s:
            ts_req_ts1 = time.time_ns()
            ts_req_raw_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
            mav.mav.timesync_send(0, ts_req_ts1)
            ts_req_at = now_mono

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
                if tlog is not None:
                    log_frame(tlog, msg, time.time_ns() // 1000)
                if msg.get_type() == "TIMESYNC" and msg.tc1 != 0:
                    # A reply to a request WE sent. tc1 is the FC's monotonic clock at
                    # its UART, ts1 is our own send timestamp echoed back, so the round
                    # trip cancels the one-way delay we cannot otherwise measure:
                    #   offset = fc - (sent + received)/2
                    if ts_req_ts1 is not None and msg.ts1 == ts_req_ts1:
                        recv_ns = time.time_ns()
                        recv_raw_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
                        rtt_ns = recv_ns - ts_req_ts1
                        mono_offset_us = (msg.tc1 - (ts_req_ts1 + recv_ns) // 2) // 1000
                        raw_offset_us = (msg.tc1
                                         - (ts_req_raw_ns + recv_raw_ns) // 2) // 1000
                        mono_rtt_us = rtt_ns // 1000
                        mono_at = time.monotonic()
                        ts_req_ts1 = None
                elif msg.get_type() == "TIMESYNC" and msg.tc1 == 0:
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
                elif msg.get_type() == "SYSTEM_TIME":
                    # time_unix_usec is 0 until the FC has a GPS fix -- measured at
                    # 1036 of 2124 messages in the 260923 flight, with the first real
                    # value 53 s after FC boot. Zero is not a time.
                    #
                    # Both fields come from inside the FC, so their difference is the
                    # FC's own monotonic-to-UTC mapping with no link latency in it.
                    if msg.time_unix_usec:
                        utc_minus_boot_us = (msg.time_unix_usec
                                             - msg.time_boot_ms * 1000)
                    # Publish only with both halves: UTC from the FC's own pairing and
                    # our offset to the FC's monotonic from TIMESYNC. Either alone is
                    # not an answer.
                    if (utc_minus_boot_us is not None and mono_offset_us is not None
                            and time.monotonic() - mono_at < mono_max_age_s
                            and time.time() - fc_time_written >= fc_time_period_s):
                        local_us = time.time_ns() // 1000
                        boot_us = local_us + mono_offset_us
                        write_fc_time(fc_time_file,
                                      utc_minus_boot_us + raw_offset_us, mono_rtt_us,
                                      time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW) // 1000,
                                      boot_id)
                        if fc_time_log is not None:
                            log_fc_time(fc_time_log,
                                        utc_minus_boot_us + mono_offset_us,
                                        mono_rtt_us, boot_us,
                                        boot_us + utc_minus_boot_us, local_us)
                        fc_time_written = time.time()
                elif (msg.get_type() == "HEARTBEAT"
                      and msg.type != mavutil.mavlink.MAV_TYPE_GCS):
                    # #88: FC arm/disarm -> shared file; sync() the FS at disarm so the
                    # flight's captures survive the power cut that usually follows.
                    # #669: the same heartbeats the arm state comes from, so the panel's
                    # "FC heard" means "an arm would get through".
                    fc_heartbeats += 1
                    write_fc_link(fc_link_file, boot_id, time.monotonic(), fc_heartbeats)
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
