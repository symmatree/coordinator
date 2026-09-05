# RTK corrections delivery path (base station -> NTRIP -> ELRS -> F9P)

How RTCM3 corrections get from the ground base station to the rover's ZED-F9P, the
rates involved, and the leading theory for the failure that keeps recurring (RTK
never coming up): the ELRS TX backpack anchoring its MAVLink stream to the wrong
ground station.

The path, workloads, and rates below are documented state. The failure-cause is a
**leading theory, not a confirmed diagnosis** -- it is consistent with the two cases
we have and with a firmware read, but has not been caught in the act; see the caveats
in that section. This is the reference for the correction path. The physical ground
kit (radio, backpack, IPs, ELRS profiles) is inventoried in fables
`Drones/rekon10/ground-station.md`; the FC-side RTK/serial wiring in
`Drones/rekon10/ardupilot.md`; the under-canopy operational doctrine ("RTCM must
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

- **RTCM3 from the base** (set in tiles [PR #516](https://github.com/symmatree/tiles/pull/516)):
  `1005(10),1074,1084,1094,1124,1230(10)` -- station coords (0.1 Hz), MSM4 for
  GPS/GLONASS/Galileo/BeiDou (~1 Hz), GLONASS code-phase bias (0.1 Hz). Base position
  `40.5323232197 -80.0418241688 327.892`.
- **ELRS telemetry budget** (Rekon10 profile): **333 Hz Full, 1:2 telemetry ratio,
  ~13211 baud** reported on the radio. RTCM shares this uplink budget; the MSM4 set
  above is chosen to fit. Keeping the message set lean matters -- a fatter set can
  starve the link.
- **Backpack MAVLink ports** (default): sends on **14550**, listens on **14555**.
  mavproxy's `udpin` binds **14550**, so the backpack's broadcast lands on mavproxy;
  mavproxy replies to the backpack's `:14555`. (This reply is what anchors the
  backpack -- see below.)

## Leading theory: the backpack anchors to the wrong GCS

**Symptom (observed).** Rover never reaches RTK; GPS Status stalls at 3D/DGPS, RTK
LED never lights, and mavproxy shows only `no link` for the whole flight -- it
received zero MAVLink from the vehicle. Everything upstream (base, caster, mavproxy
NTRIP client) verified healthy. Occurrences:
[#99](https://github.com/symmatree/coordinator/issues/99) (2026-07-12), tiles #664
(2026-07-30, first no-laptop flight; closed -> tracked in #99), and the field notes in
fables `flight-platform-build-log.md` (Flights 1 and later).

**Candidate mechanism, read from the firmware source.** ExpressLRS/Backpack
[`lib/WIFI/devWIFI.cpp`](https://github.com/ExpressLRS/Backpack/blob/1.5.9/lib/WIFI/devWIFI.cpp).
The backpack runs the build in [`firmware/backpack/`](../firmware/backpack/README.md) --
upstream tag **1.5.9** plus our WiFi patch -- so this is the source that runs. The latch
logic (`gcsIP` / `gcsIPSet`, lines 118-119, 582-583, 894-896, 932-933) is byte-identical
between 1.5.9 and master, so the reading below applies to either:

- On boot `gcsIPSet = false`, so the backpack **broadcasts** MAVLink to the subnet
  (`WiFi.broadcastIP()` in STA mode) on the send port -- discovery.
- The **first** UDP packet from any GCS latches it:
  `gcsIP = mavlinkUDP.remoteIP(); gcsIPSet = true;` -- and from then on it **unicasts
  only to that IP**.
- `gcsIPSet` is **never cleared** -- no timeout, no heartbeat-loss reset. It stays
  locked to that first responder until the backpack reboots / WiFi reconnects.

So it is a **first-responder race**. mavproxy (`udpin` -- hears the broadcast, replies
with heartbeats) races any other GCS on the LAN. A Mission Planner or QGroundControl
with UDP auto-connect (which the ELRS docs actively tell people to enable) answers
instantly and can win. If a stray GCS wins, the backpack unicasts to *it* for the
entire session; mavproxy is starved, nothing looks broken (the backpack **is**
connected -- just to the wrong host), and no RTCM reaches the FC.

If it holds, this would unify the two competing field hypotheses recorded separately
(`flight-platform-build-log.md`): "Mission Planner beat mavproxy to the connection"
and "a *radio* power-cycle fixed it, so it's the ELRS uplink stalling, not GCS
contention." Under this mechanism they are the same thing -- a radio/backpack
power-cycle clears `gcsIPSet` and re-runs the race, so "power-cycle fixed it" would
not be evidence against contention, and the stochastic recovery ("sometimes N
power-cycles") is just re-rolling the race. This was already the standing field theory
before the firmware read; the code makes it more plausible, it does not confirm it.

### What would confirm it (not yet in hand)

The two cases are *consistent* with this theory but do not prove it. Still missing:

- **No GCS caught red-handed** -- `ip.gcs` pointing at a non-acebase host during a
  failure is the direct evidence, and it has not been seen. Every sample taken since
  the endpoint became readable shows the expected `10.0.99.14`, including through the
  2026-08-14 link loss -- so that particular failure was *not* this mechanism.
- The predicted recovery (down the offender -> power-cycle -> mavproxy wins) has not
  been run as a deliberate predict-then-confirm.

Until one of those lands this is the leading theory, not a diagnosis. Current thinking
concentrates here; new evidence should update this section.

## Diagnosing it next time (ask the backpack directly)

Under this theory the backpack is not failing -- it is talking to the wrong GCS -- so
its own status endpoint should name that GCS, without any packet capture.
`GET http://boxer-txbp.local.symmatree.com/mavlink` (route `server.on("/mavlink", ...)`,
`WebMAVLinkHandler`) returns:

```json
{ "enabled": true,
  "counters": { "packets_down": N, "packets_up": N, "drops_down": N, "overflows_down": N },
  "ports":    { "listen": 14555, "send": 14550 },
  "ip":       { "gcs": "10.0.99.14" },   // "IP UNSET" == still broadcasting, unlatched
  "protocol": "UDP",
  "link":     { "rssi": -78, "ssid": "...", "bssid": "...", "reconnects": 1, "uptime_ms": 211727 } }
```

The `link` block is our patch's addition, not stock -- its presence is also how you tell
which firmware is on the unit.

- `ip.gcs` would be the offender -- the GCS it anchored to. If it is not acebase's
  address (`10.0.99.14`), that host has the link. `"IP UNSET"` means it hasn't latched yet.
- `counters` need care, and have already produced wrong readings. `drops_down` is one
  global sequence counter across three interleaving MAVLink sources, so per-source gaps
  read as huge losses -- **use `overflows_down`**. And `packets_up` is **not** proof of
  delivery: on 2026-08-12 it climbed steadily at ~6/s through a window when the FC was
  receiving nothing.
- `link.uptime_ms` distinguishes a power-cycle from a link drop across a gap in sampling
  -- compare its delta against wall-clock, not just whether it went backwards.

Record it per flight rather than curling by hand; `bin/backpack-link-watch` (coordinator
[#201](https://github.com/symmatree/coordinator/pull/201)) does that and documents the
traps above in more detail.

## Recovery

If the mechanism above is right, recovery follows from it (and running it is also how
you'd confirm the theory). In the source the target is learned, not settable -- the
firmware latches to whoever answers the discovery broadcast first and holds that lock
until it reboots. So: read `/mavlink`, note `ip.gcs`; if it isn't acebase, take that
host off the link, then power-cycle the backpack so it re-broadcasts and mavproxy can
win the fresh race. A power-cycle without removing the other GCS just re-rolls the
same race, which would explain why it has sometimes taken more than one.

Because the lock can't be pinned in firmware, keeping it from recurring is a network
question -- whether an auto-connecting GCS can reach the backpack's subnet at all --
and it is open: it depends on the site network and hasn't been chosen or tested.

## Related

- Tracking bug: [coordinator#99](https://github.com/symmatree/coordinator/issues/99).
  Closed occurrence: [tiles#664](https://github.com/symmatree/tiles/issues/664).
- Ground kit inventory (radio, backpack, ELRS profiles, IPs): fables
  `Drones/rekon10/ground-station.md`.
- FC-side RTK/serial (F9P on SERIAL2, ELRS serial modes): fables
  `Drones/rekon10/ardupilot.md`; path summary in `Drones/rekon10/flight-platform.md`.
- Under-canopy correction doctrine (why RTCM must flow continuously): fables
  `Drones/rekon10/canopy-ops.md`.
- Backpack firmware: what we build and flash is [`firmware/backpack/`](../firmware/backpack/README.md)
  (tag 1.5.9 + WiFi patch). `~/expresslrs-backpack` is a convenience clone of upstream
  **master** for reading `lib/WIFI/devWIFI.cpp` -- check the tag before citing it as what runs.
