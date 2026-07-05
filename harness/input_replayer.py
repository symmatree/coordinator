#!/usr/bin/env python3
"""input_replayer -- replay a vio-ipc-record fixture into the real vins_fusion.

The estimator half of the batch VIO harness (coordinator #35), and the inverse of
``pose_replayer.py``: where pose_replayer sends pose *output* into the router,
this sends the estimator *inputs* -- the recorded ``chobits_imu`` + ``chobits_features``
datagrams -- back into a running ``coordinator-vio-estimator`` (real ``vins_fusion``),
which then emits pose on ``chobits_server``. Tap that with ``vio-pose-tap`` to get a
pose CSV: post-hoc VINS pose regenerated from a captured flight's raw inputs.

    tracker capture --[vio-ipc-record]--> fixture.feat (+ .json)
    fixture.feat --[THIS]--> chobits_imu / chobits_features --> [real vins_fusion] --> chobits_server

Fixture format (from vio-ipc-record, manifest version 1): a JSON sidecar
``<fixture>.json`` mapping socket_id -> recorded socket path, plus framed binary
records in ``<fixture>``: per datagram

    <double t_mono><double t_unix><uint16 socket_id><uint32 length><length bytes>

little-endian. Frames are recorded in arrival order; we re-sort by ``t_mono`` (their
true send order across sockets) and **re-send the raw payload bytes verbatim** -- so
the 13-vs-14-double feature-stride question is moot: vins parses its own bytes.

Targets. Each recorded socket_id maps, by *basename*, to a socket the estimator has
bound under ``--ipc-dir`` (default ``$COORDINATOR_IPC_DIR`` or ``/tmp`` -- the
estimator container's mount). So a fixture recorded on the Pi at
``/var/lib/coordinator/ipc/chobits_imu`` replays into ``/tmp/chobits_imu`` where the
containerized vins is listening. Override any mapping with ``--map NAME=PATH``.

Pacing. Honor the recorded inter-frame ``t_mono`` deltas (scaled by ``--speed``);
``--fast`` sends with no delay (bulk mode). For deterministic pose, run vins with
``multiple_thread: 0`` -- otherwise its solver threads make the output non-reproducible
regardless of how faithfully we pace the input.

Backpressure. Sends are *blocking* and lossless on purpose: AF_UNIX dgram queues are
shallow (``net.unix.max_dgram_qlen``, typically 10), so if the estimator can't keep up,
``sendto`` blocks until it drains rather than dropping a packet vins needs. With
``multiple_thread: 0`` vins is slower, so even ``--fast`` is effectively gated by how
fast vins consumes -- that is the correct behavior for a faithful, drop-free replay.

    # estimator up (bench/flight profile, multiple_thread:0), pose-tap on chobits_server, then:
    python3 input_replayer.py ~/captures/wave-20260705-112443.feat
"""

import argparse
import json
import os
import socket
import struct
import sys
import time
from pathlib import Path

FRAME = struct.Struct("<ddHI")  # t_mono, t_unix, socket_id, payload length
DEFAULT_IPC_DIR = os.environ.get("COORDINATOR_IPC_DIR", "/tmp")


def decode_frames(path):
    """Yield (t_mono, t_unix, socket_id, payload_bytes) for every frame in a fixture.

    Raw and lean by design -- no pandas, no per-socket decode. The payload bytes are
    passed through untouched so the replay is byte-exact regardless of packet schema.
    """
    data = Path(path).read_bytes()
    n = len(data)
    off = 0
    while off + FRAME.size <= n:
        t_mono, t_unix, sid, ln = FRAME.unpack_from(data, off)
        off += FRAME.size
        if off + ln > n:  # truncated tail (e.g. recorder killed mid-write) -- stop cleanly
            print(f"input_replayer: truncated frame at offset {off} ({ln}B wanted, "
                  f"{n - off}B left) -- stopping", file=sys.stderr)
            break
        yield t_mono, t_unix, sid, data[off:off + ln]
        off += ln


