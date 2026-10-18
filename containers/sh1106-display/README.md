# sh1106-display

Coordinator front-panel status display (#115). Drives the **SH1106** 128x64 OLED on the
Pi's I2C (`0x3C`, `/dev/i2c-1`) with a compact status screen, driven by the coordinator
(a separate bus from the FC's compass I2C -- see #110).

## What it shows

An **echo of the pod bus**, and nothing else: `rekon/pod/+/status` and
`rekon/capture/intent`.

```
COORDINATOR
ok         HH:MM:SSZ
pods SE sw
idle disarmed  24G
```

- **line 2** -- the coordinator's `state` from its own pod-link document, and the clock.
  `stale` if that document has aged out.
- **pods** -- one token per campod. `se` lower case: the pod is publishing. `SE` upper
  case: its `camera.ready` is true, which is the same field the bench cycle waits on
  before it arms. `--`: no fresh document, which covers a pod that is off and a pod whose
  last message has aged out.
- **line 4** -- `ARMED` or `idle <reason>` from the capture intent, and the coordinator's
  free space.

### Why an echo

It used to walk the coordinator's own `captures/` tree and infer "recording" from file
mtimes. That made the panel a second opinion about the fleet -- one that could disagree
with the bus every other component reads, and one that knew nothing about the campods.
Now every field on the screen has one source, and the panel is wrong only if the bus is.

### Staleness, and why age rather than content

Status documents are **retained**, so one that exists is not evidence anything is alive --
the same mistake that once declared the cameras up mid-reboot. A pod is shown present only
if a message arrived within `STALE_AFTER_S` (150 s), measured on arrival rather than from
anything in the document: `as_of_boot_s` is device uptime, there is no RTC on a campod, and
there is no wall clock in the message to compare with ours.

### The lag is the heartbeat

pod-link publishes status every `POD_LINK_STATUS_PERIOD_S`, **60 s** by default and
deliberately slow -- every wake-up on a campod is a chance to fault the camera's pages back
in. So readiness here can be up to a minute behind the device. Raising it is
`desired/status_period` on the bus (`docs/pod-bus.md`), which is a decision about a pod's
residency and not one this panel makes on its own.

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
| `SH1106_MQTT_URL` | `mqtt://127.0.0.1:1883` | the pod bus; needs `Network=host` |
| `SH1106_REFRESH_SEC` | `1.0` | redraw interval |

Runs whenever the coordinator is up (no profile). Needs `/dev/i2c-1` to exist on the host,
which takes **both** halves: `dtparam=i2c_arm=on` from the image
(`pi-image/roles/coordinator/config.append.txt`) binds the controller, and the `i2c-dev`
module -- loaded by the coordinator Ansible role -- provides the char device. The compose
`devices:` entry fails the container outright if the node is missing, so a missing module
looks like a broken display.
