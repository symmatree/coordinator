#!/usr/bin/env python3
"""Coordinator front-panel status display on the SH1106 OLED (coordinator #115).

AN ECHO OF THE POD BUS, AND NOTHING ELSE. It subscribes to `rekon/pod/+/status` and
`rekon/capture/intent` and draws what is there. It used to walk the coordinator's own
`captures/` tree and infer "recording" from file mtimes, which made the panel a second
opinion about the fleet's state -- one that could disagree with the bus every other
component reads, and one that knew nothing about the campods at all.

WHAT IS ON IT, AND WHY THAT. This is the field instrument: no network, no table, someone
standing over the vehicle deciding whether to arm. So it answers that first, as an
inverted READY bar, and then explains itself -- one row per pod, `SE  C S Ac Aa`, where
the name's case is that pod's own verdict and each letter is one subsystem, upper case
when it is there. The operator decides what is an acceptable defect and what is a reason
to wait, which needs the parts shown separately, not one bit.

  C   camera present and warm          S   camera sync achieved
  Ac  accelerometer on the camera      Aa  accelerometer on the arm

A POD THAT IS NOT PUBLISHING HAS NO ROW. The rows are the inventory as well as the
status: a row existing means that pod is up and on the bus, which is the first thing
worth knowing and the thing a `--` token muddled. The documents are RETAINED, so one
that exists is not evidence of life -- age decides.

NOT RE-DERIVED HERE. `ready` per pod comes from pod-link, which holds both the camera and
accel documents; the letters explain that verdict rather than computing a second one. The
panel and the fleet-control table disagreeing about "ready" is the bug #577 fixed.

THE LAG IS THE HEARTBEAT. pod-link publishes status every POD_LINK_STATUS_PERIOD_S,
which the stacks set to 5 s (its own default is 60). Every wake-up on a campod is a
chance to fault the camera's pages back in, so the cadence is a decision about a pod's
residency rather than something this panel should take for itself -- `desired/
status_period` on the bus (docs/pod-bus.md) is where it is changed. STALE_AFTER_S below
is paired with it: three missed publications is a pod that is gone.

The render path (`draw_status`) and the state it renders (`status_from_bus`) are pure
and hardware-free, unit-tested in `test_display.py` (which the Docker build runs), so a
broken layout fails the build. The luma/I2C device and the MQTT client are only touched
in `main`.
"""

import datetime as dt
import json
import os
import signal
import time

_stop = False


def _request_stop(signum, _frame):
    global _stop
    _stop = True


def _env(name, default):
    v = os.getenv(name)
    return v if v not in (None, "") else default


# A pod whose last status is older than this is shown as absent. Three heartbeats at the
# 5 s cadence the campods publish at: one missed publication is a slow tick or a
# reconnect, three in a row is a pod that is gone. This number is paired with
# POD_LINK_STATUS_PERIOD_S in stacks/campod/pod-link.container -- raise that and a pod
# will start showing as absent between its own publications.
STALE_AFTER_S = 15.0

# The pod's own arm answer, read and not re-derived. This panel deliberately does not
# combine `warm` and `sync_ready` itself: the campod does that (#577), so the panel and
# the bench cycle's arm gate cannot drift into meaning different things by "ready".
CAMPODS = ("campod-se", "campod-sw")


def subsystem_letters(doc):
    """The per-subsystem letters for one pod, in a fixed order.

    `C` camera present and warm, `S` camera sync achieved, `Ac` and `Aa` the accelerometer
    on the camera mount and the one on the arm. UPPER CASE is that subsystem good, lower
    case is not -- so the shape of the row is constant and only its case moves, which is
    what makes it readable at arm's length without reading it word by word.

    A LETTER IS ONLY PRESENT IF ITS SUBSYSTEM IS CLAIMED. A pod publishing no accel
    document gets no A letters rather than lower-case ones: absent hardware that nobody
    expects is not a fault, and a row of failures for a pod that is fine is how a panel
    stops being read. What makes a MISSING accel visible is the reader listing the device
    and saying `present: false`, which is what it does (it reports DEVID mismatches).

    These letters explain the verdict; they are not the verdict. `ready` comes from the
    pod (pod-link's podReady) so that this panel and the fleet-control table cannot come
    to mean different things by it.
    """
    letters = []
    camera = doc.get("camera") or {}
    if camera:
        warm = camera.get("warm")
        # A pod on an image from before #577 has no `warm`; its `ready` meant warm, which
        # is what C is. Read that rather than showing a camera-shaped hole.
        if warm is None:
            warm = camera.get("ready")
        letters.append("C" if warm is True else "c")
        # Only if this pod is IN the sync group. A capture pod that never syncs -- the
        # coordinator's own, for one -- would otherwise carry a permanent lower-case `s`
        # and teach the reader to ignore a letter, which is worse than not showing it.
        if "sync_ready" in camera:
            letters.append("S" if camera.get("sync_ready") is True else "s")
    for device in (doc.get("accel") or {}).get("devices") or []:
        label = str(device.get("label") or "?")
        tag = f"A{label[:1].lower()}"
        letters.append(tag if device.get("present") is True else tag.lower())
    return letters


