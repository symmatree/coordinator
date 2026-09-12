# Reading rekon10 flight data

What the recorded data *means*: which clock to trust, how a sortie appears in a log, and what each
stream is and is not good for. **Where** things live is
[flight-data-layout.md](flight-data-layout.md) -- that document owns paths and this one owns
meaning, and neither repeats the other.

Everything here is either measured (with the flight named) or marked as belief. The point is to
stop the next reader re-deriving it wrong; several items below cost a wrong conclusion first.

---

## Clocks

Four, and they are not interchangeable.

| clock | where | honest? |
|---|---|---|
| FC `TimeUS` | every `.bin` record | yes -- monotonic from FC boot, never steps |
| coordinator `CLOCK_MONOTONIC` | capture sidecars (`monotonic_ns`), `.feat` frame headers, `timesync.jsonl`, `telemetry.jsonl` | yes -- never steps |
| coordinator wall clock | sidecar `wall_clock_unix` / `wall_clock_utc`, `.feat` frame headers | **no** -- see below |
| OAK-D device clock | sidecar `sensor_timestamp_ns`, `.feat` feature payloads | yes -- and it is the only one that says when a frame was *captured* |

### The coordinator wall clock is not trustworthy, and how it fails depends on the network

The coordinator has no RTC. Its wall clock starts wrong at boot and is corrected only if something
corrects it.

* **With house wifi**, `systemd-timesyncd` eventually reaches a time server and **steps** the clock.
  On 260814 that was **+184.288 s at monotonic 69.5 s**, after three NTP timeouts against the public
  pool. Every stamp written before that instant is 184 s early. In a capture session that reads as a
  185 s hole in the cadence -- there is no hole, `monotonic_ns` and `seq` are both continuous.
* **In the field with no wifi there is no step at all**, and the clock stays on whatever it booted
  with for the whole flight. This is the more dangerous case, because **the absence of a step is not
  evidence that the clock is right.** `capture_align.clock_step()` returning `None` means "nothing to
  repair here", never "these times are correct".

**So: join on monotonic, and repair pre-step wall stamps from it**
(`capture_align.repair_wall_clock`). Never key a join on `wall_clock_unix` directly.

### Getting to FC time

Two independent routes; use both, because neither is self-validating.

* **`VISP.RTimeUS` against FC `TimeUS`** -- the FC logs the coordinator's own timestamp next to its
  own for every pose it received, so regressing one on the other gives coordinator -> FC time with no
  GPS arithmetic and no working NTP. On 260814 the fit residual is **1.2 ms rms, 3.8 ms max** over
  2652 samples (`capture_align.fc_time_from_visp`). Requires VIO to have been streaming.
