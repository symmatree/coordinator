# RTK corrections delivery path (base station -> NTRIP -> ELRS -> F9P)

How RTCM3 corrections get from the ground base station to the rover's ZED-F9P, the
rates involved, and the one failure mode that has bitten us repeatedly: the ELRS TX
backpack silently anchoring its MAVLink stream to the wrong ground station.

This is the single source of truth for the correction path. The physical ground
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

## Failure mode: the backpack anchors to the wrong GCS

**Symptom.** Rover never reaches RTK; GPS Status stalls at 3D/DGPS, RTK LED never
lights, and mavproxy shows only `no link` for the whole flight -- it received zero
MAVLink from the vehicle. Everything upstream (base, caster, mavproxy NTRIP client)
is healthy. Occurrences: [#99](https://github.com/symmatree/coordinator/issues/99)
(2026-07-12), tiles #664 (2026-07-30, first no-laptop flight; closed -> tracked in #99),
and the field notes in fables `flight-platform-build-log.md` (Flights 1 and later).

**Mechanism (confirmed from firmware).** ExpressLRS/Backpack
[`lib/WIFI/devWIFI.cpp`](https://github.com/ExpressLRS/Backpack/blob/master/lib/WIFI/devWIFI.cpp)
(local clone: `~/expresslrs-backpack`):

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

This resolves the two competing field hypotheses that were recorded separately
(`flight-platform-build-log.md`): "Mission Planner beat mavproxy to the connection"
vs. "a *radio* power-cycle fixed it, so it's the ELRS uplink stalling, not GCS
contention." They are the same thing. A radio/backpack power-cycle is precisely how
you recover **from** the stolen lock -- it clears `gcsIPSet` and re-runs the race --
so "power-cycle fixed it" was never evidence against contention. It is also why the
recovery looks stochastic ("sometimes N power-cycles"): each reboot just re-rolls the
race; a too-short cycle that never drops WiFi leaves `gcsIPSet` set.

## Diagnosing it (the backpack is healthy -- ask it directly)

The backpack is not failing, so **its own status endpoint is up and will name the
offender.** `GET http://boxer-txbp.local.symmatree.com/mavlink` (route
`server.on("/mavlink", ...)`, `WebMAVLinkHandler`) returns:

```json
{ "enabled": true,
  "counters": { "packets_down": N, "packets_up": N, "drops_down": N, "overflows_down": N },
  "ports":    { "listen": 14555, "send": 14550 },
  "ip":       { "gcs": "10.0.x.y" },   // "IP UNSET" == still broadcasting, unlatched
  "protocol": "UDP" }
```

- `ip.gcs` **is the offender** -- the GCS it anchored to. If it is not acebase's
  address, that host stole the link. `"IP UNSET"` means it hasn't latched yet.
- `counters` are the live traffic rates: incrementing `packets_up/down` confirm it is
  streaming happily (to whoever `ip.gcs` is); `drops_down`/`overflows_down` would flag
  a genuine link-capacity problem instead.

## Recovery

The backpack's target is learned, not settable -- the firmware (above) latches to
whoever answers the discovery broadcast first and holds that lock until it reboots.
So recovery is: read `/mavlink`, note `ip.gcs`; if it isn't acebase, take that host
off the link, then power-cycle the backpack so it re-broadcasts and mavproxy can win
the fresh race. A power-cycle without removing the other GCS just re-rolls the same
race, which is why it has sometimes taken more than one.

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
- Backpack firmware (local reference clone): `~/expresslrs-backpack`
  (`lib/WIFI/devWIFI.cpp`).
