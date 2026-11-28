# Flight hardware: handoff

For whoever is the flight-hardware agent next. This role owns the **physical and electrical
systems of the flight vehicle**: FC, ESCs, radio, GPS, power distribution, the PPS
distribution board, connectors, physical mounting and strain relief. Distinct from
flight-platform (software that runs on the vehicle), flight-analysis (findings out of what
the vehicle recorded), and os-and-driver (images, overlays, device config) -- each of which
has its own handoff.

The boundary that actually matters is not the subsystem, it is **who the fact belongs to**.
`docs/rekon10/README.md` has the rule: by component, not by subsystem. A fact about an FC pad
belongs with the FC even if it exists because of the hub board. When you find yourself
explaining what software does with a signal, you have left.

## The charge on this file

Update it at the END of a session. When something here becomes a durable fact it graduates to
a real doc -- `docs/rekon10/central-hub.md` and `flight-platform.md` are where most of this
role's findings go. When it becomes work it becomes an issue. What is left is what would
otherwise be re-learned by collision. **"Where things stand" goes stale fastest.**

## What to read, in this order

1. **`docs/rekon10/README.md`** -- the map. A question-keyed table of which doc answers which
   question, and the two seams that keep needing trimming.
2. **`docs/rekon10/central-hub.md`** -- the PPS distribution board: buffer tree, the signal,
   the net list, the hole-by-hole layout, the Coordinator's SPI block.
3. **`docs/rekon10/flight-platform.md`** -- as-built hardware. The FC pad table, the UART
   mapping, receiver, VTX, ESC, power rails.
4. **`docs/rekon10/flight-platform-build-log.md`** -- why a part or value was chosen and what
   was tried first. Antenna routing lives here.
5. **`docs/campod-electrical.md`** -- the campod accelerometer harness and breakout board.
6. **`docs/rekon10/ardupilot.md`** -- which parameter makes a pin behave the way it does.

## The PPS board is generated, not hand-written

`docs/rekon10/pps-board/` holds `netlist.py` (parts and nets, the source of truth),
`layout.py` (placement + a verifier + writes the net table and the layout block into
`central-hub.md`) and `draw.py` (writes `pps-protoboard.svg`, plus a PNG to `/tmp` for
anything that cannot render SVG). **Edit the generator, never the markdown block.** The
verifier checks every placed net is connected, no hole carries two nets, no pin is orphaned,
and no reserved hole has been taken.

The **KiCad sheet is deliberately not regenerated**, so a netlist change leaves it behind --
`pps-board/README.md` records that.

**The drawing is the design, not a record of the built board.** It has been re-laid several
times since the board was built; the two differ at hole level and **the board is the
authority**. Do not "correct" the doc toward the drawing, and do not infer hole-level detail
about the built board -- ask.

## Where things stand (2026-11-28)

