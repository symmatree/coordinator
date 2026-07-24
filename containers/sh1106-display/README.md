# sh1106-display

Coordinator front-panel status display (#115). Drives the **SH1106** 128x64 OLED on the
Pi's I2C (`0x3C`, `/dev/i2c-1`) with a compact status screen, driven by the coordinator
(a separate bus from the FC's compass I2C -- see #110).

## First cut

Shows the coordinator's own **observable** state, no dependency on data it doesn't yet
export:

```
COORDINATOR
<node>     HH:MM:SSZ
cam <oak-mxid>
cap REC | idle <age>s
```

- **cam** -- OAK-D MxId, from the capture session dir (`captures/<mxid>/...`, #32).
- **cap** -- `REC` when a capture file was written in the last 5 s, else `idle`.

## Layout / provenance

Modeled on ArduPilot's onboard OLED (`NTF_DISPLAY_TYPE`). The richer FC/VIO fields
(flight mode, GPS fix + sats, battery -- via the MAVLink router) and **alternating
screens** are the follow-up in #115; they need the coordinator to expose that state
(ties to #88 and the router state model).

The render path (`draw_status`) is pure and hardware-free; `test_display.py` renders it
to a PIL image and runs at Docker build time, so a broken layout fails the build. The
luma/I2C device is only touched in `display.py:main`.

## Config (env)

| var | default | |
|-----|---------|--|
| `SH1106_I2C_PORT` | `1` | `/dev/i2c-1` |
| `SH1106_I2C_ADDR` | `0x3C` | SH1106 address |
| `SH1106_CAPTURES_DIR` | `/captures` | read-only mount of the captures dir |
| `SH1106_REFRESH_SEC` | `1.0` | redraw interval |
| `SH1106_NODE` | hostname | node label |

Runs in the `tracker`/`bench`/`flight` profiles; needs the i2c bus enabled on the host
(`dtparam=i2c_arm=on`, done by the coordinator ansible role).