* **`timesync.jsonl`** (#167 / #208) -- every TIMESYNC exchange, pairing the FC's clock with our
  realtime and monotonic. Works whether or not VIO is running. Written from 2026-09-05 on, so flights
  before that do not have it.

A third route exists and is **not** recommended: fitting GPS week/ms from the `GPS` records to
recover UTC. It works (9 ms rms on 260814) but needs `GWk > 1000` filtering to reject the pre-lock
rows, and it buys nothing the two routes above do not.

`vehicle.tlog` (#220) is every frame the FC sent us, which includes `SYSTEM_TIME` -- GPS-derived
absolute time at 2 Hz on MAV2, the field answer to "what time is it" when there is no network. Its
own timestamps are wall clock and inherit the problem above; `timesync.jsonl` is what repairs them.

**Which stream carries what** (read from `libraries/GCS_MAVLink/GCS_MAVLink_Parameters.cpp` at
`1511f271`, the firmware in these logs -- not from memory, which had `SYSTEM_TIME` on the wrong one):

| stream | MAV2 rate | carries (subset) |
|---|---|---|
| `EXT_STAT` | 2 Hz | `SYS_STATUS`, `POWER_STATUS`, `MCU_STATUS`, `MEMINFO`, `GPS_RAW_INT`, `GPS_RTK` (not populated -- below), `NAV_CONTROLLER_OUTPUT` |
| `POSITION` | 2 Hz | `GLOBAL_POSITION_INT`, `LOCAL_POSITION_NED` |
| `EXTRA1` | 2 Hz | `ATTITUDE`, `AHRS2`, `PID_TUNING`, **`ESC_TELEMETRY`** |
| `EXTRA2` | 2 Hz | `VFR_HUD` |
| `EXTRA3` | 2 Hz | `AHRS`, `DISTANCE_SENSOR`, **`SYSTEM_TIME`**, `BATTERY_STATUS`, **`EKF_STATUS_REPORT`**, **`VIBRATION`** |
| `RC_CHANNELS` | **0** | `SERVO_OUTPUT_RAW`, `RC_CHANNELS` -- off, so neither is arriving |
| `RAW_SENSORS` | **0** | off |

### `GPS_RTK` is not populated by the u-blox driver

`AP_GPS::send_mavlink_gps_rtk()` calls into the backend only if
`drivers[inst]->supports_mavlink_gps_rtk_message()` returns true (`AP_GPS.cpp:1478`). Three backends
override it: `AP_GPS_SBF.h:49`, `AP_GPS_ERB.h:38`, `AP_GPS_SBP.h:36`. `AP_GPS_UBLOX` does not, so it
uses the base implementation, which returns false (`GPS_Backend.h:73`). This vehicle runs
`GPS1_TYPE=2` (u-blox). The same three backends are the only writers of `rtk_age_ms`,
`rtk_baseline_*`, `rtk_accuracy` and `rtk_iar_num_hypotheses` (declared `AP_GPS.h:227-236`).

Same at ArduPilot master as of 2026-09-09.

Observed: `260812-hover/ground/260812-hover.tlog` contains 517 `GPS_RAW_INT` records and 0 `GPS_RTK`.
Both message IDs are in the `EXT_STAT` list (`GCS_MAVLink_Parameters.cpp:250-255`).

The dataflash RTK fields are `GPA.RTCMFU` and `GPA.RTCMFD`, counts of RTCM fragments used and
discarded. They are counts, not ages or baselines.

### The GPS serial port runs at 230400, not `SERIAL2_BAUD`

`AP_GPS` cycles through candidate baud rates and accepts a u-blox only when (`AP_GPS.cpp:771-773`):

```
(!_auto_config && baud >= 38400) || (baud >= 115200 && UBX_Use115200) || baud == 230400
```

`GPS_AUTO_CONFIG=3` makes the first clause false. `UBX_Use115200` is `GPS_DRV_OPTIONS` bit 2
(`AP_GPS.h:629`) and `GPS_DRV_OPTIONS=0`, making the second false. So the port is only accepted at
230400, and `_initialisation_blob` is `UBLOX_SET_BINARY_230400` (`AP_GPS.cpp:83-85`), which commands
the receiver to that rate. `SERIAL2_BAUD=115` does not describe this port; the capacity is
23040 bytes/s per direction.

### `GPS_RAW_DATA` counts samples; `GPS1_RATE_MS` is milliseconds

`GPS_RAW_DATA` is `AP_GROUPINFO("_RAW_DATA", 9, AP_GPS, _raw_data, 0)` -- an `AP_GPS`-level
parameter, so there is one for all instances. `GPS1_RATE_MS` is in the `AP_GPS::Params` subgroup
registered as `"1_"` / `"2_"` (`AP_GPS.cpp:280,285`), so it is per-instance.

Neither value is converted. `_raw_data` is passed to `_configure_message_rate()` and written to UBX
`CFG-MSG.rate` (`AP_GPS_UBLOX.cpp:484`, `1960-1971`), which counts navigation solutions. `rate_ms` is
written to `CFG-RATE.measure_rate_ms` (`AP_GPS_UBLOX.cpp:2187-2195`), which is milliseconds. At
`GPS1_RATE_MS=200`, `GPS_RAW_DATA=5` produces one `RXM-RAWX` per second.

`_raw_data` is read differently by different backends: as a boolean (`AP_GPS.h:558-559`), as a mode
value (`AP_GPS_SBF.cpp:207,232`), and as the CFG-MSG rate (`AP_GPS_UBLOX.cpp:484`). Its parameter
metadata is a `@Values` list, not `@Units`.

When non-zero on a u-blox it produces `GRXH` (one per epoch) and `GRXS` (one per satellite:
`prMes`, `cpMes`, `doMes`, `gnss`, `sv`, `freq`, `lock`, `cno`, `prD`, `cpD`, `doD`, `trk` --
`AP_GPS/LogStructure.h:219-220`). `UBLOX_RXM_RAW_LOGGING` is `1` unless `AP_GPS_UBLOX_CFGV2_ENABLED`,
which defaults to `0` (`AP_GPS_config.h:100-101`, `AP_GPS_UBLOX.h:62-66`).

### `UART.I` is the `SERIALn` index

`Util::uart_log()` iterates `hal.serial(i)` and logs instance `i`
(`AP_HAL_ChibiOS/Util.cpp:687-691`); `AP_SerialManager::init()` applies `SERIALi_PROTOCOL` to
`hal.serial(i)` (`AP_SerialManager.cpp:451-457`). So `UART.I=2` is `SERIAL2`.

`log_stats()` returns early when a port has moved no bytes since the last call
(`AP_HAL/UARTDriver.cpp:205-209`), so an instance absent from a log is a port with no traffic, not a
port that does not exist. `Tx`/`Rx` are bytes/s; `RxDp` is received bytes dropped, in bytes/s.

Mission Planner's Status page is a live view of these same MAVLink fields (its `CurrentState`), plus
a few values it computes about its own link. That is why it shows things that look absent from the
logs: same data, different transport and different names, with the GCS-side link counters genuinely
not observable from the vehicle.

### Stills are stamped after they are compressed

`monotonic_ns` and `wall_clock_unix` in a sidecar are taken inside `build_sidecar`, which for a
**still** runs *after* `cv::cvtColor` and `cv::imencode` on a 12 MP frame. They record when the host
finished encoding, not when the shutter opened.

Measured on 260814, as `monotonic_ns - sensor_timestamp_ns`:

```
disparity        median 36.750 s   spread 0.65 s
mono_rect_left   median 36.756 s   spread 0.69 s      (agrees with disparity to 6 ms)
still            median 44.808 s   spread 10.11 s     <- +8.06 s, and varying
```

The offsets themselves are arbitrary (different epochs); what matters is that disparity and mono are
*consistent* while stills are late by a varying amount. Placing 260814's stills by `monotonic_ns`
puts them a **median 3.3 m** from where the vehicle actually was, p95 8.8 m, max 12.5 m.

**Use `sensor_timestamp_ns` for stills.** Map device -> host by fitting the disparity and mono frames,
which carry both clocks. This is [#167](https://github.com/symmatree/coordinator/issues/167), whose
title still says "~5 s, untested".

**How to test a clock choice without appealing to another clock:** both cameras see the same ambient
light, and the mono stream's timing is trustworthy, so its exposure curve is a physical
light-vs-time reference. Sliding the still series against it on 260814: device-placed stills need
**+1.0 s** to align, host-placed need **-5.5 s**. That confirms direction and rough size; it does
*not* pin the offset, because ambient light varies too smoothly to localise a lag and the true error
varies per frame.

> **Known-stale consumer.** `analysis/image-sharpness-vs-motion.ipynb` predates this and places
> stills by `monotonic_ns`, so its stills sit several seconds late and frames near the arm boundary
> can land in the wrong regime bucket. Its cross-stream alignment (OAK-D gyro vs FC gyro) is sound;
> it is only the still placement that is affected.

---

## The sortie, as it appears in a log

**Ground time before arming is long and is supposed to be.** The operator holds until the F9P
reports **RTK Float**, because nearly every question asked of this airframe is scored against the
GPS/EKF trajectory and a flight launched without it cannot settle anything. Most of that wait is
getting the backpack link to pass RTCM ([#195](https://github.com/symmatree/coordinator/issues/195),
[#196](https://github.com/symmatree/coordinator/issues/196)).

**RTK Float is a launch condition and does not survive the sortie.** On 260814 Float held to t=176 s,
then dropped to a plain 3D fix at t=199 and stayed there: **0% of the deep-woods window at Float,
43% of the airborne window overall.** "GPS truth" is a claim about the open parts of a flight.

**The tell is accuracy, and satellite count is actively misleading.** On 260814, going into the
woods:

```
window              NSats   HDop   HAcc m   VAcc m   status
RTK Float held         24   0.92     0.14     0.23        5
deep woods             26   0.57     1.46     2.11        3
```

Satellites went **up** and HDop **improved** while horizontal accuracy got 10x worse. A health check
on sat count or HDop sees nothing wrong (E19 records the same on 260712).

**Age of the last RTK correction is derivable: `GPA.RTCMFU`** counts RTCM fragments used, so the time
since it last advanced is the age of the last accepted correction. On 260814 it advanced about once
a second while Float held (median gap 1.00 s, max 3.00 s) and then **stopped completely -- zero
advances across the 90 s deep-woods window** -- with only 14 fragments discarded in the whole flight.
So on that flight the corrections stopped *arriving*; they were not being rejected, and the GNSS
reception was if anything better. **The RTK loss there is a corrections-delivery failure, not canopy
attenuation of the satellite signal** -- consistent with an RTCM path that runs over a WiFi link back
to the house ([#195](https://github.com/symmatree/coordinator/issues/195),
[#196](https://github.com/symmatree/coordinator/issues/196)).

*Caveat on the counter:* it reads ~0 on 260712 and 260730 even though 260712 reached RTK Float, so on
those flights it is either unpopulated or corrections arrived by a path that does not increment it.
Usable on 260728 and 260814; check that it advances at all before reading anything into a gap.

**Armed spans the flight; armed-but-not-flying is seconds.** Arming times out if the vehicle does not
launch, so the operator arms last and launches immediately -- measured arm-to-liftoff **1.2 to 8.0 s**
across four flights. There is no dwellable motors-on-the-ground regime to collect, and idle is not a
low dose of hover: on 260814, armed-on-ground is **2057 rpm / VIBE 0.99** against **6970 rpm /
VIBE 8.72** in hover, so a ground frame carries ~11% of the hover vibration dose.

**Deriving liftoff and touchdown -- and cross-checking it.** The explicit markers and the derived
ones answer different questions and you want both. `ARM` is a record of something the operator
physically did; vertical velocity is a record of what the airframe did. Neither alone is "liftoff":
vertical motion while disarmed is somebody carrying the aircraft, and armed-with-no-motion is the
pre-launch pause. **Commanded liftoff is the conjunction** -- armed, positive commanded throttle, and
vertical motion following it.

Treat the agreement itself as the check. When the explicit and derived answers line up, or differ in
a way that has an explanation (the ~3 s of spool-up between arming and moving), that consistency is
evidence for the whole chain -- the clock join, the log decode, the state model. When they disagree
without one, **this is an aberrant flight and it should be investigated before any number derived
from it is believed** -- whether that number is a segmentation, a time alignment, or two flight paths
overlaid. `capture_align.airborne_window` computes the derived half; the `ARM` records are the
explicit half; a consumer should look at both.

Two traps in the derived half:

* `EV Id=28` fires at spool-up, **before** liftoff -- 3.7 s early on 260814, at `ThO=0.018` with zero
  climb rate. The `EV_MAP` in `analysis/ardupilot_log.py` does not carry 17/28 and its 16/18 entries
  do not match what a 4.7 log emits; do not lean on it.
* An absolute altitude threshold mis-detects touchdown, because the vehicle can land **below** the
  home datum -- 260814 landed ~0.8 m down-slope of where it took off. Home-relative altitude is not
  AGL and there is no rangefinder yet.

**Mode changes move the throttle meaning.** Taking off in Stabilize and switching to Loiter remaps
throttle from 0-100% to centre-is-altitude-hold, so a hover reached in Stabilize **drops** on the
switch until throttle comes up to ~50%. An altitude excursion right after a mode change is expected
behaviour, not a control fault.

**The first seconds of a flight are not representative.** The airframe comes off the battery-strap
buckle (it parks tilted) and then noses down until the standing pitch trim from the fore/aft CG
imbalance is taken out ([#169](https://github.com/symmatree/coordinator/issues/169): front pair runs
15-22% harder than the rear, yaw output ~0, pitch output standing positive).

**An at-rest attitude is not level.** Same buckle. Do not calibrate a camera-to-body rotation off a
ground segment.

---

## Log naming, and what a 1980 date does and does not tell you

The FC has no RTC battery, so a log file is named from wall-clock time **at the moment the file is
created**. With `LOG_DISARMED=2` (log from boot, needed for `LOG_REPLAY`) and `LOG_FILE_DSRMROT=1`
(rotate at each disarm), files get created at boot *and* at every disarm -- so within one day you can
get both kinds of name. The corpus splits cleanly at the point from-boot logging went on:

```
real dates    every flight through 2026-06-29
1980 epoch    every flight from 260705 onward
```

`260814-woods` has both: `2026-08-14 08-54-19.bin` (the drive to the site, created with GPS time in
hand) and `1980-01-11 08-00-07.bin` (the flight, created at the field after a reboot, before lock).

**A 1980 name means "no GPS time when this file was created" and nothing else.** It does not mean the
log lacks GPS time -- 260814's flight log reaches a 3D fix at t=7.4 s. And **the filename is not the
flight date**: derive that from the `GPS` records, or from `telemetry.jsonl` once #220 lands.

---

## The dataflash is not a MAVLink capture

The FC `.bin` is ArduPilot's own binary log format with its own message set -- `GPA`, `XKF1`,
`RGPJ`, `RISI`, `MAV`, `TSYN`. MAVLink telemetry is a separate message set -- `GPS_RTK`,
`EKF_STATUS_REPORT`, `VIBRATION`, `SYSTEM_TIME`. They overlap in content but neither contains the
other. As of 2026-09-10 no MAVLink capture from this vehicle exists on the NAS: no `vehicle.tlog`
(#220) and no `timesync.jsonl` (#208) under any flight. The ground-side mavproxy tlogs are a
different observer -- they record what the GCS sent and received, not what the FC did.

Mission Planner's Status page is a live view of the MAVLink side (its `CurrentState`), plus counters
it computes about its own link. That is why it shows fields that are absent from the dataflash: same
underlying quantities, different message set and different names.

## What each stream is good for

| stream | good for | not good for |
|---|---|---|
| `.feat` feature payloads | feature supply, stereo depth, frame-loss accounting, local relative pose | anything absolute -- there is no world frame in it |
| `.feat` device timestamps | counting dropped frames: they sit on an exact frame grid (residual **0.0000 s** on 260814), so skipped frames are countable | -- |
| `.feat` IMU | the OAK-D IMU at ~100 Hz | **vibration spectra** -- Nyquist is 50 Hz and the motor rev line (~115 Hz) and blade pass (~350 Hz) both alias. Use the FC's raw IMU (`LOG_BITMASK` bit 19, on since `5e89402`) |
| `VISP` / `VISV` | the onboard pose exactly as the FC received it, on the FC clock | truth of any kind |
| `XKF1` | truth-ish -- but only while RTK holds (see above) | under canopy |
| `ESC` | per-motor RPM, **indexed by output channel, not ArduPilot motor number** -- map through `SERVOn_FUNCTION` ([#169](https://github.com/symmatree/coordinator/issues/169) has the table) | reading motor 1/2/3/4 straight off `Instance` -- that mis-sorts a fore/aft split into a diagonal one |
| `MAV` | **per-MAVLink-channel link health**: rx/tx packet counts, drops, times-full, max gap. `chan` is 0-based and equals the `MAVn` parameter minus 1, so chan1 = MAV2 = the coordinator and chan2 = MAV3 = the ELRS/ground link. On 260814 chan1 ran 17 rx / 41 tx pkt/s with **zero** drops, chan2 190 rx / 47 tx. This is how a ground-link dropout is visible from the vehicle side | identifying *which* messages -- it is counters only |
| `UART` | per-serial-port `Tx`/`Rx` bytes/s and `RxDp` (received bytes dropped), 1 Hz, `I` = the `SERIALn` index | attributing bytes to a message; it is byte counters only |
| `TSYN` | the FC's own record of TIMESYNC exchanges, **with the peer SysID** and round-trip time (33 exchanges on 260814, RTT median 1007 us). A third, FC-side route to the clock bridge | high-rate work -- it is ~0.1 Hz |
| colour stills | the mapping product | anything needing their own timestamp -- see above |
| `mono_rect_left` | the actual VIO input, global shutter and fixed focus | only 260814 has it; capture is off by default from #216 |
| `PARM` | **the complete parameter set the vehicle actually flew.** 1293 entries on 260814 -- the same count as the FC export, with none missing. The authoritative answer to how the vehicle was configured when this data was recorded | the vehicle's configuration *now*; it is a record of that boot |

**Configuration changes between flights, so cross-flight comparisons need dating.** The clearest
example: `MAV3_OPTIONS` went to 2 (`NO_FORWARD`, stopping VIO traffic being forwarded onto the ELRS
link) for the first time on **260814**, and on that same flight every MAV3 stream rate went from 0 or
1 to 4 Hz. Measured from the `MAV` counters, the ELRS downlink roughly **doubled** (17.5-19.3 -> 39.6
pkt/s) despite the forwarding fix. So 260814 is the first post-fix flight, and a measurement taken on
it verifies that a fix landed -- it is **not** evidence about the condition the fix addressed.

**Read the flown value from the log's `PARM`, not from `ardupilot/inputs/`.** The fragments and
`rekon10-methodi.param` describe the FC as of the repo's HEAD and the last export; neither is dated to
a flight, and a parameter can be pinned in a fragment, applied to the FC, and re-exported long after
the flight you are looking at. `PARM` carries the whole set, so the log answers this by itself and
nothing has to be inferred from commit order. Worked example: `LOG_BITMASK` is `589823` in the export
(raw IMU, bit 19, applied and round-tripped 2026-09-05) while both 260812 and 260814 flew `65535`
and contain zero `ISBH`/`ISBD` records -- the flights predate the apply.

**Every `.feat` recorded with the capture overlay running is missing 38-59% of the frames the camera
produced** (E31, [#156](https://github.com/symmatree/coordinator/issues/156)). Sessions that wrote no
capture artifacts lose 0.0-2.0%. Any per-frame rate computed from a `.feat` is a rate over what
survived, not over what the camera saw.

---

## Qualified ignorance

Things it would be reasonable to assume and that are **not** established:

* Whether `SYSTEM_TIME` actually arrives on MAV2 in practice. The rates say it should
  (`MAV2_EXT_STAT=2`); nobody has watched the wire. #220 will show it or its absence.
* Whether the FC pushes time of day by any other route. (`TSYN` shows it *does* exchange TIMESYNC
  with SysID 1 and logs the RTT, so the FC-side half of the bridge exists independently of ours.)
* Whether the 260814 corrections dropout was the WiFi link, the ground station, or something else --
  only that the fragments stopped arriving at the FC.
* Whether `GPA.RTCMFU` is populated on every firmware we have flown; it reads ~0 on 260712/260730.
* Whether the coordinator wall clock in the field is merely offset or also drifting. Only the
  post-NTP case has been measured (-11 ppm on 260814).
* The exact per-frame still encode lag. Only its distribution is known, and the light-curve check
  cannot resolve it.
* Whether log naming is fully deterministic on GPS-time-at-file-creation, or whether a marginal race
  exists when lock lands mid-creation. The corpus is consistent with the deterministic reading; the
  marginal case has not been tested.
