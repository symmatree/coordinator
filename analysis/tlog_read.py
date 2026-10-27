"""tlog_read.py -- read MAVLink tlogs as tables, and score what each observer saw.

A tlog is the standard capture format: an 8-byte big-endian microsecond timestamp followed by
one raw MAVLink frame, repeated. The coordinator writes one (`captures/<node>/<session>/
vehicle.tlog`, coordinator-mavlink `log_frame`), mavproxy writes one on the ground. Each is a
lossy channel off the FC; the point of reading them is the comparison, with the FC's own
per-channel counters (`MAV.txp`, dataflash) in the middle.

What this module does and does not do:

- **Dispatches on content, not extension.** A tlog named `*.log` sends pymavlink down the
  DataFlash path and dies on a UnicodeDecodeError (coordinator#192); here the first record is
  checked for a plausible stamp and a MAVLink magic byte, and the file is read as a tlog
  regardless of its suffix. An empty file reads as an empty table.
- **Slices a long file by byte offset** before decoding, by binary search on the stamps, so a
  multi-day monolith (coordinator#192) costs a window's worth of parsing rather than all of it.
- **Decodes only the frame header** -- stamp, sysid, compid, seq, message id/name, length -- so
  every frame counts whether or not the dialect knows its payload. `BAD_DATA` runs (coordinator
  #430) are counted as bytes skipped, not dropped as if nothing was there.
- **Scores per sender**: the 8-bit sequence number is per (sysid, compid), so the frames a
  channel lost are `sum((seq_i - seq_{i-1} - 1) mod 256)` over consecutive frames from that
  sender. A reader cannot see a loss of exactly 256 frames in a row; that is the method's limit.
- **Per message type**: count, rate over the span, and the gap distribution between arrivals,
  so "present, at the advertised rate, with no holes" is three numbers.

CLI: `python3 analysis/tlog_read.py <file.tlog> [--t0 UNIX --t1 UNIX]`.
"""
import argparse
import os
import struct
import sys

import numpy as np
import pandas as pd
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as _dialect

STAMP_BYTES = 8
MAGIC_V1, MAGIC_V2 = 0xFE, 0xFD
# a stamp is plausible between 2000-01-01 and 2100-01-01, in microseconds
STAMP_MIN_US, STAMP_MAX_US = 946_684_800 * 10**6, 4_102_444_800 * 10**6
_MSG_NAMES = {mid: cls.msgname for mid, cls in _dialect.mavlink_map.items()}


def _frame_len(buf, i):
    """Length of the MAVLink frame starting at buf[i], or None if the magic is not there."""
    if i >= len(buf):
        return None
    m = buf[i]
    if m == MAGIC_V2 and i + 10 <= len(buf):
        plen = buf[i + 1]
        incompat = buf[i + 2]
        return 10 + plen + 2 + (13 if incompat & 0x01 else 0)
    if m == MAGIC_V1 and i + 6 <= len(buf):
        return 6 + buf[i + 1] + 2
    return None


def _record_at(buf, i):
    """(stamp_us, frame_len) for a record at i, or None if it does not look like one."""
    if i + STAMP_BYTES >= len(buf):
        return None
    (stamp,) = struct.unpack_from(">Q", buf, i)
    if not (STAMP_MIN_US <= stamp <= STAMP_MAX_US):
        return None
    n = _frame_len(buf, i + STAMP_BYTES)
    if n is None or i + STAMP_BYTES + n > len(buf):
        return None
    return stamp, n


