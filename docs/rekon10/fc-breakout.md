# FC breakout board

[Back to index](README.md) | [Flight platform](flight-platform.md)

A board beside the FC that every FC connection passes through. Short whips of very flexible
wire run from the FC pads to the board, and the pigtails to the rest of the vehicle plug into
board-mounted sockets on it. **Planned as a PCB (2026-11-23); on the vehicle now as two
protoboard daughterboards** ([build log](flight-platform-build-log.md#fc-breakout-daughterboards-and-the-spare-fc-2026-11-23)).

## What it is for

- **A vibration firewall between the FC and its wiring.** The FC's solder joints flex far
  less in ordinary handling, so they can be left without hot glue or conformal coating, which
  keeps them easy to adjust or repair.
- **Stress lands on board connectors**, not on wire-to-solder joints at the FC.
- **Moving a peripheral is harmless to the FC.** Relocating a radio, say, means replacing a
  pigtail at a socket.

Not a HAT: it does not sit on the FC. It tacks down to the frame floor, probably just
forward of the FC.

## On the board

- **Rail buses** for the FC rails the pigtails draw on: 4V5, 5V, 9V (VTX, RELAY4-switched)
  and GND ([flight-platform.md, *Power rails*](flight-platform.md#power-rails-measured-2026-07-27-6s-pack-vtx-off-unless-noted)).
  The 9V whip carries the VTX's current.
- **Signals passed through** to sockets: the UARTs, both I2C buses, buzzer, PPS.
- **Two 3.3 V LDOs**, one from battery 5V and one from USB-side 4V5, for the SH1106 and the
  rangefinder. Either one has more capacity than any of these parts needs.

## I2C split

| bus | pads | devices | power |
|---|---|---|---|
| I2C2 (ArduPilot bus 0) | DA2 / CL2 | internal baro (DPS310, 0x76), GPS compass, SH1106 | 4V5 side: up on USB |
| I2C1 (ArduPilot bus 1) | DA1 / CL1 | PM02D, and anything else on 5V | battery side: dark on USB |

FC-side consequences: `BATT_I2C_BUS` becomes 1 for the PM02D, and the compass detects with a
new device id on I2C2, so it needs a fresh calibration ([ardupilot.md](ardupilot.md),
`ardupilot/inputs/gps-compass.param`).

## Whips

Each whip drapes off the nearest side of the FC rather than running in one bundle, so the
whips do not weigh each other down and couple the FC back to the frame.
