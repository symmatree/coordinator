# Central hub / power distribution

[Back to index](README.md)

Central power and data distribution, built around the **Coordinator** (Raspberry Pi 4B). Maybe housed in the base / back of the oak-d mount or in a separate backpack. Unit as a whole has a lot of connections that we should pre-route for access before locking things down.

## Components

### USB 2.0 hub (as built)

A **passive 4-port USB 2.0 hub**. Its upstream port goes to the Coordinator; each Pi Zero's
**data** micro-USB goes to a hub port. Every Zero then takes a **separate 5 V pigtail from
the UBEC** into its **power** micro-USB.

**Why both, when the hub alone would enumerate them.** The Zeros do back-power through the
hub -- the Coordinator feeds it -- but not with enough current to run reliably. The direct
pigtail is what actually powers them; the hub carries data and whatever trickle it can.
That also keeps the Zeros' draw off the Coordinator's downstream port budget, which a
passive hub has no way to supply.

This is the "data-only hub links + 5 V injected at each Zero" shape described as an option
in earlier revisions of this doc, arrived at by using a passive hub rather than by
modifying a powered one. The per-pod 5 V pigtails exist anyway (the OAK-D needs a barrel
jack, so the distribution wiring is there regardless), and running them to each Zero turned
out simpler than making one hub carry both.

