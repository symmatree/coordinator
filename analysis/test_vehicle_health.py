#!/usr/bin/env python3
"""Synthetic tests for vehicle_health.py. No NAS, no pytest: `python3 analysis/test_vehicle_health.py`.

Each pins a way a reader here could be confidently wrong."""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vehicle_health as vh

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


# RAD as the ELRS receiver fills it: rssi = LQ * 2.55, remrssi = -dBm on radio 1, noise =
# uint8 of a signed SNR. Reading noise as unsigned turns -4 dB into 252.
rad = pd.DataFrame({"t_s": [0.0, 1.0], "TimeUS": [0, 1_000_000], "RSSI": [255, 204], "RemRSSI": [46, 90],
                    "Noise": [9, 252], "TxBuf": [100, 20]})
e = vh.elrs_radio(rad)
check("uplink LQ is RSSI / 2.55", np.allclose(e.uplink_lq_pct, [100, 80]), str(e.uplink_lq_pct.tolist()))
check("RemRSSI is minus the dBm", e.uplink_rssi1_dbm.tolist() == [-46, -90])
check("a negative SNR survives the uint8 field", e.uplink_snr_db.tolist() == [9, -4], str(e.uplink_snr_db.tolist()))

# A cumulative counter that resets (a reboot, a channel re-open) must not produce one huge
# negative rate that drags a median or a colour scale.
c = pd.DataFrame({"t": [0, 1, 2, 3, 4], "v": [0, 10, 20, 5, 15]})
r = vh.counter_rate(c, "v")
check("rate is the per-second difference", r.rate.iloc[0] == 10)
check("a counter reset gives no rate, not a negative one", np.isnan(r.rate.iloc[2]) and (r.rate.dropna() >= 0).all())
by = vh.counter_rate(pd.DataFrame({"t": [0, 1, 0, 1], "v": [0, 5, 100, 300], "ch": [1, 1, 2, 2]}), "v", by="ch")
check("instances are differenced separately", sorted(by.rate.tolist()) == [5, 200], str(by.rate.tolist()))

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    (d / "interface-wlan0").mkdir(); (d / "memory").mkdir()
    (d / "interface-wlan0" / "if_octets-2026-10-27").write_text("epoch,rx,tx\n1.0,10,20\n2.0,30,25\n")
    (d / "memory" / "memory-free-2026-10-27").write_text("epoch,value\n1.0,100.5\n")
    (d / "memory" / "memory-used-2026-10-28").write_text("")  # an empty file is skipped, not fatal
    s = vh.collectd_series(d)
    check("a two-value type becomes two columns", set(s[s.plugin == "interface-wlan0"].column) == {"rx", "tx"})
    check("the date suffix is not part of the type", set(s.type) == {"if_octets", "memory-free"}, str(set(s.type)))

    j = d / "journal.log"
    j.write_text("2026-10-27T15:47:09.126480-04:00 coordinator coord-throttle-log[897]: coord-throttle: 0x50005 "
                 "[under-voltage-now throttled-now under-voltage-occurred throttled-occurred]\n"
                 "2026-10-27T15:47:10.000000-04:00 coordinator kernel: mmcblk0: p1 p2\n"
                 "not a journal line\n")
    jl = vh.journal_lines(j)
    check("journal lines parse to unit and message", jl.unit.tolist() == ["coord-throttle-log", "kernel"])
    check("journal time carries the offset", abs(jl.t.iloc[0] - 1793130429.12648) < 1e-3, str(jl.t.iloc[0]))
    th = vh.throttle_states(jl)
    check("throttle value and flags decode", th.value.iloc[0] == 0x50005 and "under-voltage-now" in th["flags"].iloc[0])

# The FC -> UTC fit, from GPS week and ms: 2442 weeks + 244184 s at TimeUS 169716113.
g = pd.DataFrame({"TimeUS": np.arange(20) * 200_000 + 169_716_113, "GWk": 2442,
                  "GMS": np.arange(20) * 200 + 244_184_000, "Status": 4})
fit = vh.fc_utc_fit(g)
utc = fit[0] * 169_716_113 + fit[1]
check("the fit lands GPS time on UTC (leap seconds out)",
      abs(utc - (vh.GPS_EPOCH_UNIX + 2442 * 604800 + 244184 - 18)) < 1e-3, str(utc))
check("too few dated rows gives no fit", vh.fc_utc_fit(g.head(5)) is None)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_vehicle_health: all checks passed")
