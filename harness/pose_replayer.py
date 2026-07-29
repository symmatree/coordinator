#!/usr/bin/env python3
"""pose_replayer -- replay a vins_fusion pose stream into the router's socket.

The inverse of ``vio-pose-tap``: instead of binding ``chobits_server`` and
recording, this *sends* ``float[10]`` pose datagrams TO it, so the real
``coordinator-mavlink`` router (which binds that socket) forwards them to a
(fake or real) FC. It is the "recorded replay" input source for the router half
of the batch VIO harness (coordinator #35).

    wire format: contract v1 = float[10] (40 B, quat/pos/vel) or v2 = float[12]
    (48 B, + reset_counter + feature_count). We send whichever the CSV carries, so
    a v2 capture replays reset/health through the router unchanged.

Input formats (auto-detected):
  * vio-pose-tap CSV     -- header ``t_unix,t_mono,qw,...,vz[,reset_counter,feature_count]``
  * vio-pose-tap console -- ``q=(w,x,y,z) p=(x,y,z) v=(x,y,z)`` lines (v1 only, no timestamps)

Pacing: honor the CSV timestamps (scaled by ``--speed``) when present, else send
at ``--rate`` Hz. ``--fast`` sends with no delay at all (bulk / batch mode).

    python3 pose_replayer.py --socket /tmp/chobits_server pos-log.txt
"""

import argparse
import os
import re
import socket
import struct
import sys
import time

POSE_FMT_V1 = "<10f"  # 40 B
POSE_FMT_V2 = "<12f"  # 48 B (+ reset_counter, feature_count)
DEFAULT_SOCKET = os.path.join(
    os.environ.get("COORDINATOR_IPC_DIR", "/var/lib/coordinator/ipc"), "chobits_server"
)

# Matches a vio-pose-tap console line: q=(...) p=(...) v=(...), any float format.
_CONSOLE_RE = re.compile(
    r"q=\(([^)]*)\)\s+p=\(([^)]*)\)\s+v=\(([^)]*)\)"
)


def read_poses(path):
    """Yield (t_mono_or_None, vals) from a CSV or console pose log.

    vals is a 10-float (v1) or 12-float (v2, with reset_counter + feature_count)
    tuple, matching what the CSV carries -- send_pose packs it accordingly.
    """
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith("t_unix"):  # CSV header
                continue
            if line.startswith("q=("):  # console format, no timestamp (v1 only)
                m = _CONSOLE_RE.search(line)
                if not m:
                    continue
                vals = [float(x) for g in m.groups() for x in g.split(",")]
                if len(vals) == 10:
                    yield None, tuple(vals)
                continue
            if "," in line and line[0].isdigit():  # CSV data row
                parts = line.split(",")
                if len(parts) >= 14:  # v2: + reset_counter, feature_count
                    yield float(parts[1]), tuple(float(v) for v in parts[2:14])
                elif len(parts) >= 12:  # v1
                    yield float(parts[1]), tuple(float(v) for v in parts[2:12])
            # anything else (e.g. the "listening on ..." banner) is skipped


def send_pose(sock, target, vals):
    """Send a pose tuple as a datagram. 12 floats -> contract v2, else v1."""
    fmt = POSE_FMT_V2 if len(vals) >= 12 else POSE_FMT_V1
    sock.sendto(struct.pack(fmt, *vals[: struct.calcsize(fmt) // 4]), target)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("logfile", help="pose log to replay (vio-pose-tap CSV or console format)")
    ap.add_argument(
        "--socket", default=DEFAULT_SOCKET, help="target chobits_server path (default: %(default)s)"
    )
    ap.add_argument(
        "--rate", type=float, default=50.0,
        help="send rate in Hz when the log has no timestamps (default: %(default)s)",
    )
    ap.add_argument(
        "--speed", type=float, default=1.0,
        help="replay speed multiplier for timestamped logs (default: 1.0 = real time)",
    )
    ap.add_argument("--fast", action="store_true", help="no pacing at all (bulk/batch mode)")
    ap.add_argument("--quiet", action="store_true", help="do not print progress")
    args = ap.parse_args()

    poses = list(read_poses(args.logfile))
    if not poses:
        sys.exit(f"pose_replayer: no pose samples parsed from {args.logfile}")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    period = 1.0 / args.rate if args.rate > 0 else 0.0

    if not args.quiet:
        print(f"pose_replayer: replaying {len(poses)} samples -> {args.socket}", file=sys.stderr)

    prev_t = None
    sent = 0
    for t_mono, vals in poses:
        try:
            send_pose(sock, args.socket, vals)
        except FileNotFoundError:
            sys.exit(f"pose_replayer: nothing is bound to {args.socket} (is the router running?)")
        sent += 1
        if not args.fast:
            if t_mono is not None and prev_t is not None:
                dt = (t_mono - prev_t) / args.speed
                if dt > 0:
                    time.sleep(dt)
            elif period:
                time.sleep(period)
        prev_t = t_mono

    if not args.quiet:
        print(f"pose_replayer: sent {sent} samples", file=sys.stderr)


if __name__ == "__main__":
    main()
