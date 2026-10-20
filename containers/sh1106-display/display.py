#!/usr/bin/env python3
"""Coordinator front-panel status display on the SH1106 OLED (coordinator #115).

AN ECHO OF THE POD BUS, AND NOTHING ELSE. It subscribes to `rekon/pod/+/status` and
`rekon/capture/intent` and draws what is there. It used to walk the coordinator's own
`captures/` tree and infer "recording" from file mtimes, which made the panel a second
opinion about the fleet's state -- one that could disagree with the bus every other
component reads, and one that knew nothing about the campods at all.

WHAT THE CAMPOD TOKENS MEAN. TWO STATES, because two is what the question has. Lower
case `se`/`sw`: that pod is up and pre-flighting -- it is publishing, and it is not ready.
Upper case `SE`/`SW`: `camera.ready`, the pod's own answer to "will arming now produce
usable frames", which is warm AND synced and is combined on the pod (#577) so that this
panel and the bench cycle's arm gate cannot mean different things by it. `--`: no fresh
document, which includes a pod that is off and a pod whose last message has aged out --
the documents are RETAINED, so one that exists is not evidence anything is alive.

THIS PANEL IS THE FIELD INSTRUMENT. With a network there is the fleet-control table,
which has room to say WHY a pod is not ready. Here there is 128 px and someone deciding
whether to arm, so the verdict is all it shows and the verdict has to be the real one.

THE LAG IS THE HEARTBEAT. pod-link publishes status every POD_LINK_STATUS_PERIOD_S,
60 by default and deliberately slow -- every wake-up on a campod is a chance to fault
the camera's pages back in. So readiness here can be up to a minute behind the device.
Raising it is `desired/status_period` on the bus (docs/pod-bus.md), which is an
operator's decision about a pod's residency rather than something this panel should do
on its own.

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


def pod_token(node, doc, age):
    """`SE` ready to arm, `se` up and pre-flighting, `--` absent or stale. Short, because
    the panel is 128 px wide and two pods have to fit beside the coordinator's state."""
    short = node.replace("campod-", "")
    if doc is None or age is None or age > STALE_AFTER_S:
        return "--"
    camera = doc.get("camera") or {}
    return short.upper() if camera.get("ready") is True else short.lower()


def status_from_bus(docs, received, now, intent=None):
    """The screen's state, from bus documents alone.

    `docs` and `received` are keyed by node: the last document and the monotonic time it
    arrived. Age comes from arrival rather than from anything in the document, because
    `as_of_boot_s` is device uptime -- there is no RTC on a campod and no wall clock in
    the message to compare against ours.
    """
    pods = []
    for node in CAMPODS:
        age = None if received.get(node) is None else now - received[node]
        pods.append(pod_token(node, docs.get(node), age))
    coord = docs.get("coordinator") or {}
    coord_age = received.get("coordinator")
    capture = None
    if intent is not None:
        capture = bool(intent.get("capture"))
    elif coord:
        capture = bool(coord.get("capture"))
    return {
        "time": dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%SZ"),
        "pods": pods,
        "capture": capture,
        "reason": (intent or {}).get("reason"),
        # The coordinator's own line comes from its pod-link document too, so every field
        # on this screen has one source.
        "coord_state": (coord.get("state") if coord else None),
        "coord_stale": coord_age is None or (now - coord_age) > STALE_AFTER_S,
        "free_gb": (round(coord["data_free_bytes"] / 1e9, 1)
                    if isinstance(coord.get("data_free_bytes"), (int, float)) else None),
    }


def draw_status(draw, width, height, status):
    """Render the status screen onto a PIL ImageDraw. Pure -- no hardware, no I/O."""
    draw.rectangle((0, 0, width - 1, height - 1), outline="white")
    draw.text((4, 1), "COORDINATOR", fill="white")
    coord = status["coord_state"] or "--"
    if status["coord_stale"]:
        coord = "stale"
    draw.text((4, 15), f"{coord[:9]:<9} {status['time']}", fill="white")
    draw.text((4, 30), f"pods {' '.join(status['pods'])}", fill="white")
    if status["capture"] is True:
        cap = "ARMED"
    elif status["capture"] is False:
        cap = f"idle {status['reason'][:9]}" if status.get("reason") else "idle"
    else:
        cap = "cap --"
    free = "" if status["free_gb"] is None else f"  {status['free_gb']:.0f}G"
    draw.text((4, 45), f"{cap[:14]}{free}", fill="white")


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