The **PPS + signal-ground** pair from the buffer board ([PPS distribution board](#pps-distribution-board))
is a third connection per pod, independent of both.

Scaling: one hub covers the current 4-camera ring. The horizontal ring of 8 plus the upward
pair from the vertical ring ([campod.md](../campod.md)) needs more -- four logical USB 2.0
trees for 16 Zeros, or two for the 8-camera ring, each tree's upstream on its own
Coordinator port.

**Open, from when this was a plan rather than a build:** whether `dwc2` peripheral/gadget
mode is sensitive to VBUS state on this exact hub and wiring. The link works, so it is not
a problem in the current configuration; it is worth knowing if the power topology changes.

### Central Raspberry Pi (currently a 4B)

Call this the central or MAVLink Pi so we don't freak everybody out if we go back to a Raspberry Pi 5 at some point.

* Runs VIO along with the OAK-D (has internal IMU)
* Bridges pi zeros to each other (virtual network adapters over USB)
* Serves NTP to pi zeros; disciplines the shared **DS3234** from GNSS/MAVLink time when available
* Listens to MAVLink to FC for pose and time hints
* Sends position estimates over MAVLink to FC
* Sends depth field-based obstacle-distance messages to the FC

### 10A 5V UBEC

Has its own capacitor wired to its input side.

Hardware source: Castle Creations CC BEC 2.0 from ReadyMadeRC.

* Peak current 14A
* For 4.75-7.0V output: 9A continuous
* Default setting 5.25V

TODO: I'd like to get voltage off its output and current from the input. The matek sensor is ridiculous since we draw maybe 2A on the upstream side, we need like a 5A sensor. Gemini says a unidirectional ACS724 with 5A or 10A should be perfect.

### PPS distribution board

Uses a buffer IC in DIP package to remove load on the [**DS3234**](https://www.sparkfun.com/sparkfun-deadon-rtc-breakout-ds3234.html) **SQW** line (1 Hz), not the GNSS module.

- **Camera pod connectors:** 2-wire JST SM (20 AWG): signal ground, 3.3 V PPS (from buffer). JST SM housings must be **zip-tied/anchored to the frame** to prevent pendulum vibration from fatiguing wires.
- **Upward pair (NNW + NNE):** Two additional PPS outputs needed for the early vertical-ring cameras ([campod.md](../campod.md), *Upward-looking cameras*). Counting the Coordinator and the FC, the full build is **12 buffered lines**; the two-tier scheme in [PPS signal buffering](#pps-signal-buffering) reaches 16 with four quad packages, and 32 if a second-tier package is swapped for an octal.
- **Hub ports:** The upward pair gets the **first vertical-ring USB hub** -- a small 4-port unit with two ports used now and two spare for future vertical-ring cameras. This hub's upstream port connects to a free USB 2.0 port on the Coordinator.

### 5V distribution board

Sends around 5V. Hopefully the ElectroCookie traces carry power well, but we don't need a ton.

Board reference: ElectroCookie snappable stripboard from Amazon.

---

## Connections (end-to-end endpoints)

### Pi 4B

* Power from payload 5V rail (stripboard) via usb-c pigtail (20 AWG, Amazon)
* Data from OAK-D into USB 3.0
* Data from TBS Lucid FC USB port (confirm 2.0 or 3.0)
* Data from USB hub into USB 2.0 port (hub's USB-A upstream port)

## Stripboard 5V distribution

* 5V in from UBEC
* Barrel jack to OAK-D (dimensions in datasheet, pigtails acquired)
* One 5 V pigtail per Pi Zero, into the Zero's **power** micro-USB (the hub is passive and
  carries data only -- see [USB 2.0 hub](#usb-20-hub-as-built))
* Power to rpi 4b (usb-c pigtail, 20 AWG)
* UBEC-output-sensing voltage to FC 2nd-voltage pin

## Stripboard PPS distribution

**One board, at the front.** The earlier plan was one per macro-pod of 4 Zeros, sited next
to that pod's USB hub; that was to keep the fan-out near its consumers. It is unnecessary
after the buffer: each output is a point-to-point run with its own push-pull driver, so
nothing is shared between consumers and distance stops being a shared-node problem -- see
[PPS signal buffering](#pps-signal-buffering).

* **PPS-in** from **DS3234 SQW** (one RTC breakout, mounted on this board -- see [PPS signal buffering](#pps-signal-buffering) for why it cannot sit at the end of a cable; SPI to Coordinator for discipline from GNSS when available)
* 2-wire PPS (after buffer) and signal ground to each pi zero
* Connector reference: VISDOLL JST SM connector kits (Amazon)


### PPS signal buffering

**Buffer:** 3.3 V quad 3-state buffer, **SN74AHC125N** (PDIP-14), powered from the Coordinator's
3.3 V rail. Note **AHC, not AHCT** -- the AHCT part shares the package and pinout but is specified
for VCC 4.5-5.5 V and is out of spec on this rail.

**Topology: two tiers.** One '125 is the first tier, with all four of its inputs tied to SQW; each
first-tier output drives all four tied inputs of a second-tier '125. Four packages gives 16 outputs
against the 12 planned (10 Zeros + Coordinator + FC).

The **Coordinator takes a buffered output like every other consumer**, not the raw SQW line, so it
sees the same electrical and timing path as the pods.

> **Superseded:** an earlier version of this section said to tie every buffer input directly to SQW
> and warned against a "preamp" gate on skew grounds. The skew reasoning was sound as far as it went
> -- a tier does add a propagation stage -- but it is the wrong order of magnitude to decide on. AHC
> propagation delay at 3.3 V is 4-5 ns against a PPS budget measured in microseconds, and every
> output passes through the same number of stages, so the tier costs nothing measurable. What
> settles it instead is the **input transition rate**, below.

**Why a tier rather than a flat fan-out.** SQW is the DS3234's open-drain `INT/SQW` pin, so its
rising edge is not driven -- it is the RC of the pull-up against whatever input capacitance hangs on
the node. AHC125 specifies a **maximum input transition rate of 100 ns/V at VCC = 3.3 V**. With
worst-case `Ci` of 10 pF per input, a flat fan-out of twelve inputs is ~130 pF and needs a ~1.2k
pull-up to stay in spec, which draws 2.7 mA of the DS3234's 3 mA sink budget. Four inputs is ~50 pF
and a 2.2k pull-up holds ~90 ns/V at 1.6 mA. The tier buys margin on the one node that has none.

DC load was never the constraint: AHC inputs draw ~1 uA each, so twelve of them are 0.4% of what the
pin can sink. Note also that the on-board 10k is outside the transition-rate spec **even driving a
single input**, so this is a resistor value on the breakout rather than anything caused by fan-out.

Keep the RTC breakout on the same board as the buffers. Hookup wire runs about 1 pF/cm, so six
inches of cable between SQW and the first tier adds ~15 pF to a ~50 pF budget and puts the edge back
out of spec.

#### Package pinout (SN74AHC125N, PDIP-14)

Channels 1 and 2 run OE-A-Y down the left side; channels 3 and 4 run Y-A-OE up the right. The halves
are mirrored, which is the easiest thing to get wrong when laying out by eye.

| pin | signal | | pin | signal |
|-----|--------|---|-----|--------|
| 1 | 1OE | | 14 | VCC |
| 2 | 1A | | 13 | 4OE |
| 3 | 1Y | | 12 | 4A |
| 4 | 2OE | | 11 | 4Y |
| 5 | 2A | | 10 | 3OE |
| 6 | 2Y | | 9 | 3A |
| 7 | GND | | 8 | 3Y |

#### Board wiring

Three packages for the current build: **U1** first tier, **U2** front distributor, **U3** rear
distributor. U1's two unused outputs are live spares for future second-tier packages -- the first
tier never needs revisiting, because four inputs is the maximum a quad can present and the SQW load
therefore cannot grow.

**Common to U1, U2 and U3:**

| pin(s) | to |
|--------|-----|
| 14 | +3V3 bus |
| 7 | GND bus |
| 1, 4, 10, 13 (all OE) | GND bus -- every channel enabled, so a spare output is a wire and not a rework |
| 14 to 7 | 0.1 uF, at the package |

**Per package:**

| | inputs (2, 5, 9, 12 tied) | 1Y (3) | 2Y (6) | 3Y (8) | 4Y (11) |
|---|---|---|---|---|---|
| **U1** first tier | RTC `JP1.5` (SQW) | U2 inputs | U3 inputs | spare | spare |
| **U2** front | U1 pin 3 | FC feedback pin | Coordinator header 18 | NE campod | NW campod |
| **U3** rear | U1 pin 6 | SE campod | SW campod | LED (optional) | spare |

**RTC breakout header `JP1` (7-pin, 0.1"):**

| JP1 | signal | to |
|-----|--------|-----|
| 1 | SS | Coordinator header 24 (CE0) |
| 2 | MOSI | Coordinator header 19 |
| 3 | MISO | Coordinator header 21 |
| 4 | SCLK | Coordinator header 23 |
| 5 | SQW | U1 pins 2, 5, 9, 12 |
| 6 | VCC | +3V3 bus |
| 7 | GND | GND bus |

MOSI and MISO are **not** crossed: the breakout's silk is bus-perspective, and the v1.1 schematic
confirms `JP1.2 = MOSI = U1.DIN` and `JP1.3 = MISO = U1.DOUT`.

**Passives on the board:**

| ref | value | between | why |
|-----|-------|---------|-----|
| R_pu | **2.2k** | `JP1.5` - `JP1.6` | in parallel with the breakout's own 10k; sets the SQW rise to ~90 ns/V at 1.6 mA of a 3 mA sink budget |
| C_rtc | **0.1 uF** | `JP1.6` - `JP1.7` | the DS3234 bypass the datasheet asks for; the board ships with only 22 pF |
| C_U1..U3 | **0.1 uF** each | pin 14 - pin 7 of each package | four channels slewing together pull 160-260 mA for a few ns; bulk cannot substitute, because the inductance between bulk and package is what is being bypassed |
| C_bulk | **10 uF** | +3V3 - GND at the board entry | |
| R_led | **1k** | U3 pin 8 - LED anode | ~1.4 mA, inside the 4 mA output rating. Optional, but `INTCN` defaults to interrupt mode, so "powered but nobody configured the RTC" otherwise presents as every line sitting steady at 3.3 V |

**Not on this board, fitted at the destination end:**

| part | where | why |
|------|-------|-----|
| 1k series in the signal line | campods and FC | limits clamp current to ~3 mA against a +/-20 mA input clamp rating if that destination is unpowered while the buffers are live. **Not** on the Coordinator line -- the buffers are powered from that Pi, so it cannot be the unpowered one |
| 100 ohm series in the signal ground | campods | keeps the thin signal-ground wire from becoming a fault-current path (see [campod.md](../campod.md)) |

Lay the +3V3 and GND rows on bare bus wire rather than solder bridges -- same inductance reasoning as
the per-package decoupling. Bring SQW and one buffer output out to test points; every edge-rate
figure here is computed from worst-case datasheet capacitance, not measured.

#### Net list

The same wiring by net rather than by component -- the axis you want when checking a
half-built board, since you can put a meter on one net and tick off every pin that should
be on it. Machine-generated from the same source as
[`pps-board.kicad_sch`](pps-board.kicad_sch), so the two agree by construction.

| net | pins | note |
|-----|------|------|
| **+3V3** | `J1.1 (hdr17)`, `J2.6 (VCC)`, `R1.2`, `C1.1`, `C5.1`, `U1.14 (VCC)`, `C2.1`, `U2.14 (VCC)`, `C3.1`, `U3.14 (VCC)`, `C4.1` | from Coordinator header pin 17; RTC and all buffers share this rail |
| **GND** | `J1.4 (hdr20)`, `J2.7 (GND)`, `C1.2`, `C5.2`, `U1.7 (GND)`, `U1.1 (1OE)`, `U1.4 (2OE)`, `U1.10 (3OE)`, `U1.13 (4OE)`, `C2.2`, `U2.7 (GND)`, `U2.1 (1OE)`, `U2.4 (2OE)`, `U2.10 (3OE)`, `U2.13 (4OE)`, `C3.2`, `U3.7 (GND)`, `U3.1 (1OE)`, `U3.4 (2OE)`, `U3.10 (3OE)`, `U3.13 (4OE)`, `C4.2`, `D1.1`, `J3.2`, `J4.2`, `J5.2`, `J6.2`, `J7.2` | all OE pins tied low so every channel is enabled |
| **SQW** | `J2.5 (SQW)`, `R1.1`, `U1.2 (1A)`, `U1.5 (2A)`, `U1.9 (3A)`, `U1.12 (4A)` | open-drain; R1 parallels the breakout's own 10k. Rising edge is RC, falling is driven |
| **SPI_CS** | `J1.8 (hdr24)`, `J2.1 (SS)` |  |
| **SPI_MOSI** | `J1.3 (hdr19)`, `J2.2 (MOSI)` |  |
| **SPI_MISO** | `J1.5 (hdr21)`, `J2.3 (MISO)` |  |
| **SPI_SCLK** | `J1.7 (hdr23)`, `J2.4 (SCLK)` |  |
| **T2_FRONT** | `U1.3 (1Y)`, `U2.2 (1A)`, `U2.5 (2A)`, `U2.9 (3A)`, `U2.12 (4A)` |  |
| **T2_REAR** | `U1.6 (2Y)`, `U3.2 (1A)`, `U3.5 (2A)`, `U3.9 (3A)`, `U3.12 (4A)` |  |
| **PPS_COORD** | `U2.6 (2Y)`, `J1.2 (hdr18)` | returns on header pin 18 = BCM GPIO24 |
| **PPS_FC** | `U2.3 (1Y)`, `J3.1` |  |
| **PPS_NE** | `U2.8 (3Y)`, `J4.1` |  |
| **PPS_NW** | `U2.11 (4Y)`, `J5.1` |  |
| **PPS_SE** | `U3.3 (1Y)`, `J6.1` |  |
| **PPS_SW** | `U3.6 (2Y)`, `J7.1` |  |
| **LED_DRV** | `U3.8 (3Y)`, `R2.1` |  |
| **LED_A** | `R2.2`, `D1.2` |  |
| **T1_SPARE1** | `U1.8 (3Y)` | first-tier spare output, live, for a future second-tier package |
| **T1_SPARE2** | `U1.11 (4Y)` | first-tier spare output, live, for a future second-tier package |
| **U3_SPARE** | `U3.11 (4Y)` | rear distributor spare output |

`J1.6` (header pin 22, BCM GPIO25) is the deliberate spare and carries a no-connect.

#### Protoboard layout

Recording intended connections for soldering.

5x7cm protoboard, columns A-S, numbered bottom to top, 01 to 24.

```
Components:

U1: pin 1 at C-07, pin 8 at F-01
U2 (FRONT): pin1 at C-15, pin 8 at F-09
0.1 uF: H-07 /  H-05
0.1 uF: H-15 / H-13


N/C:

F-01 (U1.8)
F-04 (U1.11)

bridges:

C-07 (U1.1) / B-07  | GND
C-04 (U1.4) / B-04 | GND
F-03 (U1.10) / G-03 | GND
F-06 (U1.13) / G-06 | GND
C-01 (U1.7) / B-01 / A-01 | GND

F-07 (U1.14) / G-07 / H-07 | 3V3

H-05 / H-04 | GND (cap)

C-06 (U1.2) / B-06 / A-06  | SQW
C-03 (U1.5) / B-03 / A-03 | SQW
F-02 (U1.9) / G-02 | SQW
F-05 (U1.12) / G-05 | SQW

C-05 (U1.3) / B-05 | T2_FRONT
C-11 (U2.5) / B-11 / A-11 | T2_FRONT
C-14 (U2.2) / B-14 / A-14 | T2_FRONT
F-10 (U2.9) / G-10 | T2_FRONT
F-13 (U2.12) / G-13 / H-13 | T2_FRONT

C-15 (U2.1) / B-15 | GND
C-12 (U2.4) / B-12 | GND
F-11 (U2.10) / G-11 | GND
F-14 (U2.13) / G-14 | GND

C-09 (U2.7) / B-09 / A-09 | GND

F-15 (U2.14) / G-15 / H-15 | 3V3

H-13 / H-12 | GND (cap)

C-02 (U1.6) / B-02 | T2_REAR





jumpers:

B-07 / GND | GND
B-04 / GND
G-03 / GND
G-06 / GND
B-01 / GND

G-07 / 3V3
H-04 / A-01 | GND (cap)


B-06 / B-03 | SQW
A-03 / G-02 | SQW
H-02 / G-05 | SQW

B-05 / B-11 | T2_FRONT
A-11 / B-14 | T2_FRONT
A-14 / G-13 | T2_FRONT
H-13 / G-10 | T2_FRONT

B-15 / GND
B-12 / GND
G-11 / GND
G-14 / GND
B-09 / GND

G-15 / 3V3

H-12 / C-09 | GND (cap)

```

#### Schematic

[`pps-board.kicad_sch`](pps-board.kicad_sch) is the same circuit as a KiCad sheet.
Open [`pps-board.kicad_pro`](pps-board.kicad_pro) beside it -- KiCad 10 will not edit a
schematic without a project. The sheet is written in the **KiCad 6 file format** by
`kiutils`, which is the dialect that library emits; KiCad migrates it on open and will
offer to save it in the current format. Connectivity is by **net label** rather than routed wires, so
every net name is legible as text and nothing depends on a wire endpoint landing within a
hair of a pin.

The `SN74AHC125N` symbol is **package-shaped** -- one 14-pin rectangle with pins in DIP
order -- rather than KiCad's stock four-gates-plus-a-power-unit, so it reads directly onto
a perfboard layout: pin 7 is where pin 7 is. It is embedded in the sheet, so the file needs
no library beyond KiCad's stock `Device`, `Connector_Generic` and `Package_DIP`.

It is checked structurally after generation: read back through `kiutils`, every `lib_id`
resolves, no duplicate designators, every wire begins exactly on a pin, every label sits on
a wire end and names that pin's net, and the one unconnected pin (`J1.6`) carries a
no-connect. Expect to nudge placement -- connectivity is what is verified, not layout.

#### Coordinator SPI block -> RTC breakout

A single **2x4** on header pins **17-24** carries power, SPI and the returning PPS. It is
deliberately a different shape from the campod's 2x5 on the same ten positions, so the two harnesses
cannot cross-mate.

| header pin | BCM | signal | to |
|------------|-----|--------|-----|
| 17 | -- | 3V3 | board +3V3 bus (RTC + all buffers) |
| 18 | GPIO24 | **PPS in** | U2 pin 6 -- a buffered output, not SQW. Overlay line is `dtoverlay=pps-gpio,gpiopin=24` |
| 19 | GPIO10 | MOSI | `JP1.2` |
| 20 | -- | GND | board GND bus |
| 21 | GPIO9 | MISO | `JP1.3` |
| 22 | GPIO25 | spare | -- |
| 23 | GPIO11 | SCLK | `JP1.4` |
| 24 | GPIO8 | CE0 | `JP1.1` (SS) |

Pins 25-26 (the campod block's second ground and CE1) are not in this harness: one return and one
chip select is all this board needs.

**Header position 18 and BCM 18 are different pins** -- BCM 18 is header position 12. The
`gpiopin=18` placeholder that used to appear in this doc referred to the latter.

`dtparam=spi=on` is not currently set by the coordinator Ansible role; only the campod role sets it.

On the campod end, PPS lands on **header 13/14 (GPIO27 + GND)** as its own 2x1, deliberately not
sharing the accelerometer shell -- an unrelated pair of wires in that housing could not be
disconnected independently. BCM 9-27 come up pulled down, so an absent or unpowered distribution
board reads as "no pulses" rather than a floating line inventing edges.

### USB hub

* Passive -- **no** rail of its own; it draws from the Coordinator's port
* Upstream port to central bridge Pi's USB 2.0
* Downstream ports to each Zero's **data** micro-USB; the Zeros' power comes from the
  5 V rail directly ([USB 2.0 hub](#usb-20-hub-as-built))

### UBEC

* Power input: XT60 to upstream splitter
* Power output: 5V to stripboard rails for distribution
* Castle Link USB Programming Kit V3 (ReadyMadeRC) is the service/programming tool for CC BEC configuration
