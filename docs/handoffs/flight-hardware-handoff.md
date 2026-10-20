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

## Where things stand (2026-10-20)

**The PPS chain works.** Board built, powered from the Coordinator, chrony holding
`Reference ID: PPS` at stratum 1, 2.9 us RMS, both campods synced over the gadget link. Chip,
SPI link, first-tier buffer and falling-edge capture all confirmed on hardware.

**Open PRs from this role:** #520 (log message availability for GPS and RF), #558 (PPS board
as-built status and what the LED tells you), #574 (campod accel harness runs one ground;
campod-se resolution).

Open on the hardware side:

- **Two of the five PPS pigtail legs do not pass a signal.** Adapters excluded by swapping
  them between legs. Candidates: that channel's `OE` tie to the GND bus, its input tie, the
  series resistor or its joints, the pigtail solder, the connector pins. Neither package is
  dead -- the Coordinator runs off U2, the LED off U3. Which two legs was not established.
- **The PPS board is not mounted.** Needs taping up and a location.
- **The Coordinator connector is a 2x4 block on solid-core wire and has been knocked off
  once.** Solid core puts harness load straight into the header instead of flexing. Kapton
  over it for flight.
- **The PC1 current sensor is unresolved.** Reads a fixed 9.92 V; `Cur2` (PA7) shows a stable
  0.667 MOhm to GND while PC1 climbs. `BATT_MONITOR = 9` current numbers are junk and the
  calibration constant is not an FC parameter -- see below.
- **The ELRS antenna anchoring.** The horizontal antenna is mounted on the tail of the zip tie
  that strain-relieves the rear power lead, so load applied at the rear bay reaches the
  micro-U.FL at the receiver. Mounting the SE pod has disturbed it before. **This is his to
  figure out and he has said so explicitly. Do not propose a fix.**

Under consideration, his call and not a decision: a second radio carrying RTCM separately from
the Boxer's telemetry -- a full-size RadioMaster Ranger as a fixed attic install running mLRS
in modem mode, paired with a second receiver on the vehicle. 900 MHz was ruled out: losing
2.4 GHz means losing control, so a corrections link that outlives it only logs the crash site.

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
**Link Mode MAVLink**, which is what gives the FC its GCS link and the RTCM injection path, so
`SERIAL6_PROTOCOL = 2` and the FC sees `RADIO_STATUS` rather than the CRSF LinkStatistics
frame where the per-antenna numbers live. `RAD.RSSI`/`RemRSSI` are the two **ends** of the
link, not two antennas. Not a deficiency to fix.

**`BATT_MONITOR = 9` applies no multiplier.** `AP_BattMonitor_ESC.cpp` sums ESC currents
directly; `BATT_AMP_PERVLT` and `AMP_OFFSET` are analog-only. The scaling constant is
`MILLIVOLT_PER_AMP`, an **AM32 compile-time `#define`** in `Inc/targets.h`, coupled to
`CURRENT_OFFSET`. It is not an ArduPilot parameter and cannot be set from Mission Planner.

**TBS silkscreens `S5`-`S12` where ArduPilot says `M5`-`M12`**; there are no `M9`-`M13` pads.
`S11` carries the PPS leg into the FC as `CAM1_FEEDBAK_PIN`. `SERIAL1`, `SERIAL7` and
`SERIAL9` are unused.

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

**His statements about his own build are given, not claims to verify.** And record what he
tells you -- do not fill in the parts he did not say. Writing a hole position as an as-built
fact when he had only told me which chip output he used was a correction in that session.

**Every PR off fresh `origin/main`, targeting `main`. Never stack.** Ordering goes in the
title or body, never the base branch. Work in the clone at `/home/jovyan/coordinator`, on a
branch -- not in a worktree he cannot see. Verify the branch before committing: a commit
pushed onto an already-merged branch is lost, which happened once here.

**Cross-session messages are unreliable.** Four messages to another session's socket expired
undelivered with no prompt shown to him, and he was attached at the time. A finding that needs
to survive belongs in the repo, not in a message.
