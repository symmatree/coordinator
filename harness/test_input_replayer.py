#!/usr/bin/env python3
"""test_input_replayer -- exercise the estimator-half input replayer (coordinator #35).

No hardware, no vins_fusion: write a synthetic vio-ipc-record fixture (interleaved
chobits_imu + chobits_features frames with realistic wire payloads), bind two AF_UNIX
dgram sockets standing in for the estimator's inputs, replay the fixture into them,
and assert every datagram arrives byte-exact, on the right socket, in t_mono order.

This locks the seam input_replayer owns: fixture decode, socket_id -> target routing,
and raw-payload passthrough. (Pacing is tested with --fast for speed; the timing path
is a sleep, not a contract.)

Reader threads drain the fake input sockets *continuously* while the replay runs --
which is what a live estimator does, and what the replayer's blocking sends require:
AF_UNIX dgram queues are shallow (net.unix.max_dgram_qlen, typically 10), so a --fast
replay of more than a queueful would otherwise block on backpressure with no consumer.

    python3 test_input_replayer.py
"""

import os
import struct
import sys
import tempfile
import threading

from input_replayer import decode_frames, load_manifest, replay, resolve_targets

FRAME = struct.Struct("<ddHI")


def write_fixture(path, frames, socket_paths):
    """Write frames [(t_mono, t_unix, sid, payload), ...] + manifest, like vio-ipc-record."""
    import json
    with open(path, "wb") as out:
        for t_mono, t_unix, sid, payload in frames:
            out.write(FRAME.pack(t_mono, t_unix, sid, len(payload)))
            out.write(payload)
    with open(path + ".json", "w") as man:
        json.dump({"version": 1, "sockets": dict(enumerate(socket_paths))}, man)


def make_imu(t_dev):
    # 7 doubles: t_dev, ax,ay,az, gx,gy,gz -- the deployed chobits_imu payload (56 B).
    return struct.pack("<7d", t_dev, 0.1, 0.2, 9.8, 0.01, -0.02, 0.03)


def make_features(n, fts):
    # count + features_ts, then n * 13 doubles (deployed stride) -- distinct per feature.
    body = struct.pack("<2d", float(n), fts)
    for i in range(n):
        body += struct.pack("<13d", *[float(i * 100 + j) for j in range(13)])
    return body


def _settle(received, expected, key=None, timeout=2.0):
    """Wait until the drained datagram count reaches `expected` (total, or for `key`)."""
    import time
    deadline = time.monotonic() + timeout

    def count():
        if key is not None:
            return len(received[key])
        return sum(len(v) for v in received.values())

    while count() < expected and time.monotonic() < deadline:
        time.sleep(0.02)


def main():
    import socket as _socket

    tmp = tempfile.mkdtemp(prefix="input_replayer_test_")
    fixture = os.path.join(tmp, "synthetic.feat")

    # Recorded paths mimic the Pi's absolute IPC dir; the replayer must remap them
    # to our temp dir by basename -- exactly the Pi-capture -> container-replay case.
    rec_imu = "/var/lib/coordinator/ipc/chobits_imu"
    rec_feat = "/var/lib/coordinator/ipc/chobits_features"

    # Interleave IMU (~fast) and feature (~slow) frames, and deliberately write ONE
    # pair out of t_mono order to prove replay() re-sorts before sending.
    frames = []
    t = 1000.0
    for k in range(20):
        frames.append((t, t + 0.5, 0, make_imu(t)))          # sid 0 = imu
        if k % 4 == 0:
            frames.append((t + 0.005, t + 0.505, 1, make_features(30 + k, t + 0.005)))
        t += 0.01
    # swap two adjacent IMU frames so the on-disk order != t_mono order
    frames[2], frames[4] = frames[4], frames[2]

    write_fixture(fixture, frames, [rec_imu, rec_feat])

    # Bind fake estimator input sockets in the temp dir, and drain each with a reader
    # thread (a live vins consumes continuously; without that, blocking sends deadlock
    # once the shallow dgram queue fills).
    targets_dir = tmp
    recv_socks = {}
    received = {"chobits_imu": [], "chobits_features": []}
    stop = threading.Event()

    def drain(name, s):
        s.settimeout(0.1)
        while not stop.is_set():
            try:
                received[name].append(s.recv(65536))
            except _socket.timeout:
                continue
            except OSError:
                break

    readers = []
    for name in ("chobits_imu", "chobits_features"):
        p = os.path.join(targets_dir, name)
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
        s.bind(p)
        recv_socks[name] = s
        t_r = threading.Thread(target=drain, args=(name, s), daemon=True)
        t_r.start()
        readers.append(t_r)

    ok = True
    errs = []
    try:
        # --- decode + routing ---
        manifest = load_manifest(fixture)
        assert manifest == {0: rec_imu, 1: rec_feat}, f"manifest map wrong: {manifest}"
        targets = resolve_targets(manifest, targets_dir, {})
        assert targets[0] == os.path.join(targets_dir, "chobits_imu"), targets
        assert targets[1] == os.path.join(targets_dir, "chobits_features"), targets

        decoded = list(decode_frames(fixture))
        assert len(decoded) == len(frames), f"decoded {len(decoded)} != wrote {len(frames)}"

        # --- replay (fast: no sleeps) ---
        counts = replay(decoded, targets, fast=True, quiet=True)
        n_imu = sum(1 for f in frames if f[2] == 0)
        n_feat = sum(1 for f in frames if f[2] == 1)
        assert counts.get(0) == n_imu, f"imu sent {counts.get(0)} != {n_imu}"
        assert counts.get(1) == n_feat, f"feat sent {counts.get(1)} != {n_feat}"

        # --- byte-exact, correct-socket receipt ---
        # Let the reader threads catch up, then snapshot what the "estimator" saw.
        _settle(received, expected=len(frames))
        want = {"chobits_imu": [], "chobits_features": []}
        for _tm, _tu, sid, payload in sorted(frames, key=lambda f: f[0]):
            want["chobits_imu" if sid == 0 else "chobits_features"].append(payload)

        for name in ("chobits_imu", "chobits_features"):
            got = received[name]
            if got != want[name]:
                ok = False
                errs.append(f"{name}: got {len(got)} datagrams, want {len(want[name])}"
                            + ("" if len(got) == len(want[name]) else " (count mismatch)")
                            + ("" if got == want[name] else " (byte mismatch)"))
            else:
                print(f"  {name}: ok ({len(got)} datagrams, byte-exact, in t_mono order)")

        # --- unmapped socket_id is skipped, not fatal ---
        before = len(received["chobits_imu"])
        partial = replay(decoded, {0: targets[0]}, fast=True, quiet=True)
        assert 1 not in partial, "unmapped socket_id should be skipped"
        _settle(received, expected=before + sum(1 for f in frames if f[2] == 0),
                key="chobits_imu")
        print("  unmapped socket_id: ok (skipped, no crash)")

    except AssertionError as e:
        ok = False
        errs.append(str(e))
    finally:
        stop.set()
        for t_r in readers:
            t_r.join(timeout=1.0)
        for s in recv_socks.values():
            s.close()

    for e in errs:
        print(f"  FAIL -- {e}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