def load_manifest(path):
    """Return the socket_id(int) -> recorded-path map from the fixture's .json sidecar."""
    manifest = json.loads(Path(str(path) + ".json").read_text())
    return {int(k): v for k, v in manifest["sockets"].items()}


def resolve_targets(sockets, ipc_dir, overrides):
    """Map each recorded socket_id to the target path to replay it into.

    By default: <ipc_dir>/<basename of recorded path>. ``overrides`` is a dict of
    {basename: path} (from --map) that wins over the default.
    """
    targets = {}
    for sid, recorded in sockets.items():
        name = os.path.basename(recorded)
        targets[sid] = overrides.get(name, os.path.join(ipc_dir, name))
    return targets


def replay(frames, targets, speed=1.0, fast=False, quiet=False, sock=None):
    """Send each frame's payload to its target socket, paced by recorded t_mono.

    ``frames`` is any iterable of (t_mono, t_unix, socket_id, payload). It is sorted
    by t_mono here (across sockets) so interleaving matches the original send order.
    Returns per-socket_id sent counts.
    """
    frames = sorted(frames, key=lambda f: f[0])
    if sock is None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    counts = {}
    prev_t = None
    for t_mono, _t_unix, sid, payload in frames:
        target = targets.get(sid)
        if target is None:  # a socket_id with no target mapping -- skip, don't crash
            continue
        if not fast and prev_t is not None:
            dt = (t_mono - prev_t) / speed
            if dt > 0:
                time.sleep(dt)
        try:
            sock.sendto(payload, target)
        except FileNotFoundError:
            sys.exit(f"input_replayer: nothing is bound to {target} "
                     f"(is vio-estimator running with the input sockets bound?)")
        counts[sid] = counts.get(sid, 0) + 1
        prev_t = t_mono
    if not quiet:
        summary = ", ".join(f"{os.path.basename(targets[s])}={c}" for s, c in sorted(counts.items()))
        print(f"input_replayer: sent {summary}", file=sys.stderr)
    return counts


def parse_map(items):
    """Parse repeated --map NAME=PATH into {NAME: PATH}."""
    out = {}
    for item in items or []:
        if "=" not in item:
            sys.exit(f"input_replayer: --map expects NAME=PATH, got {item!r}")
        name, path = item.split("=", 1)
        out[name] = path
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("fixture", help="vio-ipc-record binary fixture (manifest at <fixture>.json)")
    ap.add_argument(
        "--ipc-dir", default=DEFAULT_IPC_DIR,
        help="dir holding the estimator's bound sockets (default: %(default)s)",
    )
    ap.add_argument(
        "--map", action="append", dest="maps", metavar="NAME=PATH",
        help="override target for a socket by basename, e.g. chobits_imu=/tmp/chobits_imu (repeatable)",
    )
    ap.add_argument(
        "--speed", type=float, default=1.0,
        help="replay speed multiplier (default: 1.0 = real time; >1 faster)",
    )
    ap.add_argument("--fast", action="store_true", help="no pacing at all (bulk/batch mode)")
    ap.add_argument("--quiet", action="store_true", help="do not print the summary")
    args = ap.parse_args()

    if not os.path.exists(args.fixture):
        sys.exit(f"input_replayer: no such fixture {args.fixture}")

    sockets = load_manifest(args.fixture)
    targets = resolve_targets(sockets, args.ipc_dir, parse_map(args.maps))
    if not args.quiet:
        for sid, tgt in sorted(targets.items()):
            print(f"input_replayer: socket {sid} ({os.path.basename(sockets[sid])}) -> {tgt}",
                  file=sys.stderr)

    frames = list(decode_frames(args.fixture))
    if not frames:
        sys.exit(f"input_replayer: no frames decoded from {args.fixture}")
    if not args.quiet:
        print(f"input_replayer: replaying {len(frames)} frames "
              f"({'no pacing' if args.fast else f'{args.speed}x'})", file=sys.stderr)
    replay(frames, targets, speed=args.speed, fast=args.fast, quiet=args.quiet)


if __name__ == "__main__":
    main()
