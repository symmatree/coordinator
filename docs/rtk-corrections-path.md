# RTK corrections delivery path (base station -> NTRIP -> ELRS -> F9P)

How RTCM3 corrections get from the ground base station to the rover's ZED-F9P, the
rates involved, and what has actually been observed to break on the way: the ELRS TX
backpack's WiFi association, and its coverage once the operator walks away from the
house.

The path, workloads, and rates below are documented state. The failure section
distinguishes what was observed from what was read in firmware. This is the reference
for the correction path. The physical ground
kit (radio, backpack, IPs, ELRS profiles) is inventoried in
[`rekon10/ground-station.md`](rekon10/ground-station.md); the FC-side RTK/serial wiring in
[`rekon10/ardupilot.md`](rekon10/ardupilot.md); the under-canopy operational doctrine ("RTCM must
flow continuously") in `Drones/rekon10/canopy-ops.md`. Those link here for the
network story rather than restating it.

## The path, hop by hop

```
ZED-F9P base (ttyACM0)                              [ tiles cluster, node acebase ]
   -> str2str  (serial -> tcp 127.0.0.1:5015)
   -> str2str  (tcp 5015 -> local NTRIP caster :2101/ATTIC)         rtkbase pod (ntrip ns)
   -> NTRIP caster  ntrip.tiles.symmatree.com:2101/ATTIC
   -> mavproxy ntrip module  (NTRIP client, sendalllinks)           mavproxy pod (mavproxy ns)
   -> mavproxy MAVLink link  (udpin 0.0.0.0:14550, GCS sysid 255/190)
   === house WiFi ===
   -> boxer-txbp ELRS backpack  (UDP; 10.0.6.120)
   -> ELRS RF uplink  (2.4 GHz, 333 Hz Full, 1:2 telem)
   -> ArduPilot FC  (GPS_RTCM_DATA)
   -> ZED-F9P rover  (SERIAL2)  -> RTK Float/Fixed
```

Workloads that implement it (in **tiles**):

| Hop | Repo path | Notes |
|-----|-----------|-------|
| Base station + local caster | `tanka/environments/ntrip/`, `containers/rtkbase/` | RTKBase; `str2str` chain off the ZED-F9P base on `acebase`. Serves `ATTIC`. |
| Correction injector | `tanka/environments/mavproxy/`, `containers/mavproxy/` | MAVProxy on `acebase`, `hostNetwork`, `udpin:0.0.0.0:14550`; NTRIP client -> injects `GPS_RTCM_DATA` on all MAVLink links. |
| Air link | ELRS TX backpack (`boxer-txbp`) | Bridges house WiFi UDP <-> ELRS RF. **Not a cluster workload** -- firmware on the radio; see the failure mode below. |

Endpoints: caster `ntrip.tiles.symmatree.com:2101/ATTIC`, Mission Planner TCP
`mavproxy.tiles.symmatree.com:5760`, backpack `http://boxer-txbp.local.symmatree.com`
(`10.0.6.120`). Both tiles hostnames resolve to 10.x on the site LAN.

## Rates and message set

- **RTCM3 from the base**, currently `1005(10),1074,1094` -- station coords (0.1 Hz) and MSM4 for
  **GPS and Galileo only** (~1 Hz). Base position `40.5323232197 -80.0418241688 327.892`. The set
  is `local_ntripc_msg` in tiles `tanka/environments/ntrip/settings.conf`, which is the source of
  truth for this line; check it rather than trusting this doc's copy.
  **This was cut on purpose** (tiles `62beb29`, the experiment in
  [#199](https://github.com/symmatree/coordinator/issues/199)): it was
  `1005(10),1074,1084,1094,1124,1230(10)` -- adding GLONASS (1084 + its 1230 biases) and BeiDou
  (1124) -- measured at 569 B/s payload / ~632 B/s wire against a ~735 B/s uplink budget, i.e.
  77-86% utilised. Dropping the two takes it to ~333 B/s. **The revert is documented in that file**
  and should be taken if RTK Fixed does not improve: the constellations are worth more than the
  headroom if the uplink is not the constraint.
- **ELRS telemetry budget** (Rekon10 profile): **333 Hz Full, 1:2 telemetry ratio,
  ~13211 baud** reported on the radio. The MAVLink uplink at that rate is **735 B/s with
  RC** (1470 B/s downlink) per the
  [ExpressLRS MAVLink page](https://www.expresslrs.org/software/mavlink/) -- the figure
  coordinator#199 carried as second-hand, now read from the source. RTCM shares this
  uplink budget; the MSM4 set above is chosen to fit (the full six-message set was 569 B/s
  payload, ~632 B/s on the wire, 86% of budget). Keeping the message set lean matters -- a
  fatter set can starve the link.
- **Backpack MAVLink ports** (default): sends on **14550**, listens on **14555**.
  mavproxy's `udpin` binds **14550**, so the backpack's broadcast lands on mavproxy;
  mavproxy replies to the backpack's `:14555`. (This reply is what anchors the
  backpack -- see below.)

## What breaks, as observed

The failure this doc was first written around -- rover never reaches RTK, mavproxy shows
`no link` for the whole flight, everything upstream verified healthy
([#99](https://github.com/symmatree/coordinator/issues/99) 2026-07-12, tiles#664
2026-07-30) -- has been seen on the backpack's own status endpoint since, and it is
**the backpack's WiFi association**, not where its MAVLink goes once associated.

**1. Association, observed 2026-08-03 (#99).** The backpack had fallen back to its own AP
(an `ExpressLRS TX Backpack` SSID visible in the yard), acebase could not reach it, and
its ARP entry was stale. From the 1.5.9 source: a `WL_CONNECTION_LOST` went straight to AP
fallback with no reconnect, and WiFi power-save was left on. The build in
[`firmware/backpack/`](../firmware/backpack/README.md) (upstream 1.5.9 plus our patch,
#173/#204) reconnects instead of falling back, disables power-save, and exposes the link
state on `/mavlink`. The reconnect was seen working on 2026-08-12 (`reconnects` incremented
while still in STA). It is reduced, not gone: on 2026-09-23 the backpack rebooted
mid-session (`uptime_ms` reset, counters reset) and re-associated 23 s before arm.

**2. Coverage, observed 2026-08-14 (#196, 2026-09-08 comment).** Walking into the woods,
backpack RSSI went -72 -> -87 dBm, it stopped answering at FC t~137 s and never came back;
`GPA.RTCMFU` stopped advancing across the deep-woods window; RTK coasted ~40 s on the last
corrections and collapsed. `overflows_down` was 0 throughout, so it was not congestion. This
is the one in-flight loss of corrections in the record. (The 2026-08-12 "RTCM lost between
mavproxy and the FC" finding was retracted on #195: `RTCMFU` counts only fragmented RTCM,
and the byte stream was continuous.)

**3. The GCS latch: real in the source, never caught, easy to avoid.** The backpack
broadcasts MAVLink until the first UDP packet from any GCS arrives, then unicasts to that
address only, and `gcsIPSet` is never cleared until it reboots
([`lib/WIFI/devWIFI.cpp`](https://github.com/ExpressLRS/Backpack/blob/1.5.9/lib/WIFI/devWIFI.cpp),
byte-identical in 1.5.9 and master). So a Mission Planner with UDP auto-connect on the
same subnet could capture the stream. Every `ip.gcs` sample taken since the endpoint became
readable has shown the mavproxy host, including through the 2026-08-14 loss and across the
whole 2026-09-23 armed window (Mimir, `backpack_mavlink_gcs_info`). The avoidance is not
running another GCS against the backpack's subnet while flying. It was never a major
problem; it stays here so nobody re-derives it from the source.

## Reading the backpack's status endpoint

`GET http://boxer-txbp.local.symmatree.com/mavlink` (route `server.on("/mavlink", ...)`,
`WebMAVLinkHandler`) returns:

```json
{ "enabled": true,
  "counters": { "packets_down": N, "packets_up": N, "drops_down": N, "overflows_down": N },
  "ports":    { "listen": 14555, "send": 14550 },
  "ip":       { "gcs": "10.0.99.14" },   // a sample, not a constant -- see below.
                                        // "IP UNSET" == still broadcasting, unlatched
  "protocol": "UDP",
  "link":     { "rssi": -78, "ssid": "...", "bssid": "...", "reconnects": 1, "uptime_ms": 211727 } }
```

The `link` block is our patch's addition, not stock -- its presence is also how you tell
which firmware is on the unit.

- `ip.gcs` is the GCS it latched to. It should be the address of whichever host is running
  mavproxy. **Resolve that address rather than comparing against a remembered one:**
  acebase's is DHCP-assigned and has changed. `"IP UNSET"` means it hasn't latched yet.
- `link.rssi`, `link.bssid` and `link.reconnects` are the association story. `reconnects`
  lands at 1 on a clean boot; only increments above that are link losses.
- `counters` need care, and have already produced wrong readings. `drops_down` is one
  global sequence counter across three interleaving MAVLink sources, so per-source gaps
  read as huge losses -- **use `overflows_down`**. And `packets_up` is **not** proof of
  delivery: on 2026-08-12 it climbed steadily at ~6/s through a window when the FC was
  receiving nothing.
- `link.uptime_ms` distinguishes a power-cycle from a link drop across a gap in sampling
  -- compare its delta against wall-clock, not just whether it went backwards.

Record it per flight rather than curling by hand; `analysis/tools/backpack-link-watch` (coordinator
[#201](https://github.com/symmatree/coordinator/pull/201)) does that and documents the
traps above in more detail.

## Recovery

Read `/mavlink` first, or the `job="backpack"` series in Mimir, before power-cycling anything.

- **Not answering at all**, or an `ExpressLRS TX Backpack` AP is visible: it is off the house
  WiFi. Power-cycle the Boxer; the patched firmware should then associate and stay. Entering
  the home WiFi credentials at flash time has been more reliable than setting them at runtime.
- **Answering, `ip.gcs` is not the mavproxy host:** the latch went elsewhere. Take that host
  off the link and power-cycle the backpack so it re-broadcasts. Not yet seen in practice.
- **Answering, `ip.gcs` correct, RSSI falling:** that is coverage, and there is nothing to
  reset. The dedicated link below is the answer to this one.

The backpack's Telem mode must be **WiFi** for any of this to apply; a reflash resets it to
Off, and "Enable Backpack WiFi" in the Lua is the config/update session, not the bridge.

## Direction (2026-10-27): a dedicated corrections link

The owner's current direction is to take the handheld radio out of this path: **mLRS at
2.4 GHz** on a RadioMaster Ranger in the attic, fed MAVLink over serial from the attic host
(acebase today; a PoE ESP32 doing UART-to-TCP is planned), to a second ELRS-hardware receiver
flashed mLRS on the FC's unused **SERIAL7**, carrying RTCM only. The WiFi hop, the backpack
firmware and the GCS latch above all leave the corrections path. 915 MHz stays open only if it
measures better out of the attic; the Ranger is the one standalone-capable module on hand.
It is gated on an attic-to-field RSSI survey with the Boxer. The case, the numbers (mLRS
31 Hz mode = 2000 B/s uplink) and the option-1 steelman are in
[coordinator#209](https://github.com/symmatree/coordinator/issues/209#issuecomment-6430447924).
This addresses the 260814 coverage failure (coordinator#196, 2026-09-08 comment); it does not
address Float-not-Fixed, which stays with coordinator#195.

## Related

- Tracking bug: [coordinator#99](https://github.com/symmatree/coordinator/issues/99).
  Closed occurrence: [tiles#664](https://github.com/symmatree/tiles/issues/664).
- Ground kit inventory (radio, backpack, ELRS profiles, IPs):
  [`rekon10/ground-station.md`](rekon10/ground-station.md).
- FC-side RTK/serial (F9P on SERIAL2, ELRS serial modes):
  [`rekon10/ardupilot.md`](rekon10/ardupilot.md); path summary in [`rekon10/flight-platform.md`](rekon10/flight-platform.md).
- Under-canopy correction doctrine (why RTCM must flow continuously):
  [`rekon10/canopy-ops.md`](rekon10/canopy-ops.md).
- Backpack firmware: what we build and flash is [`firmware/backpack/`](../firmware/backpack/README.md)
  (tag 1.5.9 + WiFi patch). `~/expresslrs-backpack` is a convenience clone of upstream
  **master** for reading `lib/WIFI/devWIFI.cpp` -- check the tag before citing it as what runs.
