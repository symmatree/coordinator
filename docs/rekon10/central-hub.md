# Central hub / power distribution

[Back to index](README.md)

Central power and data distribution, built around the **Coordinator** (Raspberry Pi 4B). Maybe housed in the base / back of the oak-d mount or in a separate backpack. Unit as a whole has a lot of connections that we should pre-route for access before locking things down.

## Components

### Coordinator Rpi

Raspberry pi 4B.

* Connects to FC for Mavlink.
* Bridges pi zeros over usb gadget network.
* Serves time, MQTT, other coordination to zeroes.

Connections:

* Power from payload 5V rail via usb-c pigtail
* Data from OAK-D into USB (OAK-D supports 2.0)
* UART to FC
* Hub's USB-A upstream cable

### 5V supply and distribution

Castle Creations CC BEC 2.0

* Has its own capacitor wired to its input side.
* Peak current 14A
* For 4.75-7.0V output: 9A continuous
* Default setting 5.25V

Via WAGO or lever-nut multi-connector busses, daisy-chained as needed. Directly feeds pigtails for micro-usb,
usb-c, barrel jack, etc.

### USB 2.0 hub

A **passive 4-port USB 2.0 hub**, upstream port to the Coordinator. Each Pi Zero connects
twice: its **data** micro-USB to a hub port, and its **power** micro-USB to a 5 V pigtail
from the UBEC. The **PPS + signal-ground** pair from the
[PPS](#pps) is a third connection.

The hub is passive -- no supply of its own -- so anything it passes to a Zero comes out of
the Coordinator's downstream USB budget, which on a Pi 4B is **1.2 A total across all four
ports**. That figure is fixed and does not depend on how the Pi itself is powered; the Pi 5
is the model that cuts its downstream allocation to 600 mA unless it detects a 5 A supply
([power supplies](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#power-supply)).

Four Zeros idling would plausibly fit inside 1.2 A; four capturing would not. Published
Zero 2 W figures run roughly 120-250 mA idle against 300-580 mA under load, before the
camera, and the measured payload rail here goes from 1.25 A at rest to 2.0 A once
collection starts with a single pod fitted. So back-power alone would come up on the bench
and brown out when capture began.

**The OAK-D sits on that same budget.** With its barrel jack unpowered it falls back to USB
power, so the Coordinator's 1.2 A would have to cover the camera and every Zero together.
Without power on the pigtails the payload does not run.

A powered hub would also carry the load -- its own supply is 5 V at 2.5 A, enough for four
Zeros. It is not that the hub concentrates current where the pigtails do not: the UBEC and
its Wago rail are a shared bus as well, and every pigtail hangs off it. The difference is
that the hub would be a **second** shared element in series behind the UBEC, a consumer
part whose internal distribution is built to USB's 500 mA per port, and one whose failure
takes out power *and* data for everything on it. A pigtail failure costs one device.

With the full build -- the horizontal ring of eight plus the upward pair, **ten Zeros**
([campod.md](../campod.md)) -- there would be three such hubs and three more supplies to
feed, while the UBEC is already rated for the whole payload.

### PPS

Timing signal to coordinate campod and coordinator clocks precisely.

This section owns the **signal**: the fan-out, which output drives which consumer, the
pull-up arithmetic, and which edge is the second. What software does with it -- chrony's
reference, when the chip is written, what a correction looks like where it arrives -- is in
[time-base.md](../time-base.md).

**Buffer:** 3.3 V quad 3-state buffer, **SN74AHC125N** (PDIP-14), powered from the Coordinator's
3.3 V rail. **AHC, not AHCT** -- the AHCT part shares the package and pinout but is specified for
VCC 4.5-5.5 V and is out of spec on this rail.

**Topology: two tiers.** The '125's four channels are independent, each with its own input pin, so
a fan-out is made by tying inputs together. U1 is the first tier, its four inputs tied to SQW; each
of its outputs drives the four tied inputs of a second-tier '125. Four packages gives 16 outputs
against the 12 planned (10 Zeros + Coordinator + FC). The Coordinator takes a buffered output like
every other consumer rather than the raw SQW line, so it sees the same electrical and timing path
as the pods.

**The tier exists because SQW drives four inputs far more easily than twelve.** SQW is the DS3234's
open-drain `INT/SQW` pin, so its rising edge is not driven -- it is the RC of the pull-up against
whatever input capacitance hangs on the node, and AHC125 specifies a maximum input transition rate
of **100 ns/V at VCC = 3.3 V**. At a worst-case `Ci` of 10 pF per input, twelve inputs is ~130 pF
and needs a ~1.2k pull-up to stay in spec, drawing 2.7 mA of the DS3234's 3 mA sink budget. Four
inputs is ~50 pF, where a 2.2k pull-up holds ~90 ns/V at 1.6 mA.

Current is not the limit at either number -- AHC inputs draw ~1 uA each, so twelve of them are 0.4%
of what the pin can sink. The breakout's own 10k pull-up is outside the transition-rate spec even
driving a single input, so the resistor value is set by the breakout, not by the fan-out.

The RTC breakout sits on this board rather than at the end of a cable: hookup wire runs about
1 pF/cm, so six inches between SQW and the first tier would add ~15 pF to a ~50 pF budget and put
the edge back out of spec.

**The falling edge is the top of the second, and it is also the driven one -- inferred, not
yet measured.** The DS3234 datasheet (p. 11): "The countdown chain is reset whenever the
seconds register is written [...] The 1Hz square-wave output, if enabled, transitions high
500ms after the seconds data transfer." The seconds register increments at write+*n* and the
output goes high at write+0.5+*n*, so the rising edge sits on the half second -- **provided
the output is 50% duty, which the datasheet does not state anywhere.** It is near-certain:
the 1 Hz is a tap off the same binary divider chain that clocks the seconds counter, so it
is symmetric by construction. But that is an argument from how the part must work, not a
line to cite, and the timing design rests on it.

Two bench tests settle it, neither needing a scope. Poll the seconds register over SPI and
compare the monotonic timestamp of the increment against the PPS timestamps for each edge --
whichever edge the increment coincides with is the second. Or set `capture_clear=1` on the
overlay and read the assert-to-clear spacing; 0.5 s confirms the duty.

If it holds, the edge worth timestamping and the fast edge are the same one, since
`INT/SQW` is open-drain: the falling edge is actively pulled down while the rising edge is
the pull-up's RC curve. The design did not arrange that and got it anyway.

**R1 does not shape the timestamped edge at all.** The '125 is non-inverting with a
push-pull output, so what the consumer sees on the falling edge is driven by the buffer, not
by any pull-up. R1 shapes only the **rising** edge at the '125's *input*, where the 100 ns/V
transition spec applies -- it stops the input dwelling in its linear region, which would
produce spurious transitions and corrupt the whole waveform including the falling one. The
consequence worth having: the jitter eventually measured is the part plus the buffer, with
no hardware term anyone can tune.

**Writing the seconds register moves the edge, and the design does write it.** The chain
resets on a write, so the correction both steps the phase and makes one second the wrong
length. That is survivable here because the pulse and the seconds label come from the same
chip: every consumer takes a buffered copy of the same edge, so a correction moves label and
edge together and steps everyone at the same instant by the same amount. Common-mode, not
divergence. The window is disarmed-on-the-ground, where nothing is aligned to the tick;
software policy is on [coordinator#11](https://github.com/symmatree/coordinator/issues/11).

**What the output does during that write is undefined.** The datasheet gives the state
500 ms after the seconds transfer and says nothing about the interval between. So depending
on where in its cycle the write lands, the output can produce a **runt pulse** -- a high or
low period far shorter than 500 ms -- or **no transition at all** where one was due, or
nothing unusual. All three are electrically ordinary and the buffer passes any of them
faithfully, so none of it is visible on this board; the consequence lands at the consumers,
and what each of them sees is in [time-base.md](../time-base.md). Not worth designing
around, because the disturbance is the size of the phase correction being applied and no
landing instant avoids it.

**Rate trimming is possible without a discontinuity, with a caveat.** The aging-offset
register (`10h/90h`, two's complement, p. 16) adds or removes oscillator capacitance at the
next temperature conversion or on a manual `CONV` -- a rate change, no phase step. But its
effect is strongly temperature-dependent: the datasheet's per-LSB sensitivity is **0.1 ppm
at +25 C against 0.8 ppm at +85 C** (0.7 at -40 C), and it shifts the whole
frequency-vs-temperature curve. A trim derived on a warm bench is wrong in cold air.

#### Package pinout (SN74AHC125N, PDIP-14)

Channels 1 and 2 run OE-A-Y down the left side; channels 3 and 4 run Y-A-OE up the right.

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

**Passives on the board:**

| ref | value | between | why |
|-----|-------|---------|-----|
| R1 | **2.2k** | `JP1.5` - `JP1.6` | in parallel with the breakout's own 10k; sets the SQW rise to ~90 ns/V at 1.6 mA of a 3 mA sink budget |
| C1 | **0.1 uF** | `JP1.6` - `JP1.7` | the DS3234 bypass the datasheet asks for; the board ships with only 22 pF |
| C2, C3, C4 | **0.1 uF** each | pin 14 - pin 7 of each package | four channels slewing together pull 160-260 mA for a few ns, through whatever inductance lies between the package and the next capacitor out |
| ~~C5~~ | **not fitted** | -- | nothing on this board asks for a reservoir. The buffers' worst moment is four channels slewing together, 160-260 mA for a few ns, which the local 0.1 uF covers with 3.3 mV of droop and then has a second to recharge; the DS3234 draws 400 uA. There is no band between what the 0.1 uF parts cover and DC |
| R2 | **1k** | `H-17` - `K-17` | sets the LED at ~1.4 mA, inside the AHC output's 4 mA rating |
| D1 | **LED**, proof of life | `K-17` anode - `L-17` cathode, cathode jumpered to the GND bus | `INTCN` defaults to interrupt mode, so without a 1 Hz blink a board nobody has configured presents as every line sitting steady at 3.3 V |
| R3, R4, R5, R6, R7 | **1k** each | `C-13`-`C-10`, `I-09`-`I-11`, `I-12`-`I-14`, `C-21`-`C-24`, `C-18`-`C-16` | **buffers live, destination dead.** The '125's absolute-maximum continuous output is +/-25 mA against the destination's +/-20 mA input clamp rating, so the buffer can out-drive what the pin is rated to absorb; 1k makes it ~3 mA. The reverse case -- destination live, buffers dead, which for the FC is the ordinary one since it runs on the pack while the Coordinator may be off -- needs no resistor: that destination's input pull-up is ~40k, so ~80 uA, 0.4% of the clamp rating. **Not** on the Coordinator line, which cannot differ from the buffers either way |

The five leg resistors stand along the free column rather than in-row: the connector's
ground pin lands straight in the GND bus hole, which leaves one hole between it and the
chip pin. Each leg bridges out of the buffer pin, runs the resistor up or down the
column, and bridges across to the connector, so no hole carries two inserted leads.
Position along the run does not matter electrically -- it is series either way -- so
they are here rather than spliced into five cables.

SQW and one buffer output come out to test points. Every edge-rate figure here is computed from
worst-case datasheet capacitance, not measured.

**Which legs are actually populated is not recorded here.** This section is the design --
five buffered outputs and what each drives. Whether a given leg has a wire on it is
deployment state, it changes when somebody solders rather than when the design does, and it
is held in ansible as `coord_pps_wired`, which is read on every converge. Deliberately one
answer in one place: the overlay presents `/dev/pps0` on an unconnected pin, so a populated
leg is not detectable and a stale second copy here would be believed.

#### Net list

The same wiring by net rather than by component, generated from the same source as
[`pps-board.kicad_sch`](pps-board.kicad_sch).

| net | pins | note |
|-----|------|------|
| **+3V3** | `J1.1 (hdr17)`, `J2.6 (VCC)`, `R1.2`, `C1.1`, `U1.14 (VCC)`, `C2.1`, `U2.14 (VCC)`, `C3.1`, `U3.14 (VCC)`, `C4.1` | from Coordinator header pin 17; RTC and all buffers share this rail |
| **GND** | `J1.4 (hdr20)`, `J2.7 (GND)`, `C1.2`, `U1.7 (GND)`, `U1.1 (1OE)`, `U1.4 (2OE)`, `U1.10 (3OE)`, `U1.13 (4OE)`, `C2.2`, `U2.7 (GND)`, `U2.1 (1OE)`, `U2.4 (2OE)`, `U2.10 (3OE)`, `U2.13 (4OE)`, `C3.2`, `U3.7 (GND)`, `U3.1 (1OE)`, `U3.4 (2OE)`, `U3.10 (3OE)`, `U3.13 (4OE)`, `C4.2`, `D1.1 (cathode)`, `J3.2`, `J4.2`, `J5.2`, `J6.2`, `J7.2` | all OE pins tied low so every channel is enabled |
| **SQW** | `J2.5 (SQW)`, `R1.1`, `U1.2 (1A)`, `U1.5 (2A)`, `U1.9 (3A)`, `U1.12 (4A)` | open-drain; R1 parallels the breakout's own 10k. Rising edge is RC, falling is driven |
| **SPI_CS** | `J1.8 (hdr24)`, `J2.1 (SS)` |  |
| **SPI_MOSI** | `J1.3 (hdr19)`, `J2.2 (MOSI)` |  |
| **SPI_MISO** | `J1.5 (hdr21)`, `J2.3 (MISO)` |  |
| **SPI_SCLK** | `J1.7 (hdr23)`, `J2.4 (SCLK)` |  |
| **T2_FRONT** | `U1.3 (1Y)`, `U2.2 (1A)`, `U2.5 (2A)`, `U2.9 (3A)`, `U2.12 (4A)` |  |
| **T2_REAR** | `U1.6 (2Y)`, `U3.2 (1A)`, `U3.5 (2A)`, `U3.9 (3A)`, `U3.12 (4A)` |  |
| **PPS_COORD** | `U3.11 (4Y)`, `J1.2 (hdr18)` | returns on header pin 18 = BCM GPIO24 |
| **PPS_FC** | `U2.3 (1Y)`, `R3.1` |  |
| **PPS_FC_J** | `R3.2`, `J3.1` | connector side of the 1k; the leg past the series resistor |
| **PPS_NE** | `U2.8 (3Y)`, `R4.1` |  |
| **PPS_NE_J** | `R4.2`, `J4.1` | connector side of the 1k; the leg past the series resistor |
| **PPS_NW** | `U2.11 (4Y)`, `R5.1` |  |
| **PPS_NW_J** | `R5.2`, `J5.1` | connector side of the 1k; the leg past the series resistor |
| **PPS_SE** | `U3.3 (1Y)`, `R6.1` |  |
| **PPS_SE_J** | `R6.2`, `J6.1` | connector side of the 1k; the leg past the series resistor |
| **PPS_SW** | `U3.6 (2Y)`, `R7.1` |  |
| **PPS_SW_J** | `R7.2`, `J7.1` | connector side of the 1k; the leg past the series resistor |
| **LED_DRV** | `U3.8 (3Y)`, `R2.1` | U3's third output to the resistor |
| **LED_A** | `R2.2`, `D1.2 (anode)` | resistor to LED anode -- both land in K-17, so this net is one junction |
| **T1_SPARE1** | `U1.8 (3Y)` | first-tier spare output, live, for a future second-tier package |
| **T1_SPARE2** | `U1.11 (4Y)` | first-tier spare output, live, for a future second-tier package |
| **U2_SPARE** | `U2.6 (2Y)` | rear distributor spare output |

`J1.6` (header pin 22, BCM GPIO25) is spare and carries a no-connect.

#### Protoboard layout

Hole-by-hole plan for the buffer tree, generated from the same netlist as the schematic and
checked against it: every placed net connected, no hole carrying two nets, no orphaned pin.

**5x7cm protoboard, columns A-S (no J), rows numbered bottom to top 01 to 24.** Reading the
notation: a **bridge** fans one chip pin out to adjacent free holes, one per jumper end that
has to land on it, so a chain can pass through a pin. A **jumper** is a wire between two
such holes. The trailing value is the net; `GND (cap)` marks a decoupling return
specifically, which is not interchangeable with a general ground run.

**Column plan.** `A` is the GND bus and `M` the 3V3 bus, both bare solid-core wire threaded
through and tacked at each hole they cross, so the joints are parallel taps off a continuous
conductor rather than a series chain. `K` and `L` are free for test points. `C1` goes straight across `JP1.6`/`JP1.7`. `R1` cannot span SQW to VCC the same way -- they are
adjacent header pins, one row apart -- so it runs up column `N` to the 3V3 bus at its own
lead pitch. It is in `N` and not `O` because all seven of the breakout's header pins are in
column `P`, so each one needs the hole beside it in `O` to get off its own column: `O-01`
through `O-07` are spoken for.

**The Coordinator's leg is not a block on this board.** Each of its seven wires is its own
dupont jumper off the Pi's header, soldered at the far end onto the hole it belongs to:
3V3 and GND onto the two buses, the four SPI lines onto the breakout's escape holes in `O`,
and the returning PPS onto `H-20`. `PPS_COORD` comes off `U3.11`, the output on the G side,
not `U2.6` -- `U2.6` can only leave its pin through `C-10`, which is held open (below).

**Held holes.** An output is only spare if the hole it would escape through is still free, so
three are reserved and the generator fails if anything takes one: `C-10` for `U2.6`, `H-01`
for `U1.8`, `H-04` for `U1.11`. Without them the n/c outputs are unreachable without undoing
neighbouring wiring, which is not a spare.

**Decoupling.** Each cap sits against pin 14 at `H-nn`, its lower leg stepping one column
out to `I` to clear the row below, and its ground jumper running to pin 7's own tap. That is
the whole loop -- pin 14 to cap to pin 7 -- at about 29 mm. The AHC output's ~50 ohm
impedance sets the edge, not this inductance.

**Every hole is entered from the component side.** The board is plated on one side, so a
hole in the DIP channel cannot take a lead once the chip is down. Routing a cap ground
through `E-nn` would shorten the loop by about 4 mm and is not used.

```
Components:

U1: pin 1 at D-07, pin 8 at G-01
U2 (FRONT): pin 1 at D-15, pin 8 at G-09
U3 (REAR): pin 1 at D-23, pin 8 at G-17
J2 (DS3234 breakout JP1): pin 7 GND at P-01 up to pin 1 SS at P-07
    board overhangs right of column P
0.1 uF (U1): H-07 / I-05
0.1 uF (U2): H-15 / I-13
0.1 uF (U3): H-23 / I-21
R1 (2.2k): N-03 / N-06
C1 (0.1 uF): P-02 / P-01
R2 (1k): H-17 / K-17
D1 (LED): K-17 / L-17
R3 (1k): C-13 / C-08
R7 (1k): C-18 / C-16
R6 (1k): C-21 / C-24
R4 (1k): I-09 / I-11
R5 (1k): I-12 / I-14

Offboard pigtails (2-pin, signal / ground):

J3: B-08 (PPS_FC) / A-08 (GND) -- ground pin lands on the GND bus
J7: B-16 (PPS_SW) / A-16 (GND) -- ground pin lands on the GND bus
J6: B-24 (PPS_SE) / A-24 (GND) -- ground pin lands on the GND bus
J4: K-11 (PPS_NE) / L-11 (GND)
J5: K-14 (PPS_NW) / L-14 (GND)

Busses:

GND: column A, bare solid-core wire, rows 01-24
3V3: column M, bare solid-core wire, rows 01-24
K, L: free -- test points / changes

N/C:

G-01 (U1.8 3Y)
G-04 (U1.11 4Y)
D-10 (U2.6 2Y)

bridges:

D-07 (U1.1 1OE) / C-07 | GND
D-04 (U1.4 2OE) / C-04 | GND
D-01 (U1.7 GND) / C-01 / B-01 | GND
G-03 (U1.10 3OE) / H-03 | GND
G-06 (U1.13 4OE) / H-06 | GND

G-07 (U1.14 VCC) / H-07 / I-07 | 3V3

D-15 (U2.1 1OE) / C-15 | GND
D-12 (U2.4 2OE) / C-12 | GND
D-09 (U2.7 GND) / C-09 / B-09 | GND
G-11 (U2.10 3OE) / H-11 | GND
G-14 (U2.13 4OE) / H-14 | GND

G-15 (U2.14 VCC) / H-15 / I-15 | 3V3

D-23 (U3.1 1OE) / C-23 | GND
D-20 (U3.4 2OE) / C-20 | GND
D-17 (U3.7 GND) / C-17 / B-17 | GND
G-19 (U3.10 3OE) / H-19 | GND
G-22 (U3.13 4OE) / H-22 | GND

G-23 (U3.14 VCC) / H-23 / I-23 | 3V3

P-01 (J2.7 GND) / O-01 | GND

P-02 (J2.6 VCC) / O-02 | 3V3

P-03 (J2.5 SQW) / O-03 | SQW
O-03 / N-03 | SQW

N-06 (R1.2 1A) / M-06 | 3V3

G-17 (U3.8 3Y) / H-17 | LED_DRV

D-03 (U1.5 2A) / C-03 | SQW
D-06 (U1.2 1A) / C-06 / B-06 | SQW
G-02 (U1.9 3A) / H-02 / I-02 | SQW
G-05 (U1.12 4A) / H-05 | SQW

D-05 (U1.3 1Y) / C-05 | T2_FRONT
D-11 (U2.5 2A) / C-11 / B-11 | T2_FRONT
D-14 (U2.2 1A) / C-14 / B-14 | T2_FRONT
G-10 (U2.9 3A) / H-10 / I-10 | T2_FRONT
G-13 (U2.12 4A) / H-13 | T2_FRONT

D-02 (U1.6 2Y) / C-02 | T2_REAR
D-19 (U3.5 2A) / C-19 / B-19 | T2_REAR
D-22 (U3.2 1A) / C-22 / B-22 | T2_REAR
G-18 (U3.9 3A) / H-18 / I-18 | T2_REAR
G-21 (U3.12 4A) / H-21 | T2_REAR

D-13 (U2.3 1Y) / C-13 | PPS_FC

C-08 (R3.2 1A) / B-08 | PPS_FC_J

D-18 (U3.6 2Y) / C-18 | PPS_SW

C-16 (R7.2 1A) / B-16 | PPS_SW_J

D-21 (U3.3 1Y) / C-21 | PPS_SE

C-24 (R6.2 1A) / B-24 | PPS_SE_J

G-09 (U2.8 3Y) / H-09 / I-09 | PPS_NE

I-11 (R4.2 1A) / K-11 | PPS_NE_J

L-11 / L-12 | GND

G-12 (U2.11 4Y) / H-12 / I-12 | PPS_NW

I-14 (R5.2 1A) / K-14 | PPS_NW_J

L-14 / L-15 | GND

P-04 (J2.4 SCLK) / O-04 | SPI_SCLK

P-05 (J2.3 MISO) / O-05 | SPI_MISO

P-06 (J2.2 MOSI) / O-06 | SPI_MOSI

P-07 (J2.1 SS) / O-07 | SPI_CS

G-20 (U3.11 4Y) / H-20 | PPS_COORD

jumpers:

C-07 / A-07 | GND
C-04 / A-04 | GND
C-01 / A-01 | GND
H-03 / A-03 | GND
H-06 / A-06 | GND

I-07 / M-07 | 3V3

I-05 / B-01 | GND (cap)

C-15 / A-15 | GND
C-12 / A-12 | GND
C-09 / A-09 | GND
H-11 / A-11 | GND
H-14 / A-14 | GND

I-15 / M-15 | 3V3

I-13 / B-09 | GND (cap)

C-23 / A-23 | GND
C-20 / A-20 | GND
C-17 / A-17 | GND
H-19 / A-19 | GND
H-22 / A-22 | GND

I-23 / M-23 | 3V3

I-21 / B-17 | GND (cap)

O-01 / A-01 | GND

O-02 / M-02 | 3V3

L-17 / A-17 | GND

C-03 / C-06 | SQW
B-06 / H-02 | SQW
I-02 / H-05 | SQW
O-03 / C-03 | SQW

C-05 / C-11 | T2_FRONT
B-11 / C-14 | T2_FRONT
B-14 / H-10 | T2_FRONT
I-10 / H-13 | T2_FRONT

C-02 / C-19 | T2_REAR
B-19 / C-22 | T2_REAR
B-22 / H-18 | T2_REAR
I-18 / H-21 | T2_REAR

L-12 / A-12 | GND
L-15 / A-15 | GND
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
no-connect.

#### Coordinator SPI block -> RTC breakout

A single **2x4** on header pins **17-24** carries power, SPI and the returning PPS -- a different
shape from the campod's 2x5 on the same ten positions, so the two harnesses cannot cross-mate.

| header pin | BCM | signal | to |
|------------|-----|--------|-----|
| 17 | -- | 3V3 | board +3V3 bus (RTC + all buffers), at `M-10` |
| 18 | GPIO24 | **PPS in** | U3 pin 11 -- a buffered output, not SQW, at `H-20`. Overlay line is `dtoverlay=pps-gpio,gpiopin=24,assert_falling_edge=1` -- **the `assert_falling_edge` half is not in the flashed image yet** (dotfiles-symm#81); everything else in this table is as-built |
| 19 | GPIO10 | MOSI | `JP1.2`, at `O-06` |
| 20 | -- | GND | board GND bus, at `A-13` |
| 21 | GPIO9 | MISO | `JP1.3`, at `O-05` |
| 22 | GPIO25 | spare | -- |
| 23 | GPIO11 | SCLK | `JP1.4`, at `O-04` |
| 24 | GPIO8 | CE0 | `JP1.1` (SS), at `O-07` |

`dtparam=spi=on` and the `pps-gpio` overlay are in the coordinator image as of
`coordinator-pi-20261011.img`. Both are device tree, so they come from the image rather than
from a converge.

On the campod end, PPS lands on **header 13/14 (GPIO27 + GND)** as its own 2x1 rather than in the
accelerometer shell, so the two cables can be disconnected independently. BCM 9-27 come up pulled down, so an absent or unpowered distribution
board reads as "no pulses" rather than a floating line inventing edges.
