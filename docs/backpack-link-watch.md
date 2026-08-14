# backpack-link-watch -- record the ELRS backpack's WiFi link health during a flight session

[`bin/backpack-link-watch`](../bin/backpack-link-watch) polls the ELRS TX backpack's
`GET /mavlink` endpoint and records it, so a flight session has ground-link data on the same
timeline as the FC log and the coordinator capture.

The endpoint exists because of the WiFi-robustness patch in
[`firmware/backpack/`](../firmware/backpack/README.md) -- stock firmware reports RF-link stats
but nothing about the backpack's own WiFi. It is the only view we have of that hop.

## Run it

```sh
# from anywhere with network reach to the backpack -- it does not need to run on the radio
OUT=~/datasets/flights/rekon10/260814-hover/ground/backpack-link.jsonl bin/backpack-link-watch
```

| env | default | |
|---|---|---|
| `URL` | `http://10.0.6.120/mavlink` | also `http://boxer-txbp.local.symmatree.com/mavlink` |
| `OUT` | `./backpack-link.jsonl` | every sample, JSONL |
| `INTERVAL` | `5` | seconds |

**Start it before the first flight of a session.** On 2026-08-12 it was started four minutes
late, and the window where RTK should have been resolving to Fixed has no link data at all --
which is why the WiFi question could not be settled from that flight
([#195](https://github.com/symmatree/coordinator/issues/195)).

It is safe to leave running: stdout carries only events worth reacting to (association,
reachability loss and recovery, reconnect-counter moves, backpack reboot, RSSI moves of
>= 6 dBm, and `ip.gcs` latching somewhere unexpected). Everything else goes to the file.

## Output

One JSON object per poll: `{"t": "<UTC>", "ok": true, "d": {<the endpoint payload>}}`, or
`{"t": ..., "ok": false}` when it does not answer.

Fields that matter, and how to read them:

- **`link.rssi`** -- the backpack's signal to the AP. Measured -63 to -95 dBm on 2026-08-12.
- **`link.reconnects`** -- **misnamed; read it as "connections".** From
  `firmware/backpack/patches/wifi-robustness.patch` it starts at 0 and increments on
  `WL_CONNECTION_LOST` *or* `WL_DISCONNECTED`, and the ESP reports `WL_DISCONNECTED` while
  still coming up -- so a clean boot lands at **1, never 0**. Only increments *above* the
  post-boot value are real link losses.
- **`link.bssid`** -- which AP radio. The backpack has **no roaming logic whatsoever** (no
  RSSI awareness, no BSSID pinning), so it associates once and stays until the link drops;
  a BSSID change means it re-associated.
- **`counters.packets_up`** -- packets forwarded toward the aircraft. **Not proof of
  delivery**: on 2026-08-12 this climbed steadily at ~6/s through a window when the FC was
  receiving nothing.
- **`counters.drops_down`** -- **meaningless.** The backpack keeps one global sequence counter
  across three interleaving MAVLink sources, so per-source gaps read as huge drop counts.
  Use `overflows_down` instead.
- **`ip.gcs`** -- which GCS it latched to; expected `10.0.99.14` (mavproxy, hostNetwork on
  acebase).

## Where the output belongs

Alongside the flight it covers, in the `ground/` directory next to the FC log:

```
flights/rekon10/<flight>/ground/backpack-link.jsonl
```

Note `ground/` is not yet part of the canonical tree in
[`docs/flight-data-layout.md`](flight-data-layout.md), which defines only the FC `.bin`,
`captures/`, and `derived/`. Ground-side capture is immutable source and needs a defined slot
-- see [#137](https://github.com/symmatree/coordinator/issues/137).

## This should not stay a script

[#190](https://github.com/symmatree/coordinator/issues/190) tracks scraping this endpoint into
Mimir via Alloy on a ~30 s cadence, which is the right home: always-on, dashboarded, and not
dependent on somebody remembering to start a poller. Until that lands, this script is the
stopgap.