def pod_row(node, doc, age):
    """One pod's row, or None when that pod is not on the bus.

    NO ROW AT ALL rather than a placeholder: a row existing is itself the signal that the
    pod is up and talking, which is the first thing worth knowing and the one a token like
    `--` muddles. The documents are RETAINED, so one that exists is not evidence of life
    and age is what decides.

    The NAME carries the pod's own verdict in its case -- `SE` ready, `se` not -- so one
    glance down the left edge answers "can I arm", and the letters answer "why not".
    """
    if doc is None or age is None or age > STALE_AFTER_S:
        return None
    short = node.replace("campod-", "")
    ready = doc.get("ready") is True
    return {
        "name": short.upper() if ready else short.lower(),
        "ready": ready,
        "letters": subsystem_letters(doc),
    }


def status_from_bus(docs, received, now, intent=None):
    """The screen's state, from bus documents alone.

    `docs` and `received` are keyed by node: the last document and the monotonic time it
    arrived. Age comes from arrival rather than from anything in the document, because
    `as_of_boot_s` is device uptime -- there is no RTC on a campod and no wall clock in
    the message to compare against ours.
    """
    coord_age = received.get("coordinator")
    coord = docs.get("coordinator") or {}

    # The coordinator's own capture pod, when there is one. It publishes a camera document
    # like any other pod, so it needs no special reading; until something does, there is
    # no row and nothing to explain.
    oak_rows = []
    if coord_age is not None and (coord.get("camera") or coord.get("accel")):
        row = pod_row("oak", coord, now - coord_age)
        if row is not None:
            oak_rows.append(row)

    campod_rows = []
    for node in CAMPODS:
        age = None if received.get(node) is None else now - received[node]
        row = pod_row(node, docs.get(node), age)
        if row is not None:
            campod_rows.append(row)
    rows = oak_rows + campod_rows

    # THE GATE: every campod on the bus AND saying it is ready. Counting the rows is half
    # of it -- a pod that is absent is the most important way of not being ready, and it
    # has no row to be lower case in. The oak pod is shown and does not gate; say so and
    # it becomes a one-line change.
    gate = len(campod_rows) == len(CAMPODS) and all(r["ready"] for r in campod_rows)

    capture = None
    if intent is not None:
        capture = bool(intent.get("capture"))
    elif coord:
        capture = bool(coord.get("capture"))
    return {
        # The blink phase, not a clock: liveness for THIS panel, since a frozen screen and
        # a quiet fleet look identical otherwise and every other field can sit still. Kept
        # in the state so the render stays pure and the test can drive both phases.
        "blink": int(now) % 2 == 0,
        "rows": rows,
        "gate": gate,
        "capture": capture,
        "reason": (intent or {}).get("reason"),
        "free_gb": (round(coord["data_free_bytes"] / 1e9, 1)
                    if isinstance(coord.get("data_free_bytes"), (int, float)) else None),
    }


# MEASURED, NOT ASSUMED. PIL's default font draws 8 px wide glyphs whose ink runs from
# 2 px to 12 px below the given y -- so a 128 px panel holds 15 characters at x=4, and a
# row costs 11 px of height. The first layout here was written for 6x8 and clipped its
# own footer. `test_display.py` asserts the fit rather than trusting this comment.
CHARS_PER_ROW = 15
BANNER_H = 15
ROW_Y = (17, 28, 39)
FOOTER_Y = 50


