#!/usr/bin/env python3
"""pose_replayer -- replay a vins_fusion pose stream into the router's socket.

The inverse of ``vio-pose-tap``: instead of binding ``chobits_server`` and
recording, this *sends* ``float[10]`` pose datagrams TO it, so the real
``coordinator-mavlink`` router (which binds that socket) forwards them to a
(fake or real) FC. It is the "recorded replay" input source for the router half
of the batch VIO harness (coordinator #35).

    wire format: 10 little-endian float32 (40 bytes) -- quat(w,x,y,z) pos(x,y,z) vel(x,y,z)

Input formats (auto-detected):
  * vio-pose-tap CSV     -- header ``t_unix,t_mono,qw,...,vz``; paced by ``t_mono``
  * vio-pose-tap console -- ``q=(w,x,y,z) p=(x,y,z) v=(x,y,z)`` lines (no timestamps)

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

POSE_FMT = "<10f"
POSE_SZ = struct.calcsize(POSE_FMT)  # 40
DEFAULT_SOCKET = os.path.join(
    os.environ.get("COORDINATOR_IPC_DIR", "/var/lib/coordinator/ipc"), "chobits_server"
)

# Matches a vio-pose-tap console line: q=(...) p=(...) v=(...), any float format.
_CONSOLE_RE = re.compile(
    r"q=\(([^)]*)\)\s+p=\(([^)]*)\)\s+v=\(([^)]*)\)"
)


def read_poses(path):
    """Yield (t_mono_or_None, (10 floats)) from a CSV or console pose log."""
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith("t_unix"):  # CSV header
                continue
            if line.startswith("q=("):  # console format, no timestamp
                m = _CONSOLE_RE.search(line)
                if not m:
                    continue
                vals = [float(x) for g in m.groups() for x in g.split(",")]
                if len(vals) == 10:
                    yield None, tuple(vals)
                continue
            if "," in line and line[0].isdigit():  # CSV data row
                parts = line.split(",")
                if len(parts) >= 12:
                    t_mono = float(parts[1])
                    yield t_mono, tuple(float(v) for v in parts[2:12])
            # anything else (e.g. the "listening on ..." banner) is skipped


def send_pose(sock, target, vals):
    """Send one 10-float pose tuple as a datagram to the target socket path."""
    sock.sendto(struct.pack(POSE_FMT, *vals), target)


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
