#!/usr/bin/env python3
"""Hardware-free test for the SH1106 panel (#115). No luma, no I2C, no broker: build the
state from bus documents and draw it onto a PIL image. Runs at Docker build time, so a
broken layout or import fails the build.

    python3 test_display.py
"""
import sys

from PIL import Image, ImageDraw

from display import STALE_AFTER_S, draw_status, pod_token, status_from_bus

READY = {"node": "campod-se", "state": "ok",
         "camera": {"phase": "capture", "ready": True, "frames": 12}}
WARM = {"node": "campod-se", "state": "ok",
        "camera": {"phase": "warmup", "ready": False, "frames": 0}}
NO_CAMERA = {"node": "campod-se", "state": "ok"}
COORD = {"node": "coordinator", "state": "ok", "capture": False,
         "data_free_bytes": 24_707_977_216}

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def _render(status):
    img = Image.new("1", (128, 64), 0)
    draw_status(ImageDraw.Draw(img), 128, 64, status)
    return img


# ---- the token, which is the whole readability of the panel ---------------------------
# Upper case is `camera.ready`: the same field the bench cycle waits on before arming.
check("ready is upper case", pod_token("campod-se", READY, 1.0) == "SE")
check("publishing but not ready is lower case", pod_token("campod-se", WARM, 1.0) == "se")
check("a pod with no camera document is lower case, not ready",
      pod_token("campod-se", NO_CAMERA, 1.0) == "se")
check("absent is --", pod_token("campod-sw", None, None) == "--")
# RETAINED documents outlive the pod: a document that exists is not evidence of life, so
# age is what decides. This is the same mistake that once declared cameras up mid-reboot.
check("a stale document is -- however ready it claims to be",
      pod_token("campod-se", READY, STALE_AFTER_S + 1) == "--")
check("fresh and ready survives right up to the threshold",
      pod_token("campod-se", READY, STALE_AFTER_S - 1) == "SE")

# ---- the state the panel draws --------------------------------------------------------
st = status_from_bus(
    {"campod-se": READY, "campod-sw": WARM, "coordinator": COORD},
    {"campod-se": 100.0, "campod-sw": 100.0, "coordinator": 100.0},
    now=101.0,
    intent={"capture": True, "reason": "armed"},
)
check("both pods appear, in order, cased by readiness", st["pods"] == ["SE", "sw"], str(st["pods"]))
check("the intent topic decides armed, not the pod documents", st["capture"] is True)
check("the coordinator's own line comes from its document", st["coord_state"] == "ok")
check("free space is read from the same document", st["free_gb"] == 24.7, str(st["free_gb"]))
check("a fresh coordinator is not stale", st["coord_stale"] is False)

empty = status_from_bus({}, {}, now=0.0)
check("nothing on the bus renders as absent rather than crashing",
      empty["pods"] == ["--", "--"] and empty["capture"] is None and empty["coord_stale"] is True,
      str(empty))

# ---- the render -----------------------------------------------------------------------
for name, status in (("armed", st), ("empty bus", empty)):
    img = _render(status)
    check(f"{name} renders something", img.getbbox() is not None)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    print("RESULT: FAIL")
    sys.exit(1)
print("\nRESULT: PASS")