def draw_status(draw, width, height, status):
    """Render the status screen onto a PIL ImageDraw. Pure -- no hardware, no I/O.

    FOUR THINGS, in the order someone standing over the vehicle asks them:

      1. the banner   -- can I arm. INVERTED (black on white) when every campod is on the
                         bus and ready, so the one-bit answer differs by half the screen's
                         brightness rather than by one word. WAIT otherwise
      2. a row per pod -- `SE  C S Ac Aa`. The name's case repeats that pod's own verdict,
                         the letters say which subsystem is not there yet. A pod that is
                         not publishing HAS NO ROW, so the rows are the inventory too
      3. the footer   -- ARMED or idle, and the card space: the other thing checked before
                         a flight
      4. the blink    -- a dot in the corner, on for one second and off the next. Liveness
                         for THIS PANEL: every other thing here can legitimately sit still,
                         so without it a frozen screen looks like a calm fleet. There is no
                         room for a clock beside the footer at 15 characters a row, and the
                         dot answers the only question the clock was answering
    """
    draw.rectangle((0, 0, width - 1, height - 1), outline="white")

    if status["gate"]:
        draw.rectangle((1, 1, width - 2, BANNER_H), fill="white")
        draw.text((width // 2 - 20, 2), "READY", fill="black")
    else:
        draw.text((width // 2 - 16, 2), "WAIT", fill="white")

    for y, row in zip(ROW_Y, status["rows"]):
        line = f"{row['name']:<4}{' '.join(row['letters'])}"
        draw.text((4, y), line[:CHARS_PER_ROW], fill="white")

    if status["capture"] is True:
        cap = "ARMED"
    elif status["capture"] is False:
        cap = f"idle {status['reason']}" if status.get("reason") else "idle"
    else:
        cap = "cap --"
    free = "" if status["free_gb"] is None else f" {status['free_gb']:.0f}G"
    draw.text((4, FOOTER_Y), f"{cap[:CHARS_PER_ROW - len(free)]}{free}", fill="white")

    if status["blink"]:
        draw.rectangle((width - 6, height - 6, width - 3, height - 3), fill="white")


def main():
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    port = int(_env("SH1106_I2C_PORT", "1"))
    addr = int(_env("SH1106_I2C_ADDR", "0x3C"), 0)
    refresh = float(_env("SH1106_REFRESH_SEC", "1.0"))
    broker = _env("SH1106_MQTT_URL", "mqtt://127.0.0.1:1883")

    # Imported here so the render path stays testable without luma, I2C or a broker.
    import paho.mqtt.client as mqtt
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import sh1106

    docs, received, intent = {}, {}, {}

    def on_message(_client, _userdata, msg):
        try:
            doc = json.loads(msg.payload)
        except ValueError:
            return  # a message we cannot read is not state; leave the last one up
        if msg.topic == "rekon/capture/intent":
            intent.clear()
            intent.update(doc)
            return
        node = doc.get("node") or msg.topic.split("/")[2]
        docs[node] = doc
        received[node] = time.monotonic()

    host = broker.split("//")[-1]
    hostname, _, portstr = host.partition(":")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sh1106-display")
    client.on_message = on_message
    # SUBSCRIBE ON EVERY CONNECT, not once: the broker runs on this same box, so it comes
    # and goes with a converge, and a panel that subscribed only at startup would freeze
    # on whatever it last saw and look like a healthy fleet.
    client.on_connect = lambda c, *_: (c.subscribe("rekon/pod/+/status", qos=0),
                                       c.subscribe("rekon/capture/intent", qos=0))
    client.connect_async(hostname, int(portstr or "1883"), keepalive=30)
    client.loop_start()

    device = sh1106(i2c(port=port, address=addr))
    device.persist = True  # leave the last frame up on SIGTERM instead of blanking
    print(f"sh1106-display: i2c={port}@{hex(addr)} bus={broker} refresh={refresh}s",
          flush=True)

    while not _stop:
        status = status_from_bus(docs, received, time.monotonic(),
                                 intent if intent else None)
        with canvas(device) as draw:
            draw_status(draw, device.width, device.height, status)
        end = time.monotonic() + refresh
        while not _stop and time.monotonic() < end:
            time.sleep(min(0.2, max(0.0, end - time.monotonic())))

    client.loop_stop()
    client.disconnect()
    print("sh1106-display: stopped", flush=True)


if __name__ == "__main__":
    main()
