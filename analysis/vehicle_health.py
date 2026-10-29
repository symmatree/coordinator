"""vehicle_health.py -- readers for what the vehicle reports about itself.

Shared by `link-map.ipynb` and `vehicle-health.ipynb`. Readers only: each function turns
one recorded stream into a table and makes no judgement about the values. What each field
means, where that is not obvious, is said beside the reader with the source it came from.

    collectd_series(node_dir)   every collectd CSV under captures/<node>/<session>/collectd/<node>/
    journal_lines(path)         journal.log as a table: time, host, unit, pid, message
    throttle_states(journal)    coord-throttle-log's decoded `vcgencmd get_throttled` lines
    fc_utc_fit(gps)             FC TimeUS -> UTC seconds, from GPS week/ms rows
    counter_rate(df, ...)       per-second rate of a cumulative counter, per instance
    elrs_radio(rad)             RAD decoded per the ExpressLRS receiver's RADIO_STATUS fill
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

GPS_EPOCH_UNIX = 315964800  # 1980-01-06T00:00:00Z
GPS_LEAP_S = 18             # GPS - UTC since 2017-01-01


def collectd_series(node_dir):
    """Every collectd CSV under one node's collectd directory, long form.

    `node_dir` is `captures/<node>/<session>/collectd/<node>`. collectd's csv plugin writes
    `<plugin>-<instance>/<type>-<type_instance>-<YYYY-MM-DD>` with an `epoch,<col>...`
    header; a type with several values (if_octets: rx,tx; disk_octets: read,write) has
    one column per value. Returns columns: plugin, type, column, t (unix s), value.
    Counters (`*_octets`, `*_packets`, `cpu-*`, `ps_cputime` ...) are as written --
    cumulative; `counter_rate` turns them into rates.
    """
    node_dir = Path(node_dir)
    frames = []
    for f in sorted(node_dir.glob("*/*")):
        if not f.is_file():
            continue
        plugin = f.parent.name
        typ = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", f.name)
        try:
            df = pd.read_csv(f)
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
        if "epoch" not in df.columns or df.empty:
            continue
        for col in df.columns:
            if col == "epoch":
                continue
            frames.append(pd.DataFrame({"plugin": plugin, "type": typ, "column": col,
                                        "t": df["epoch"].astype(float),
                                        "value": pd.to_numeric(df[col], errors="coerce")}))
    if not frames:
        return pd.DataFrame(columns=["plugin", "type", "column", "t", "value"])
    return pd.concat(frames, ignore_index=True).sort_values(["plugin", "type", "column", "t"])


# journal.log is `journalctl -o short-iso-precise`: ISO time with offset, host, then
# `unit[pid]: message` or `unit: message`.
_JOURNAL = re.compile(r"^(?P<ts>\S+)\s+(?P<host>\S+)\s+(?P<unit>[^\s\[:]+)(?:\[(?P<pid>\d+)\])?:\s?(?P<msg>.*)$")


def journal_lines(path):
    """journal.log as a table: t (unix s, NaN where the stamp does not parse), host, unit,
    pid, msg. A bundle's journal spans the whole boot, and the kernel's first lines carry
    the wall clock from before chrony set it, so early stamps can be months off."""
    rows = []
    for line in Path(path).read_text(errors="replace").splitlines():
        m = _JOURNAL.match(line)
        if m:
            rows.append(m.groupdict())
    df = pd.DataFrame(rows, columns=["ts", "host", "unit", "pid", "msg"])
    df["t"] = pd.to_datetime(df["ts"], errors="coerce", utc=True, format="ISO8601")
    df["t"] = df["t"].map(lambda x: x.timestamp() if pd.notna(x) else np.nan)
    return df.drop(columns=["ts"])


_THROTTLE = re.compile(r"coord-throttle: (?P<hex>0x[0-9a-fA-F]+) \[(?P<flags>[^\]]*)\]")


def throttle_states(journal):
    """coord-throttle-log's lines (device-sw/cli/coord-throttle-log): the raw
    `vcgencmd get_throttled` value and its decoded flags, one row per run of the timer."""
    rows = []
    for _, r in journal.iterrows():
        m = _THROTTLE.search(r["msg"] or "")
        if m:
            rows.append(dict(t=r["t"], host=r["host"], raw=m["hex"], value=int(m["hex"], 16),
                             flags=m["flags"]))
    return pd.DataFrame(rows, columns=["t", "host", "raw", "value", "flags"])


def fc_utc_fit(gps):
    """Least-squares FC `TimeUS` -> UTC seconds from `GPS` rows that carry a week number.
    Returns (slope, intercept) for utc = slope * TimeUS + intercept, or None with fewer
    than 10 dated rows. A sub-second fit is all the position-tagging here needs;
    time-sync.ipynb is where the clock itself is characterised."""
    if gps is None or gps.empty or "GWk" not in gps:
        return None
    g = gps[(gps["GWk"] > 0) & (gps["Status"] >= 3)]
    if len(g) < 10:
        return None
    utc = GPS_EPOCH_UNIX + g["GWk"].astype(float) * 604800 + g["GMS"].astype(float) / 1000 - GPS_LEAP_S
    slope, intercept = np.polyfit(g["TimeUS"].astype(float), utc, 1)
    return float(slope), float(intercept)


def counter_rate(df, value_col, t_col="t", by=None):
    """Per-second rate of a cumulative counter. A negative step (a counter reset, a
    reboot) is dropped rather than turned into a huge negative rate. `by` splits
    instances (a channel, a port) before differencing."""
    def one(g):
        g = g.sort_values(t_col)
        dt = g[t_col].diff()
        dv = g[value_col].diff()
        out = g[[t_col]].copy()
        out["rate"] = dv / dt
        out.loc[(dv < 0) | (dt <= 0), "rate"] = np.nan
        return out.iloc[1:]
    if by is None:
        return one(df)
    parts = []
    for key, g in df.groupby(by):
        r = one(g)
        r[by] = key
        parts.append(r)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def elrs_radio(rad):
    """`RAD` decoded per what the ExpressLRS receiver puts in RADIO_STATUS.

    ExpressLRS `src/src/rx-serial/SerialMavlink.cpp` (sendQueuedData) fills, at 100 Hz:
      rssi     = uplink_Link_quality * 2.55   -> uplink LQ %, as received on the vehicle
      remrssi  = uplink_RSSI_1                -> minus the uplink RSSI in dBm on radio 1
                                                 (rx_main.cpp: `uplink_RSSI_1 = -rssiDBM`)
      noise    = (uint8) uplink_SNR           -> uplink SNR in dB, two's complement
      txbuf    = free space in the receiver's MAVLink input buffer, %
      remnoise, rxerrors, fixed = 0
    So all of it is the vehicle's receiver hearing the ground. Radio 2's RSSI, which
    radio is active, the downlink and transmit power are in the receiver's link stats
    but not in RADIO_STATUS, so not in this log.
    """
    out = pd.DataFrame({"t_s": rad["t_s"], "TimeUS": rad["TimeUS"]})
    out["uplink_lq_pct"] = rad["RSSI"].astype(float) / 2.55
    out["uplink_rssi1_dbm"] = -rad["RemRSSI"].astype(float)
    snr = rad["Noise"].astype(int)
    out["uplink_snr_db"] = np.where(snr > 127, snr - 256, snr).astype(float)
    out["rx_mavlink_buf_free_pct"] = rad["TxBuf"].astype(float)
    return out


def summarize(s):
    """n, min, median, max of a numeric series -- the figures the JSON reports, nothing
    computed on top of them."""
    s = pd.to_numeric(pd.Series(s), errors="coerce").dropna()
    if s.empty:
        return {"n": 0}
    return {"n": int(s.size), "min": float(s.min()), "median": float(s.median()),
            "max": float(s.max())}
