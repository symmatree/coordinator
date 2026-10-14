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
| FC `TimeUS` | every `.bin` record | yes -- monotonic from FC boot, never steps. But it is **not a good second**: it runs fast against GPS by a flight-dependent amount, see below |
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

### FC `TimeUS` runs hundreds of ppm fast, and by a different amount each flight

Fitting GPS seconds-of-week (`GPS.GWk`/`GMS`, the receiver's own time) against `TimeUS` across a
whole log:

| flight | records | rate error | residual |
|---|---|---|---|
| 260923 | 1877 | **+313.1 ppm** | 8.7 ms rms, 29 ms max |
| 260926 | 2957 | **+241.8 ppm** | 11.3 ms rms, 38 ms max |

So `TimeUS` is monotonic and jump-free but it is not a calibrated second, and the error **moved
71 ppm between two flights three days apart** -- plausibly thermal, not measured as such. At
313 ppm a one-shot offset costs 313 us per second of separation from where it was taken: 94 ms
across a 300 s flight. **Fit the rate per flight; do not store it as a constant.**

This also means the GPS fit is the anchor for anything absolute. Its residual is the FC's
log-time-versus-fix-time jitter, and it averages down: 8.7 ms over 1877 records is 0.20 ms on a
fitted offset, which over a 600 s flight is **0.33 ppm**. That is the best absolute time reference
on the vehicle and it is good enough to check a TCXO against.

### `SYSTEM_TIME` is NOT GPS-authoritative, and can be a day wrong

`SYSTEM_TIME` carries `AP::rtc().get_utc_usec()` and `AP_HAL::millis()` sampled together inside the
FC, which makes it look like the clean way to get UTC with no link latency in it. It is not
trustworthy. On 260923, comparing it against the FC's **own GPS** at matched boot times, with GPS
locked and valid throughout:

```
boot 52.6 s   SYSTEM_TIME: 2026-09-22 12:26:10.517
              its own GPS: 2026-09-23 12:06:28.353
              delta: -85217.836 s, constant over 214 samples
```

**A day behind, while GPS was right.** The coordinator's wall clock stepped +85,093 s that morning
at 12:06:17Z, and the only other time source on that link is the coordinator -- so `get_utc_usec()`
can be sourced from *our* clock rather than from GPS. That matters for any scheme whose point is to
be independent of our clock discipline: if `get_utc_usec()` is in the chain, it isn't. Take UTC from
the dataflash's own `GPS` records instead.

Even when the RTC is right it agrees with GPS to only **median +18.7 ms, rms 18.5 ms** (678
samples) -- so that route has a ~19 ms floor regardless.

And `SYSTEM_TIME` **cannot measure the FC clock rate**: on the same flight it gives +2.0 ppm with a
0.2 ms residual against the GPS route's +313.1 ppm. My reading is that `get_utc_usec()` returns
`micros64()` plus a stored offset, so the message ships the same clock twice and their difference
reveals the offset-refresh interval rather than the crystal. Consistent with a ~310 us sawtooth
buried in `time_boot_ms`'s integer-millisecond quantisation, but **the mechanism is inferred from
behaviour, not read from `AP_RTC`.** The 150x discrepancy is the established part.

### `vehicle.tlog` concatenates FC boot sessions with no marker

260923's tlog has **three backward steps in `time_boot_ms`** -- at message indices 597, 848 and
1526 of 2123 `SYSTEM_TIME` records -- so the file spans **four** FC boot sessions. The deltas are a
clean 500 ms otherwise, so this is the file's content and not a parse error. Anything joining tlog
to dataflash has to establish which session it is in, and the boundaries have to be found from
boot-time discontinuities because nothing marks them. Fitting a line across two sessions a day
apart yields a 165,000,000 ppm clock error, which is how this was found.

### Getting to FC time

Two independent routes; use both, because neither is self-validating.

* **`VISP.RTimeUS` against FC `TimeUS`** -- the FC logs the coordinator's own timestamp next to its
  own for every pose it received, so regressing one on the other gives coordinator -> FC time with no
  GPS arithmetic and no working NTP. On 260814 the fit residual is **1.2 ms rms, 3.8 ms max** over
  2652 samples (`capture_align.fc_time_from_visp`). Requires VIO to have been streaming --
  so this route is **unavailable from 260923 onward**, where `VISO_TYPE=0` and the log's `VISP`
  count is 0. For those flights `timesync.jsonl` is the only route to FC time.
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

### Aligning two campods' frames

A campod sidecar carries the shutter instant twice, in two clocks, and the choice matters
because only one of them is comparable between pods:

| field | clock | use |
|---|---|---|
| `sensor_timestamp_ns` | `CLOCK_BOOTTIME`, pod-local | "the time when the first row of the image sensor active array is exposed" -- frame START, and the quantity camera sync aligns. Not comparable across pods: boot clocks share no epoch |
| `frame_wall_clock_ns` | `CLOCK_REALTIME` | the SAME instant as `sensor_timestamp_ns` (libcamera's `FrameWallClock`), and the field libcamera's own sync algorithm compares. "Being a wall clock measurement, it can be used to synchronise timing across different devices" |
| `clk` = `boot_ns`/`mono_ns`/`wall_ns` | all three, sampled back to back | the device's own boot->wall conversion for that frame. Cross-checks the field above, and stands in if a device's libcamera predates it. Same keys as the accel reader's `t:"clk"` records |
| `wall_clock_utc`, `monotonic_ns` | post-capture | when the loop stamped and finished the frame, NOT the shutter -- see below. Median 578 ms after exposure on campod-sw |

So the acceptance test for camera sync is a subtraction between two pods' matched
`frame_wall_clock_ns`, done off the device. Both pods' realtime clocks are chrony-disciplined
off the same PPS edge, measured at ~12 us apart, so the comparison is meaningful at that
scale. `SyncTimer` is not that number: on the server it is 0 by definition, and on the client
it is how early it crossed its predicted ready time.

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

### An enable-gated subtree is absent from both the export and `PARM`

`VISO_TYPE` is declared with `AP_PARAM_FLAG_ENABLE` (`AP_VisualOdom.cpp:37`), and
`AP_Param::next()` sets `last_disabled` and skips the rest of the subtree while such a param
reads 0 (`AP_Param.cpp:1813-1818`). A param *download* therefore omits the whole group -- so
turning VIO off took the export from 1293 to 1283 names, and the 260923 log's `PARM` block
likewise carries only `VISO_TYPE`.

**The values are still on the FC.** Enumeration hides them; storage keeps them, and a direct
`PARAM_REQUEST_READ` by name answers. Measured on the vehicle 2026-09-23 with `VISO_TYPE=0`:
`VISO_POS_X` returned `0.072`, `VISO_DELAY_MS` returned `100`, neither present in that day's
export or `PARM`. The last known values are written down in
[ardupilot-vio.md](ardupilot-vio.md).

So "absent from `PARM`" means one of two different things and they are not distinguishable
from the log alone: the parameter does not exist on that firmware, or it exists and its
enable gate is off. `ardupilot/verify.py` reports the second case as `[missing]`.

### Which OAK-D capture session was the armed one, without forensics

Stills are arm-gated (`OAK_ARM_FILE`, `feature_tracker.cpp`, #88), so **a session directory
containing JPEGs is a session that was armed, and one containing only `.feat` never was.**
On 260923 exactly one of the coordinator's sessions held stills and it was the flight; the
rest of that day's sessions held `.feat` alone.

This is the cheap answer to the question that cost exposure/ISO forensics on 260730 (#157).
Two cautions:

- A session can span more than one day and its directory name is the coordinator's wall clock
  at session *start*, not the flight date. 260923's flight session is named `20260922T122804Z`
  because it opened on the previous day and ran a ~24 h boot. Check the name against the
  session's own `start_unix` in its `.feat.json` before reading anything into it.
- The stills bracket the armed window only loosely, because their sidecar timestamps are
  written after encoding (see above). For the window itself, prefer the FC's `ARM`/`EV10`/
  `EV11` records or a ground-side observer.

### Ground-side logs are the one absolute-time reference

All four clocks in the table above live on the vehicle or the coordinator, and two of them are
untrustworthy. The cluster-side observers are not: `mavproxy`'s console log and the Mimir
metrics are stamped by machines with working NTP, so they are the outside check on any
absolute time derived from vehicle data.

Worked example, 260923 -- the FC dataflash and the mavproxy console share no clock, the first
mapped to UTC through its own `GPS` week/ms and the second stamped by the cluster:

| event | dataflash | mavproxy | delta |
|---|---|---|---|
| ARM | 12:09:10.4 | 12:09:11.7 | 1.3 s |
| DISARM | 12:11:49.9 | 12:11:50.8 | 0.9 s |

Agreement at that level corroborates the clock join, the log decode and the state model at
once, the same way the explicit-vs-derived liftoff check does below. It is a sanity check, not
a calibration: the residual here is dominated by a two-point linear fit plus MAVLink transport
latency, and is not good enough to key a join on.

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
created**. With `LOG_DISARMED=2` (**"Disabled on USB connection"** per `AP_Logger.cpp` -- so it logs
from boot *except* while USB is connected; `LOG_REPLAY` accepts 1 or 2), a file is created at each
boot -- and only there, since `LOG_FILE_DSRMROT` is inert under `LOG_REPLAY=1` (see below). The
corpus splits cleanly at the point from-boot logging went on:

```
real dates    every flight through 2026-06-29
1980 epoch    every flight from 260705 onward
```

`260814-woods` has both: `2026-08-14 08-54-19.bin` (the drive to the site, created with GPS time in
hand) and `1980-01-11 08-00-07.bin` (the flight, created at the field after a reboot, before lock).

**`LOG_ENTRY.time_utc` over MAVLink is last-modified, not creation.** This is the field a log
download shows you, and it decides which log is the flight. The check: `LOG_DISARMED` is 1 or
2, so a log MUST have been created at boot -- if no entry carries a boot-time stamp, the field
is not creation time.

**And `LOG_FILE_DSRMROT` is inert on this vehicle, so do not reason from it.**
`AP_Logger_Backend::vehicle_was_disarmed()` rotates only
`if (file_disarm_rot && !log_replay)`, and this vehicle flies `LOG_REPLAY=1`. So nothing
rotates at disarm: **a log's boundaries are power cycles.** Its `time_utc` marks the power-off,
not a disarm.

On 260923 the disarm was 12:11:50.8Z and the flight log's `time_utc` was 12:12:00Z, which looks
like disarm rotation and is not -- the vehicle was powered down shortly after landing, so the
two coincide. An earlier version of this section drew the rotation conclusion from exactly that
coincidence.

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
| `.feat` IMU | the OAK-D IMU at ~100 Hz | **vibration spectra** -- Nyquist is 50 Hz and the motor rev line (~115 Hz) and blade pass (~350 Hz) both alias. Use the FC's raw IMU: `ACC` / `GYR`, from `LOG_BITMASK` bit 19 (`MASK_LOG_IMU_RAW`, on since `5e89402`). **Not `ISBH`/`ISBD`** -- those are the batch sampler, a separate feature gated by `INS_LOG_BAT_MASK`, which is 0 on this vehicle. A log can be full of raw IMU and still have zero `ISBH` |
| `VISP` / `VISV` | the onboard pose exactly as the FC received it, on the FC clock | truth of any kind |
| `XKF1` | truth-ish -- but only while RTK holds (see above) | under canopy |
| `ESC` | per-motor RPM, **indexed by output channel, not ArduPilot motor number** -- map through `SERVOn_FUNCTION` ([#169](https://github.com/symmatree/coordinator/issues/169) has the table) | reading motor 1/2/3/4 straight off `Instance` -- that mis-sorts a fore/aft split into a diagonal one |
| `MAV` | **per-MAVLink-channel link health**: rx/tx packet counts, drops, times-full, max gap. `chan` is 0-based and equals the `MAVn` parameter minus 1, so chan1 = MAV2 = the coordinator and chan2 = MAV3 = the ELRS/ground link. On 260814 chan1 ran 17 rx / 41 tx pkt/s with **zero** drops, chan2 190 rx / 47 tx. This is how a ground-link dropout is visible from the vehicle side | identifying *which* messages -- it is counters only |
| `UART` | per-serial-port `Tx`/`Rx` bytes/s and `RxDp` (received bytes dropped), 1 Hz, `I` = the `SERIALn` index | attributing bytes to a message; it is byte counters only |
| `TSYN` | the FC's own record of TIMESYNC exchanges, **with the peer SysID** and round-trip time (33 exchanges on 260814, RTT median 1007 us). A third, FC-side route to the clock bridge | high-rate work -- it is ~0.1 Hz |
| colour stills | the mapping product | anything needing their own timestamp -- see above |
| `mono_rect_left` | the actual VIO input, global shutter and fixed focus | only 260814 has it; capture is off by default from #216 |
| `GPS` / `GPA` | fix status, `NSats`, `HDop`, and the receiver's own accuracy estimates -- `HAcc`, `VAcc`, `SAcc`. On 260923: `NSats` 6-18 median 13, `HDop` 1.09-4.20, `HAcc` down to 0.01 m | -- |
| `UBX1` / `UBX2` | the F9P's own view of its RF environment, and the only in-band interference measurement the vehicle carries. `jamInd` 6-48 and `noisePerMS` 66-91 on 260923, each with a position on it | `agcCnt`, constant at 4563 for all 75 records of that log. And it is sparse -- 75 samples against `GPS`'s 1879 |
| `RAD` / `RSSI` | **`RAD.RemRSSI` only** -- 9-52, median 37 on 260923. That is the ground end's report of what it hears, and the one live RF number in the log | everything else in them. `RAD.RSSI` sits pinned at 254, `RSSI.RXRSSI` at 1.0 because it is normalised from that, and `RSSI.RXLQ` at -1. And **per-antenna**: see below |
| `PARM` | **the parameter set the vehicle actually flew, as far as a param download can see it.** 1293 names on 260814, 1283 on 260923 -- both equal to the FC export of the day. The authoritative answer to how the vehicle was configured when this data was recorded | the vehicle's configuration *now* (it is a record of that boot); and **not quite complete** -- see the enable-gated note below |

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

**Per-antenna RSSI is not in the log, and that follows from the link mode.** The R24-TD is a
diversity receiver and knows which of its two antennas it is using; the per-antenna numbers live in
the CRSF LinkStatistics frame. This link runs in ELRS **Link Mode MAVLink** -- a deliberate choice,
recorded in [`rekon10/ground-station.md`](rekon10/ground-station.md), which is what gives the FC a
MAVLink GCS link and the RTCM injection path -- so `SERIAL6_PROTOCOL = 2`, the FC sees
`RADIO_STATUS` rather than LinkStatistics, and `RSSI_TYPE = 5` reads from it. `RXLQ` is -1 because
`RADIO_STATUS` carries no link quality, and `RAD.RSSI` sits pinned at 254 with `RSSI.RXRSSI`
normalised from it to 1.0. `RAD.RemRSSI` is the live number. `RAD.RSSI`/`RemRSSI` are the two
**ends** of the link, air and ground, not antenna 1 and antenna 2.

**Every `.feat` recorded with the capture overlay running is missing 38-59% of the frames the camera
produced** (E31, [#156](https://github.com/symmatree/coordinator/issues/156)). Sessions that wrote no
capture artifacts lose 0.0-2.0%. Any per-frame rate computed from a `.feat` is a rate over what
survived, not over what the camera saw.

---

## Gaps in the campod accel stream, and the two windows that are not the same window

Both captures that carry `accel-camera.jsonl` / `accel-arm.jsonl` -- 260923 (flight, campod-sw) and
260924 (bench tumble, campod-sw, same two parts) -- have stretches of elapsed time with no samples
in them. All of them are early: the last is at capture +80.5 s on 260923 and +13.8 s on 260924.

**A short read is the normal case, not a symptom.** The reader takes whatever each FIFO holds and
moves on, so the mean batch is 2.2 samples (camera) and 2.5 (arm) against a 32-deep FIFO. `n` < 32
describes 461121 of the 461129 batches on 260923 camera. It is not evidence of anything.

**`ovr` cannot be used to classify a short read, because it is not sampled on one.** `entries()`
reads `FIFO_STATUS`, and returns `(n, false, nil)` immediately whenever `n < fifoDepth` -- it only
goes on to read `INT_SOURCE` when the FIFO came back full. So `ovr` is false by construction on
every short batch, and the header's rule "a batch with `n` < 32 and `ovr` false lost nothing" rests
on a flag that was never read. The rule is still sound on its own terms -- nothing drains that FIFO
but the reader, so a non-full FIFO did not overflow since the last drain -- but it describes the
instant of the read, not the interval before it, and the interval is where the holes are.

**The detector is `gap_ns` against the FIFO fill time** (32 / the fitted rate; 9.81 ms camera,
10.08 ms arm on 260923). **The classifier is the other stream:** the two devices are drained by one
loop, so asking whether the co-stream kept producing at its own expected rate during the gap
separates a process-level stall from a device-level one. That is a measurement, unlike `ovr`.

**But `gap_ns` is structurally blind to loss spread one sample at a time**, which is the thing
this section got wrong. Measured on a seven-hour PPS-locked campod-sw session (785k batches): the
`drops` counter reached 103k (arm) and 144k (camera), which at one sample each is **44 s of dark
time, 0.18% of the session** -- while `gap_ns` found only **six** gaps over 10 ms in the whole night
(worst 247 ms arm, 514 ms camera, 0.001% of batches). Both are correct. A discard does extend the
following `gap_ns`, because `lastNS` is only updated on the successful path -- but at 5.7 events/s
it extends it by ~0.31 ms against a **measured median gap of 0.50 ms**, so it lands inside the
normal spread. Any threshold at 2x the fill time cannot see it, however much of it there is.

So the two qualifiers are complementary and neither subsumes the other: **`gap_ns` finds
concentrated holes, a drop counter finds thin loss.** `gap_ns` is still the right gate for an
analysis window, because a 500 ms hole is what corrupts a decay fit or a spectrum segment, and a
0.18% thinning is a negligible broadband noise addition. But "no stalls" is not "no loss".

**`drops` counts failed pool allocations, not samples.** The field was added to attribute holes and
does not do that cleanly. Two traps in it: the name invites reading it as lost samples, and it is
*cumulative*, so summing it across the batches in a window inflates it by the batch count (summing
~1600 batches of a ~116k counter gives 186M, which is not a loss figure). Take endpoints.

Each failed allocation does discard at least one sample -- `if n == 0 { continue }` sits before
`pool.get()`, so the "found nothing" case returns earlier -- so `drops` is a lower bound on
discarded samples in units of events, and the conversion to samples is unrecorded.

**And the fitted rate does not bound loss.** The rate is `(true ODR) x (1 - loss_fraction)`, so a
constant loss fraction scales it and leaves it flat. Rate flatness bounds the *variation* in loss,
not loss itself, and absolute loss is unbounded from the rate alone because the ADXL345 datasheet
gives no ODR tolerance to compare against. A corollary for anything quoting absolute ADXL rates:
those are emitted-sample rates, so the true ODR is higher by the loss fraction -- on that session,
-1.21% and +1.48% of nominal become -1.08% and +1.66%.

| capture | stream | stalls | of which loop-blocked | of which co-stream at full rate | elapsed time with no samples | last stall |
|---|---|---|---|---|---|---|
| 260923 flight | camera | 10 | 7 (+1 partial) | 2 | 985 ms (3123 ppm of capture) | capture +80.5 s |
| 260923 flight | arm | 7 | 3 (+4 partial) | 0 | 505 ms (1606 ppm) | capture +80.5 s |
| 260924 tumble | camera | 2 | 2 | 0 | 363 ms (920 ppm) | capture +13.8 s |
| 260924 tumble | arm | 2 | 2 | 0 | 365 ms (925 ppm) | capture +13.8 s |

* **Most stalls are process-level.** Both streams go silent at the same instant for the same
  duration and both come back with `n`=32 and `ovr` set -- 260923 at capture +6.3, 12.2, 13.0, 20.0,
  20.1, 36.6 and 80.5 s, matching to 0.1 s and 1-2 ms; 260924 at +4.5 and 13.8 s. During those the
  co-stream emits 0.1-5% of its expected samples. The largest is 355-361 ms, about 1160 samples into
  a 32-deep FIFO.
* **Two are camera-only, and the loop was demonstrably running through them** -- capture +7.4 s
  (115 ms) and +26.5 s (361 ms), during which the arm emitted 369 and 1142 samples against 366 and
  1146 expected, 100.8% and 99.6%. So the loop polled the camera at full rate for those windows and
  emitted nothing for it. Two mechanisms produce that and the capture file cannot separate them:
  `entries()` returning 0 or erroring (both `continue` without emitting and without updating
  `lastNS`), or the camera's batch pool running dry, whose handler drains the FIFO into a throwaway
  buffer and discards it -- leaving no record, no `lastNS` update, and a consumed overrun latch. The
  pool holds 256 batches, which at the camera's 1463 batches/s absorbs 175 ms, so a 361 ms writer
  stall would exhaust it. Read order does not explain it: camera is `spidev0.0` and is drained
  *first* each cycle, which is why the arm's batches are the fatter of the two (it accumulates
  during the camera's 1.2 ms drain), not why the camera has the holes.
* **The pool-discard path is invisible in the capture.** Its counter is `pool.drops`, printed to
  stdout once at process exit, so it reaches journald on the pod and not the offload bundle. In the
  same branch `readErrs.Add(0)` adds zero.
* **Nothing appears late in either capture.** 260924 was ended by a `quiesced` stop from the fleet
  API -- a graceful SIGTERM -- and shows no stall at the stop, 381 s after its last one. One capture
  is one capture, but the teardown path has not been observed to cost samples.

### Classifying a hole from the counters -- and the one class that records nothing

The reader's own counters mostly settle what a hole is, which matters because the alternatives --
the part ceasing to deliver, the SPI read failing, the process restarting, the writer blocking --
each need a different response. Two captures have shown two *different* mechanisms, so classify
rather than assume.

**Case A, the writer blocked: `dropped_samples` rises and accounts for the hole.** Measured on
20261015 campod-sw, over 28 holes on the camera channel:

* **`dropped_samples` accounts for the hole.** It grew by 80726 across exactly the stall batches,
  against 81273 the part would have produced during those gaps at the fitted rate -- **99.3%** (arm
  99.1%). The reader counted them, which means it drained the FIFO and discarded the batches, so
  the part was delivering throughout. **This is the test for "did the sensor stop".** It did not.
* **`errs` separates a failed SPI read from an empty pool**, and was 0 across all 380402 batches.
  So the bus was never the mechanism either.
* **`i`, `dropped_samples` and `errs` are cumulative and were all monotonic**, so the reader never
  restarted. A reset would break the sequence; nothing did.
* **`ovr` was set on 2 of 380402 batches and on only 1 of the 28 batches that end a hole**, so the
  FIFO was not sitting full waiting to be read -- consistent with continuous draining rather than a
  device that went away and came back.
* **`drain_ns` shows no ramp.** 38 us on the batch before a hole and 214 us on the batch after,
  against a 39.5 us baseline, with the preceding 200 batches statistically identical to baseline.
  It blocks hard and releases hard, which is the shape of a write that does not return rather than
  throughput falling away.
* **The co-recorded camera stalls in the same windows** -- 24 of its 25 inter-frame gaps over 1.5 s
  overlap an accel hole. Two ADXL345s on separate chip selects and a CSI camera do not fail
  together, so the common element is the card and the processes writing to it.

What this does *not* identify is the cause of the write stall. A card going busy produces no kernel
message, and the journal over that window carries no mmc error, no I/O error and no blocked-task
warning -- so their absence discriminates nothing.

**Case B, the reader stopped polling: every counter stays flat and ~1600 samples vanish
unrecorded.** This is the dangerous one, and the header's rule gets it wrong. That rule says
"neither rising means the device had nothing to give." On 20261019 campod-sw both devices show a
single hole -- 514.7 ms on `arm`, 513.4 ms on `camera`, within 1.3 ms of each other, so
process-level by the classifier above -- and across it:

```
i=  440545  n= 1  ovr=False  drain=   36.6us  gap=    0.3ms  dropped=0  errs=0
i=  440577  n=32  ovr=True   drain= 1317.6us  gap=  514.7ms  dropped=0  errs=0   <- ends the hole
```

`dropped_samples` 0, `errs` 0, and the part was fine: at the fitted rate it produced **~1630
samples** (arm) and **~1671** (camera) during those gaps. The reader recovered **32** -- one FIFO --
and recorded no loss, because the pool-empty path that increments `dropped_samples` only runs when
the reader is actually draining. It was not draining; it was not there.

So "neither rising" means **the reader was not polling**, not that the device was idle. In that
state the loss is `gap_ns x fitted_rate - 32`, computable from the record but counted nowhere, and
the only positive trace is `ovr` true with `n`=32 on the batch that ends the hole.

**And `i` does not help.** It steps 440545 -> 440577, exactly 32, across 514.7 ms in which the part
made 1630 samples. The cumulative index counts what was *read*, so it advances smoothly however
much was discarded -- the point `docs/rekon10/vibration-testing.md` makes in the abstract, here as
a measurement.

| | Case A (20261015, 28 holes) | Case B (20261019, 1 hole) |
|---|---|---|
| reader | draining at full rate | not polling |
| `dropped_samples` | +80726, 99.3% of expected | **0** |
| `errs` | 0 | 0 |
| `ovr` on the ending batch | 1 of 28 | **set, with `n`=32** |
| loss recoverable from the file | yes, from the counter | only as `gap x rate - 32` |

**Both cases need `gap_ns` to find the hole in the first place**, and then the counters to say which
it was. A pipeline that trusts `dropped_samples` alone sees Case B as a clean capture.

**And collectd cannot see it.** The disk plugin samples at `metrics_interval`, so a device blocking
for ~1 s once per interval contributes a steady ~1000 to `disk_io_time` in every sample and reads as
light load. For a sub-interval event the accel stream is the better instrument: its own per-batch
`gap_ns` is ~1 ms-resolution on the write path, at ~1300 batches/s.

**Stalls have stayed early-capture, on every capture so far.** Last gap above 20 ms, capture-relative:

| capture | campod-se | campod-sw |
|---|---|---|
| 260923 flight | -- | +80.5 s |
| 260924 tumble | -- | +13.8 s |
| 20261019 cycle 19 | +18.4 s | +15.1, +24.8 s |
| 20261019 cycle 20 | **none at all** (worst gap 12 ms) | +17.7 s |

Every one is simultaneous across that pod's two channels, so process-level by the classifier above.
**A validity window starting 90 s in has been clean on every capture to date**, which costs nothing
on this vehicle: the pods get a settle window on the order of 120 s before the vehicle is armed, so
the validity and regime windows nest (see below) rather than competing.

One capture now exists with no hole over 12 ms anywhere in 250 s -- cycle 20's campod-se -- so the
early stalls are not an invariant of the platform.

### Two windows, two reasons

Two separate reasons to restrict an analysis to part of a capture, answering different questions:

* **Validity** -- is the data trustworthy here? Set by the stalls above. Nothing to do with the
  vehicle: how a sensor is mounted does not depend on whether the vehicle was armed. Calibration
  runs on the whole capture.
* **Regime** -- was the vehicle in the condition under study? Set by the flight. Vibration at flight
  level needs the rotors turning, so ground time is the wrong regime rather than bad data -- and a
  steady minute on the ground is sometimes the thing you want.

So say which one an analysis is applying. On 260923 they nest -- last stall at capture +80.5 s, ARM
at +161.0 s -- because the pods get a settle window on the order of 120 s, timed against a clock,
before the vehicle is armed. (ARM is FC `TimeUS` 214.6 s, GPS-dated to 12:09:10.42Z; the
capture-relative figure trusts the pod's `started_utc` of 12:06:29.45Z, a wall-clock stamp written
12 s after NTP stepped the coordinator -- fresh rather than independently verified.)

## The ADXL sample rates are not 3200 Hz, are not equal, and are not constant

`odr_hz_nominal` in the header is 3200 -- what was asked for over SPI, not what happened. The rate
has to be fitted per capture from the batch stamps, `polyfit(boot_ns / 1e9, i)`
([#388](https://github.com/symmatree/coordinator/issues/388) adds this as `rate_fitted_hz`).

Fitted over the stall-free part of each capture, same two physical parts both times:

| capture | window (pod boot s) | camera | arm | camera/arm |
|---|---|---|---|---|
| 260923 flight | 138.5 - 367.7 | 3269.78 Hz | 3180.51 Hz | 1.028069 |
| 260924 tumble | 92.7 - 469.0 | 3247.52 Hz | 3162.96 Hz | 1.026734 |

* **The two parts differ from each other by about 2.75%.** This is much the largest effect here.
  Putting the two streams on a shared grid, or differencing them, needs both fitted rates; assuming
  one common rate misplaces a 250 Hz line by ~7 Hz.
* **The rate does not repeat across captures.** Camera moved 6830 ppm between these two and arm
  5530 ppm, both lower on the tumble. A rate fitted on one capture is not reusable on another.
* **It is not constant within a capture.** Splitting each stall-free window in half: camera +1529
  ppm and arm +1811 ppm across the flight, camera -549 ppm and arm -161 ppm across the tumble. Both
  streams move the same direction within a capture, and the opposite direction between captures.

### Fit over stall-free SEGMENTS, not over a window that looks stall-free

Half-splitting a window is confounded by any stall inside it, and the size of that confound is
larger than the effect being measured. On 20261015 campod-sw, half-splitting the whole capture
reads **-15682 ppm** (camera) and **-23936 ppm** (arm). Fitting each of the 27 inter-stall
segments separately instead gives **+276** and **+334 ppm**, and the per-segment rates scatter by
0.13 and 0.08 Hz -- 40 and 25 ppm -- with no trend across the capture. The apparent drift was the
holes leaking into the fit.

The arithmetic is the same one the rate itself needs: emitted rate is `(true ODR) x (1 - loss)`,
so a hole inside the fitted span depresses the slope in proportion to the dark time it
contributes. On that capture the dark time was 8.6% of the window. Whether the 260923 and 260924
windows above were clean enough for the half-split to mean what it says has not been re-examined.

So: cut the span at every `gap_ns` above a couple of fill times, fit each remaining segment, and
report the scatter across segments as the error bar. A single number over a span containing a
hole is a lower bound on the rate, not a measurement of it.

**The size of the bias is exactly the dark fraction, checked on eight streams.** Across the four
devices in each of bench cycles 19 and 20, `1 - span_rate/segment_rate` matches the dark time's
share of the span to within 86 ppm every time -- and on the two cycle-20 campod-se streams, which
have no hole over 20 ms at all, the two methods agree to 18 ppm and 1 ppm. So the correction is
arithmetic, not an estimate: a span-based rate can be repaired if the dark time is known, and the
two methods are interchangeable only on a capture with no holes.

A trap that follows: two captures with *different* dark fractions compared span-to-span will appear
to differ by the difference of their biases. On campod-sw that bias was 1266-1434 ppm in cycle 19
and 2008-2059 ppm in cycle 20, so a span-to-span comparison understates the real change between
those boots by about 700 ppm.

### As of 20261019 the rates are absolute, because the pods are PPS-disciplined

Four parts, two pods, one 248 s window, stall-free segment fit:

Four parts, two pods, two independent boots eight hours apart (bench cycles 19 and 20), stall-free
segment fit on each:

| pod | label | cycle 19 | cycle 20 | delta |
|---|---|---|---|---|
| campod-se | *(unconfirmed, see below)* | 3280.14 Hz | 3281.95 Hz | +552 ppm |
| campod-se | *(unconfirmed, see below)* | 3156.38 Hz | 3159.29 Hz | +923 ppm |
| campod-sw | arm | 3163.47 Hz | 3166.54 Hz | +971 ppm |
| campod-sw | camera | 3251.75 Hz | 3255.29 Hz | +1087 ppm |

Read from each bundle's own `collectd/<node>/chrony/` at the end of the window: both pods
stratum 1, `time_offset` 0.000, `clock_skew_ppm` 0.026 (se) and 0.247 (sw), PPS reachability 255.
Sub-ppm against parts sitting 1.1-2.5% off nominal, so these are the parts and not the
denominator.

**Four parts, four different rates, spanning 3.8%.** The two on one pod differ by 2.0% (sw) and
3.8% (se), which is the figure that matters for putting two streams on a shared grid.

**All four moved the same direction between the two boots**, by 552 to 1087 ppm, with the clocks
PPS-disciplined on both. Four independent MEMS oscillators do not drift together by chance, so the
shift is common-mode and the candidates are a shared one: the parts' temperature, or the rate of
the pulse all four are ultimately measured against. **Neither is established, and this is two
points.** The correlate available is the SoC temperature in each bundle's own collectd -- campod-se
mean 41.2 C in cycle 19 against a single sample of 43.5 C in cycle 20, campod-sw 43.6 C against
46.2 C, so both pods ran 2-3 C warmer on the second boot, same direction as the rates. That is a
proxy twice over: the SoC sensor is not the ADXL die, and cycle 20's collectd holds **one sample
per series**, so no slope can be fitted from it. The ADXL345 publishes no ODR tolerance and no ODR
tempco, so there is no datasheet number to check it against either.

**Across captures, a reused rate is still not safe.** campod-sw's camera part reads 3247.52
(260924), 3247.08 (20261015), 3251.75 (cycle 19) and 3255.29 (cycle 20) -- 2460 ppm end to end,
with the two closest 135 ppm apart. **Fit per capture**, and per segment within it.

Scale for spectra: 1000 ppm moves a 250 Hz line by 0.25 Hz, so a rate carried over from the
previous boot costs about that much on a line the vibration work is trying to resolve.

**That within-capture movement is not undetected sample loss.** Over those stall-free windows the
largest single read gap is 5.4-9.2 ms against a 9.8-10.1 ms fill time, and the summed excess over
fill is 0.0 ms -- the FIFO had no opportunity to discard anything. The one exception is the 260924
arm's second half, 5.4 ms of excess over 188 s (29 ppm of elapsed time) against a -161 ppm rate
change.

### The Pi was never a candidate for a thousand-ppm effect

An earlier version of this section said the fit divides by `CLOCK_BOOTTIME` and so cannot separate
the ADXL's rate from the Pi's, leaving the two degenerate. That is true as algebra and was never
true as a magnitude, which is the part that decides anything.

**`chronyd` logs the free-running crystal's offset at startup**, read from
`/var/lib/chrony/chrony.drift` -- what it learned while last disciplined. On 20261019 that is
**0.698 +/- 0.051 ppm** on campod-se and **-1.049 +/- 0.269 ppm** on campod-sw. About 1 ppm,
against the thousands of ppm being attributed. So an *undisciplined* pod still measures elapsed
time to roughly 1 ppm even when its wall clock is days out -- on that boot both were stepped by
16304264 s, 188.7 days, and the rate was never the thing that was wrong. Offset and rate fail
differently and only the offset was ever bad.

That also means the pre-PPS captures' denominators were sound, so their rate figures are the
parts, not their Pis. The fit still has to divide by *something*, and `boot_ns` is the right
choice; it just carries a ~1 ppm error rather than an unbounded one.

PPS ([#11](https://github.com/symmatree/coordinator/issues/11)) is live as of 20261019 and makes
the reference explicit rather than inferred, which is worth having -- but it closes a gap that was
three orders of magnitude smaller than the effect.

Whether the movement is thermal is testable from the FC side without resolving that: `IMU.T` (per-IMU
die), `BARO.Temp` and `GndTemp`, `ESC.Temp` and `MotTemp` per motor, `MCU.MTemp`, and `BARO.Press`.
The pod's own temperature is not in the flight directory -- the offload bundle carries accel, frames
and manifests, no collectd -- so the Pi die temperature nearest these parts stays on the device.

For spectra the size that matters is this: 2000 ppm of rate error moves a 250 Hz line by 0.5 Hz,
which is the same size as the per-motor RPM changes the vibration work is trying to resolve. Fit the
rate over the window being transformed rather than over the whole capture.

**Nothing in the file says which physical part it is.** `devid` reads 229 (0xE5), which every
ADXL345 reports, and `separation_m` is present in the header and empty on both captures. The only
identity a file carries is `label` (camera / arm) plus `node`; which part sits in which pod is
operator knowledge, stated when parts move, and the rate fit is the cross-check if it goes unstated.

**And the rate fit cannot resolve a label within one pod.** It separates the two parts easily --
2.0% apart on campod-sw, 3.8% on campod-se, against a 30-70 ppm within-capture scatter -- but it
carries no prior for which rate belongs to which end of the arm, so it cannot say whether `camera`
and `arm` are the right way round. Only putting a tap on a known sensor and finding it in that
stream does that. As of 20261019 campod-sw's pair is confirmed that way and campod-se's is not: se's
labels were assigned by tracing pins and chip selects, so a swap of its two is not ruled out, which
is why its two rates are listed above without positions.

---

## Qualified ignorance

Things it would be reasonable to assume and that are **not** established:

* ~~Whether `SYSTEM_TIME` actually arrives on MAV2 in practice.~~ **Settled 260923**: it does.
  The flight's `vehicle.tlog` holds 2124 of them, 1087 carrying real UTC, the first at
  `boot_ms=52576` -- so GPS-derived absolute time reaches the coordinator at 2 Hz about 53 s
  after FC boot, and nothing consumes it yet. Original note follows. The rates say it should
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
* Whether the two camera-only stalls on 260923 (capture +7.4 and +26.5 s, with the arm running at
  100.8% and 99.6% of expected throughout) were `entries()` returning 0, `entries()` erroring, or the
  camera batch pool running dry. All three leave the same trace in the capture file, and the counter
  that would separate the third -- `pool.drops` -- is printed to stdout at exit only.
* ~~Whether the within-capture rate movement is the ADXL oscillator or the Pi crystal. The fit
  divides by `CLOCK_BOOTTIME` and there is no absolute reference on the pod, so the two are
  degenerate.~~ **Closed 20261019**: `chrony.drift` puts the free-running crystal at ~1 ppm on both
  pods, three orders of magnitude under the effect, so the Pi was never a candidate. See above.
* Whether the graceful-stop path costs samples. 260924 stopped via `quiesced` with no stall in its
  last 381 s; that is one observation of one stop.
* Whether log naming is fully deterministic on GPS-time-at-file-creation, or whether a marginal race
  exists when lock lands mid-creation. The corpus is consistent with the deterministic reading; the
  marginal case has not been tested.
