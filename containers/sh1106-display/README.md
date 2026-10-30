# sh1106-display

Coordinator front-panel status display (#115). Drives the **SH1106** 128x64 OLED on the
Pi's I2C (`0x3C`, `/dev/i2c-1`) with a compact status screen, driven by the coordinator
(a separate bus from the FC's compass I2C -- see #110).

## What it shows

An **echo of the pod bus**, and nothing else: `rekon/pod/+/status` and
`rekon/capture/intent`.

```
oak C Ac          SE C S Ac Aa
SW C S Ac Aa      NE C S Ac Aa
nw C s Ac aa                      sync still counting, arm accel missing -> not ready

idle disarm 25G               .   . blinks once a second
```

This is the **field instrument**: no network, no table, someone standing over the vehicle
deciding whether to arm. So it answers that first and explains itself second.

- **the whole screen inverts** (black on white) when every campod is publishing and
  reports itself ready, and the coordinator is hearing the FC. The one-bit answer is the
  screen's brightness and costs no line.
- **one cell per pod, two to a row** -- the name's case is that pod's own verdict (`SE`
  ready, `se` not), and the letters say which subsystem is not there yet. Cells are drawn
  one size down from the footer so two fit across:

  | | |
  |---|---|
  | `C` | camera present and warm |
  | `S` | camera sync achieved |
  | `Ac` | accelerometer on the camera mount |
  | `Aa` | accelerometer on the arm |

  Upper case is good. **A letter appears only if that pod claims the subsystem**, so a
  capture pod that never joins the sync group shows no `S` rather than a permanent
  lower-case one -- a letter that is always bad teaches you to stop reading it.
- **no cell at all** when a pod is not publishing, or its last document has aged out
  (`STALE_AFTER_S`, three missed heartbeats). The cells are the inventory as well as the
  status: a cell existing means that pod is up and on the bus. Documents are retained, so
  one that exists is not evidence of life.
- **the coordinator's own capture pod** gets a cell too, named `oak`, if it publishes a
  camera document. It is shown and does not gate -- say the word and that is one line.
- **the footer** -- `NO FC` when the coordinator has not heard an FC heartbeat in the
  last `FC_STALE_AFTER_S` (3 s), otherwise `ARMED` or `idle <reason>` from the capture
  intent; and the card's free space. Arming reaches the pods only through those
  heartbeats, so with the wire silent `idle` is the router's default rather than the FC's
  answer -- which is how two flights on 2026-10-30 recorded nothing (#669). The heartbeat
  time comes from the router's `fc_link` file, which pod-link publishes as `fc` on the
  coordinator's status.
- **the dot** -- blinks once a second. Liveness for the panel itself: everything else here
  can legitimately sit still, so without it a frozen screen looks like a calm fleet. A
  clock would not fit beside the footer at 15 characters a row.

`ready` per pod is **not computed here**. pod-link holds both the camera and accel
documents and publishes the verdict; this panel and the fleet-control table read the same
field, because two readers each assembling their own answer is the bug #577 fixed.

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