**PPS reaches every consumer, the FC included.** The FC leg is on pad `S12` (`CAM1_FEEDBAK_PIN`
61, coordinator#709); `ardupilot/scripts/pps-feedback-arm.lua` arms the handler at boot. Confirmed
2026-11-07 from 17:10Z: one CAMERA_FEEDBACK per second over mLRS, 422 contiguous. The FC's clock
counts about 999,696 us per RTC second (~304 ppm), on the FC side; whether it is the oscillator is not
established. flight-analysis has it for the time-sync notebook. The RTC's state is on the
coordinator's bus status as `rtc` (coordinator#680; `GET /pods`).

**Corrections run over mLRS and are confirmed end to end** (2026-11-03/04, Boxer off: DGPS, mostly
RTK float, RTK fixed twice; coordinator#690). RadioMaster Ranger in the attic on acebase's USB
(CP2102, `/dev/mlrs`), mLRS 31 Hz, to an RP4TD on SERIAL7 (MAV4, `ardupilot/inputs/mlrs-link.param`).
tiles `mavproxy-mlrs` carries NTRIP; the backpack mavproxy only logs; both run as sysid 254 so a
connected Mission Planner is the FC's GCS (255). MAV4 is not private, so STATUSTEXT and PreArm reach
the ground over mLRS (ELRS/MAV3 stays private). The full path is `docs/rtk-corrections-path.md`.
coordinator#676 (constellation review) and #683 (ground visibility) are open.

**Battery monitor is the Holybro PM02D** (`BATT_MONITOR` 21, INA228 on I2C2 via the repurposed RANGE
pigtail). coordinator#741 (`BATT_MAX_AMPS` 164) merged 2026-11-15.

**The spare FC is fitted (2026-11-23)**, wired through two protoboard breakout daughterboards with
short whips from the pads; the PCB they become is `docs/rekon10/fc-breakout.md`. Its parameter store
is whatever flight-platform's apply step has put on it. The I2C buses are split (wired by
2026-11-28): baro bus (I2C2) on the 4V5 side with the compass and SH1106, I2C1 on battery 5V with
the PM02D. That makes `BATT_I2C_BUS` 1 (coordinator#764) and needs a fresh compass calibration,
since the compass detects with a new device id.

**Failsafes:** radio and battery RTL, EKF AltHold, `RTL_ALT_M` 40 (coordinator#710). Bidirectional
DShot on the four motors (coordinator#677).

**The 2026-11-07 crash: the front-left (NW) motor failed on the climb.** Everything observed is in
`datasets/flights/rekon10/2026-11-07-crash/README.md`. All four motors are new;
the motor leads go through MR30s at the ESC (`flight-platform.md`, *Motor lead
connectors*). Name motors by corner and take every label from `analysis/motors.py`; the table
and how it was checked is `docs/rekon10/ardupilot.md`, "Which motor is which". NW is Motor3,
FC output 2, ESC telemetry index 1.

**The ESC is replaced (2026-11-21)** after its NW channel ran rough with new motors: an identical
Lucid 60A on **stock AM32 v2.22** (the calibrated v2.20 build was the first ESC's; per-ESC voltage
and current are back on generic constants, and nothing on the FC reads them). All four channels
ran a trial motor fine. Which came first in the crash, motor or ESC, is not established; the
crash README has the evidence.

**Motor direction is set in the AM32 configurator, whose positions are ArduPilot motor numbers,
not FC outputs** (checked on the bench 2026-11-25; `ardupilot.md`, "Which motor is which"). NW
and SE are the CW pair. Last report: positions 2 and 3 had been reversed (SW, NW), so 2 needed undoing
and 4 (SE) reversing.

**Open from this role:**
- coordinator#739 (thermistor motor temperature, an option) and #740 (runtime motor-failure
  diagnostics, a design space; not a plan).
- coordinator#674 (compass interference from flight current; test is a longer mast). The GPS mast tube
  broke at both ends in the crash.
- **Compass on USB power** (first FC): not detected on one boot, dropping reads on the next. Candidate
  (inference): the SH1106 on I2C1, unpowered on USB, loading the bus. The planned I2C split puts the
  compass and SH1106 on the 4V5 side.
- **PC1 read a fixed 9.9 V** on the first FC with the first ESC. Both are now out of the vehicle.
- **Boards being designed:** `docs/rekon10/coordinator-hat.md` (DS3234 + 74LVC1G17 + 3x '541,
  24 PPS outputs for 14 consumers; SH1106 and one hub carried on it), the campod header board
  (`campod-electrical.md`), and `docs/rekon10/fc-breakout.md`. Where the HAT's PPS headers go is
  open. Digi-Key stock/price lookup was offered via its API; credentials would be in
  `~/.config/digikey/credentials`, not yet set up.
- **Raft:** plywood (1/4" Baltic birch or skinned 3/16") is under consideration. There is no raft
  vibration data from any flight: on 2026-11-07 only SW had accelerometers and both flight-boot
  files are empty.
- **ELRS from MAVLink to CRSF** is under consideration, not decided: faster RC at a light telemetry
  ratio, one telemetry path, but the ELRS RC overrides stop feeding the GCS failsafe and the backpack
  log ends. `ground-station.md`'s Firefly row says "Std, Hybrid" where the module export it quotes
  reads `telemetry-ratio = 6`, `switch-mode = 1`.
- **The ELRS antenna anchoring.** **This is his to figure out and he has said so explicitly. Do not
  propose a fix.**

## What must not be dropped

**AHC is not AHCT.** Same package, same pinout, one letter. AHCT is specified 4.5-5.5 V and is
unusable on a 3.3 V rail. There are ten of each in stock.

**The two-tier buffer topology exists for the input transition-rate spec, not for DC load.**
The '125 inputs draw about 1 uA each; the constraint is 100 ns/V at VCC = 3.3 V, which the
DS3234's open-drain SQW cannot meet into twelve tied inputs at any sane pull-up.

**`INTCN` defaults to 1, so a DS3234 nobody has configured has its square wave OFF** and every
buffered line sits steady at 3.3 V. Control register write is `0x8E`, POR value `0x1C`, wanted
value `0x00`.

**All-zeros over SPI means unpowered, not a broken bus.** The datasheet disables read and
write access when VCC drops below both VPF and VBAT, so the chip stops driving MISO and the
reader sees its own idle line. With `BBSQW = 0`, which our `0x00` sets, INT/SQW also goes high
impedance below VPF while VBAT keeps timekeeping running -- so the clock is fine and has the
right time when power returns.

**D1 has three states and only one is conclusive.** Blinking means the whole chain to the
first-tier buffer is good. Dark means no 3V3 on the board. Solid is ambiguous: SQW parked high
with the pull-up winning, which is either an unconfigured chip or a chip that lost VCC while
the board still has 3V3.

**The per-leg 1k is one-directional.** It is for buffers live into a dead destination: the
'125's absolute-maximum continuous output is +/-25 mA against a +/-20 mA input clamp rating.
The reverse case is self-limiting at ~80 uA through a ~40 kOhm input pull-up. There is none on
the Coordinator line, because the Pi is the board's only 3V3 source -- the buffers cannot be
live into a dead Coordinator.

**The campod accel pigtail is not a straight-through.** Module reads `GND, VCC, CS, INT1,
INT2, SDO, SDA, SCL`; pigtail reads `VCC, CS, INT1, MOSI, GND, MISO, GND, SCL`. `MOSI` lands
on `SDA` and `MISO` on `SDO` -- the silkscreen carries the I2C names. One ground, on pin 20;
pin 25 is n/c and `INT2` is not carried.

**An ADXL345 with `CS` tied low reads as BOTH chips being absent.** It is permanently selected
and drives MISO through every transaction aimed at the other chip, so one miswired chip select
gives `DEVID 0x00` on both. That was `CE1` landed on pin 25 -- a ground -- rather than 26.

**`GET /pods` carries the accel block and costs the devices nothing.** `GET /status` is
`quiesced('coord version')` and SIGTERMs every container on the device. Do not pay the quiesce
to read something the retained documents already have.

**There is no per-antenna RSSI and that is a consequence of a choice.** The link runs ELRS
**Link Mode MAVLink**, so
`SERIAL6_PROTOCOL = 2` and the FC sees `RADIO_STATUS` rather than the CRSF LinkStatistics
frame where the per-antenna numbers live. `RAD.RSSI`/`RemRSSI` are the two **ends** of the
link, not two antennas. Not a deficiency to fix.

**AM32's telemetry current has no ArduPilot-side scale.** Its constant is `MILLIVOLT_PER_AMP`, an
**AM32 compile-time `#define`** in `Inc/targets.h`, coupled to `CURRENT_OFFSET`; not an ArduPilot
parameter. The battery monitor no longer uses it (PM02D), but the per-ESC current in telemetry
still carries the generic scale.

**TBS silkscreens `S5`-`S12` where ArduPilot says `M5`-`M12`**; there are no `M9`-`M13` pads.
`S12` carries the PPS leg into the FC as `CAM1_FEEDBAK_PIN` (61). `SERIAL1` and `SERIAL9`
are unused; `SERIAL7` is the mLRS receiver (`ardupilot/inputs/mlrs-link.param`).

**The ELRS antenna is not "known" to be anchored anywhere but where he put it, and the
handoff's 900 MHz line was wrong.** The previous version of this file said 900 MHz was "ruled
out"; he corrected it to a preference with an exception. Record his rulings as he states them,
not as the strongest version of them.

**A 555 is still made.** Asked for a ~20 ms square-wave LED to check that the cameras fire
together: NE555P (bipolar, 4.5-16 V) and TLC555CP (CMOS, 2-15 V, rail-to-rail output) are both
active in PDIP-8. For 50% duty on a CMOS part, one resistor from pin 3 to the timing cap on
pins 2/6, pin 7 open: period ~1.39 RC, so 15 k and 1 uF gives ~21 ms. Use a coloured LED, not
white, so phosphor decay does not smear the edge.

**A dead-seeming FC with all four ESCs beeping was a wiped parameter store**, not a brownout
-- `FRAME_CLASS = 0` means no motors module, so no DShot. `STAT_BOOTCNT` told the story.

## How to work here

**Stay in the lane.** The worst failures in the session that produced this file were drifting
out of it: asked to record which log fields carry GPS and RSSI, I correlated fix status
against byte rates, narrated a sortie, and wrote a field up as "dead" -- in a file that
already defined it, already had a worked analysis using it, and already listed the open
question under *Qualified ignorance*. Three sections up from where I was typing.

**Read the document before adding to it.** Not a grep for a term you guessed at.

**A challenge means evaluate the claim, not reword it.** Support it with evidence or drop it.
A second and third framing with no new evidence behind either is escalation in the costume of
responsiveness. Three framings of one unsupported claim is what that looked like.

**Do not warn him about mistakes he would not make.** Asked which header pins a connector goes
on, I added cautions about wiring 3V3 to 5V and about MOSI/MISO being crossed -- the second
time that session, having already been told the names are the entire point. If the error you
are guarding against requires him not to know something basic about his own hardware, it is
not a caution.

**Thinking aloud is not a request.** He mentioned needing to work out how to mount the SE pod
without disturbing the antenna; I produced a design proposal and was told to stop.

**Read an issue before characterising it.** Calling coordinator#674 a top risk without reading it
put a made-up ranking in front of him; its comments already held the explanation and the test.

**Config comments say what a value does now.** No "was", no history, never a comment about another
comment.

**Reread the incident record before reasoning about a cause.** Asked whether the ESC could have
started the NW fire, I offered crash impact as a third story and said the log ordering was not
recorded; the crash README already had both the in-air onset and the ordering. Open the record
first, then answer from it.

**Answer the electrical question at the level he asks it.** He knows the hardware; he wants
failure mechanisms and the measurement that separates them, not a probability you cannot back.
"I can't give you a number" followed by the mechanisms and the multimeter check worked.

**His statements about his own build are given, not claims to verify.** And record what he
tells you -- do not fill in the parts he did not say. Writing a hole position as an as-built
fact when he had only told me which chip output he used was a correction in that session.

**Every PR off fresh `origin/main`, targeting `main`. Never stack.** Ordering goes in the
title or body, never the base branch. Work in this workspace's `coordinator` checkout, on
a branch. Verify the branch before committing: a commit pushed onto an already-merged
branch is lost, which happened once here.

**When he states a change, fix every doc it makes false, now, with what he said.** Delete what it
obsoletes. His level of detail is the record; asking for more before updating held his update
ransom.

**One PR per conversation topic; a description of a line or two.** Weights and a build-log entry
for the same rebuild are one PR. A description restating a doc diff is read twice for nothing.

**Do not hand him a reason for his own choices.** The build log gave one reason for the cheese plate
and I argued from it; he bought it for a different one. Telling him why he did something, or that
he contradicts himself, is the tell.

**Cross-session messages are unreliable.** Four messages to another session's socket expired
undelivered with no prompt shown to him, and he was attached at the time. A finding that needs
to survive belongs in the repo, not in a message.