def is_tlog(path):
    """A file whose first record is a plausible stamp followed by a MAVLink magic byte."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return False
    return len(head) >= STAMP_BYTES + 6 and _record_at(head, 0) is not None


def _resync(buf, i):
    """Next offset >= i at which a record parses; the bytes passed over are not a frame."""
    while i < len(buf):
        if _record_at(buf, i) is not None:
            return i
        i += 1
    return len(buf)


def _offset_for(buf, t_us):
    """Byte offset of the first record whose stamp is >= t_us, by binary search on stamps."""
    lo, hi = 0, len(buf)
    while lo < hi:
        mid = (lo + hi) // 2
        j = _resync(buf, mid)
        if j >= len(buf):
            hi = mid
            continue
        if _record_at(buf, j)[0] < t_us:
            lo = j + 1
        else:
            hi = mid
    return _resync(buf, lo)


def read_tlog(path, t0=None, t1=None):
    """Frame table: stamp_s, sysid, compid, seq, msgid, msg, length. t0/t1 are unix seconds.

    Returns (DataFrame, info) with info = dict(bytes, bytes_skipped, frames, t_first, t_last).
    Only the header is decoded, so an unknown message id still counts, as msg "ID<n>".
    """
    with open(path, "rb") as f:
        buf = f.read()
    rows = []
    skipped = 0
    i = _offset_for(buf, int(t0 * 1e6)) if t0 is not None and buf else 0
    stop = int(t1 * 1e6) if t1 is not None else None
    while i < len(buf):
        rec = _record_at(buf, i)
        if rec is None:
            j = _resync(buf, i + 1)
            skipped += j - i
            i = j
            continue
        stamp, n = rec
        if stop is not None and stamp > stop:
            break
        fr = i + STAMP_BYTES
        if buf[fr] == MAGIC_V2:
            seq, sysid, compid = buf[fr + 4], buf[fr + 5], buf[fr + 6]
            msgid = buf[fr + 7] | (buf[fr + 8] << 8) | (buf[fr + 9] << 16)
        else:
            seq, sysid, compid, msgid = buf[fr + 2], buf[fr + 3], buf[fr + 4], buf[fr + 5]
        rows.append((stamp / 1e6, sysid, compid, seq, msgid, _MSG_NAMES.get(msgid, f"ID{msgid}"), n))
        i = fr + n
    df = pd.DataFrame(rows, columns=["stamp_s", "sysid", "compid", "seq", "msgid", "msg", "length"])
    info = dict(bytes=len(buf), bytes_skipped=skipped, frames=len(df),
                t_first=float(df.stamp_s.iloc[0]) if len(df) else None,
                t_last=float(df.stamp_s.iloc[-1]) if len(df) else None)
    return df, info


def seq_loss(df):
    """Per sender: frames seen, frames the sequence numbers say were lost, loss fraction."""
    out = []
    for (sysid, compid), g in df.groupby(["sysid", "compid"], sort=True):
        s = g.seq.values.astype(int)
        lost = int(((np.diff(s) - 1) % 256).sum()) if len(s) > 1 else 0
        out.append(dict(sysid=int(sysid), compid=int(compid), frames=int(len(s)), lost=lost,
                        loss_frac=lost / (lost + len(s)) if len(s) else None))
    return pd.DataFrame(out)


def message_table(df, t0=None, t1=None):
    """Per message type: count, rate over [t0, t1] (default the table's own span), gap stats."""
    if not len(df):
        return pd.DataFrame(columns=["msg", "n", "rate_hz", "gap_median_s", "gap_max_s"])
    span_t0 = df.stamp_s.min() if t0 is None else t0
    span_t1 = df.stamp_s.max() if t1 is None else t1
    span = max(span_t1 - span_t0, 1e-9)
    out = []
    for msg, g in df.groupby("msg", sort=True):
        t = np.sort(g.stamp_s.values)
        gaps = np.diff(t)
        out.append(dict(msg=msg, n=int(len(t)), rate_hz=len(t) / span,
                        gap_median_s=float(np.median(gaps)) if len(gaps) else None,
                        gap_max_s=float(gaps.max()) if len(gaps) else None))
    return pd.DataFrame(out).sort_values("n", ascending=False).reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tlog")
    ap.add_argument("--t0", type=float, help="unix seconds, start of the window")
    ap.add_argument("--t1", type=float, help="unix seconds, end of the window")
    a = ap.parse_args(argv)
    if not os.path.getsize(a.tlog):
        print(f"{a.tlog}: empty file")
        return 0
    if not is_tlog(a.tlog):
        print(f"{a.tlog}: first record is not a stamped MAVLink frame", file=sys.stderr)
        return 1
    df, info = read_tlog(a.tlog, a.t0, a.t1)
    print(info)
    with pd.option_context("display.width", 200, "display.max_rows", 200):
        print(seq_loss(df).to_string(index=False))
        print(message_table(df, a.t0, a.t1).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
