# Coordinator HAT

[Back to index](README.md)

A board that sits on the Coordinator's 40-pin header and replaces the hub board's
[PPS distribution](central-hub.md#pps) and its pigtails. It carries the DS3234 directly
rather than the SparkFun breakout, and brings everything the Coordinator's header feeds out
to board-mounted connectors. **Design in progress (2026-11-22); not built.**

The signal reasoning that carries over unchanged -- which edge is the second, why the
timestamped edge is driven by the output buffer, the per-leg 1k, what the LED's three states
mean -- is in [central-hub.md, *PPS*](central-hub.md#pps) and is not repeated here.

## Mechanical

- Sits on the Pi 4B's 40-pin header through a **female header** on the HAT.
- Holes line up with the Pi's four M2.5 mounting holes (58 x 49 mm centres), so the HAT
  stands off on the same posts.
- **The SH1106 module mounts directly on the HAT**, on its own 4-pin header, display facing
  up. The HAT is therefore the top of the Coordinator stack, and the rest of that stack goes
  below the Coordinator: the dehoused USB hubs ([central-hub.md, *USB 2.0
  hub*](central-hub.md#usb-20-hub)).
- **The HAT carries one hub mechanically**, as a daughter board on that hub's own screw
  holes, placed clear of the SH1106. No electrical connection between the two.

## PPS consumers

| consumer | count | status |
|---|---|---|
| campods | 4 | flying hardware |
| Coordinator (GPIO24, header 18) | 1 | flying hardware |
| LED | 1 | flying hardware |
| blackfly pod (chrony) | 1 | flying hardware |
| blackfly trigger line ([flir-blackfly-s.md, *Trigger*](flir-blackfly-s.md#trigger)) | 1 | flying hardware |
| FC (`S12`, [flight-platform.md](flight-platform.md)) | 1 | flying hardware |
| four more cameras around the waist | 4 | future |
| OAK-D or another camera taking a pulse | 1 | future |
| **total** | **14** | 9 now |

## PPS distribution

```
DS3234 SQW --> 74LVC1G17 (Schmitt) --+--> 74AHC541 #1  (8 outputs)
                                     +--> 74AHC541 #2  (8 outputs)
                                     +--> 74AHC541 #3  (8 outputs)
```

24 outputs against 14 consumers, so headers can be spread around the board without every one
being used.

**Why one Schmitt gate instead of a first tier of buffers.** The two tiers on the hub board
exist only for the AHC input transition-rate limit, 100 ns/V at VCC = 3.3 V, which SQW's
open-drain RC edge cannot meet into many tied inputs. A Schmitt-trigger input has no
transition-rate limit, so SQW drives one input and its pull-up is no longer set by edge rate.
The 1G17's push-pull output drives the tied inputs of all three '541s: 24 inputs at up to
10 pF each is about 240 pF, which a push-pull output charges in nanoseconds against the
~330 ns rise the 100 ns/V limit allows.

| part | package | pitch | role |
|---|---|---|---|
| `DS3234S#` | 20-pin 300-mil SO | 1.27 mm | RTC with integrated TCXO; SPI0, CE0 |
| `SN74LVC1G17DBVR` | SOT-23-5 | 0.95 mm | Schmitt-trigger buffer on SQW |
| `SN74AHC541DW` x 3 | SOIC-20 wide | 1.27 mm | octal buffer, flow-through pinout (inputs one side, outputs the other); inputs tied, OEs grounded |

Per package: 0.1 uF from VCC to GND at the pins. The DS3234 also needs its backup-cell
holder on the board, since that comes off the breakout. The per-leg 1k stays on every output
leg except the Coordinator's.

## Pi header, carried through

Taken from the current wiring ([central-hub.md](central-hub.md#coordinator-spi-block---rtc-breakout)):

| header pins | function | on the HAT |
|---|---|---|
| 17-24 | 3V3, PPS in (GPIO24, 18), SPI0 MOSI/MISO/SCLK, CE0 (24), GND | DS3234 on SPI0 CE0; a buffer output to header 18 |
| 1, 3, 5, 7, 9 | 3V3, SDA1, SCL1, GPIO4, GND | SH1106 front-panel display, mounted on the HAT |
| 6, 8, 10 | GND, TXD (GPIO14), RXD (GPIO15) | FC serial connector |
| 26 | SPI0 CE1 | accelerometer connector (just in case one is ever fed to the Coordinator) |

## Connectors

- Board-mounted connectors throughout, in place of pigtails.
- PPS legs: a JST with a retention tab on the board. The no-retention JST stays inline in
  the cable as a breakaway.
- The campod accelerometer pigtail is an 8-pin JST GH ([campod-electrical.md](../campod-electrical.md)).

## Open

- Where the PPS output headers go. That depends on the payload layout.
