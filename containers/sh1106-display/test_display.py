#!/usr/bin/env python3
"""Hardware-free test for the SH1106 panel (#115). No luma, no I2C, no broker: build the
state from bus documents and draw it onto a PIL image. Runs at Docker build time, so a
broken layout or import fails the build.

    python3 test_display.py
"""
import sys

from PIL import Image, ImageDraw

from display import (CAMPODS, CELL_FONT, CHARS_PER_ROW, COL_X, FC_STALE_AFTER_S, FOOTER_Y,
                     ROW_Y, STALE_AFTER_S, draw_status, fc_heard, panel_text, pod_row,
                     status_from_bus, subsystem_letters)

W, H = 128, 64


# coord-rtc's clock document: chrony on the 1 Hz, the 1 Hz arriving alone, and no pulse.
RTC_ON = {"boot_id": "b", "edge": True, "pps_selected": True}
RTC_EDGE = {"boot_id": "b", "edge": True, "pps_selected": False}
RTC_NONE = {"boot_id": "b", "edge": False, "pps_selected": False}


def pod(ready=True, warm=True, settled=True, rtc=RTC_ON, accels=(True, True)):
    doc = {"node": "campod-se", "state": "ok", "ready": ready,
           "camera": {"phase": "capture", "warm": warm, "sync_ready": True,
                      "sync_settled": settled, "ready": warm and settled, "frames": 12},
           "accel": {"devices": [{"label": "camera", "present": accels[0]},
                                 {"label": "arm", "present": accels[1]}]}}
    if rtc is not None:
        doc["rtc"] = rtc
    return doc


# Hearing the FC: pod-link stamped this document at 500 s, half a second after the
# router's last heartbeat (#669).
FC_OK = {"boot_id": "b", "last_heartbeat_mono_s": 499.5, "heartbeats": 480}
COORD = {"node": "coordinator", "state": "ok", "capture": False, "as_of_boot_s": 500.0,
         "data_free_bytes": 24_707_977_216, "fc": FC_OK}
# The coordinator as it publishes in the field, with coord-rtc feed's document -- which
# also gives it a cell of its own.
COORD_ON = dict(COORD, rtc=RTC_ON)

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def _render(status):
    img = Image.new("1", (W, H), 0)
    draw_status(ImageDraw.Draw(img), W, H, status)
    return img


# ---- the letters, which are what the panel says beyond yes/no -------------------------
check("everything present reads C S Ac Aa",
      subsystem_letters(pod()) == ["C", "S", "Ac", "Aa"], str(subsystem_letters(pod())))
check("a missing arm accel lowers its letter only",
      subsystem_letters(pod(accels=(True, False))) == ["C", "S", "Ac", "aa"])

# C: the camera ready -- warm, synced and settled -- upper; warm and still aligning, lower;
# not warm, nothing.
check("a camera whose sync is still correcting lowers C only",
      subsystem_letters(pod(settled=False)) == ["c", "S", "Ac", "Aa"])
check("a camera not yet warm has no C",
      subsystem_letters(pod(warm=False)) == ["S", "Ac", "Aa"])
check("a pod with no camera document has no C",
      subsystem_letters({"accel": {"devices": [{"label": "arm", "present": True}]}}) == ["Aa"])

# S: chrony on the 1 Hz upper; the 1 Hz arriving and chrony not on it, lower; no pulse,
# nothing. nw and ne on 2026-11-07 were the lower case: pulse arriving, cameras synced to
# NTP time, 191 and 197 ms off sw.
check("the 1 Hz arriving with chrony not on it lowers S only",
      subsystem_letters(pod(rtc=RTC_EDGE)) == ["C", "s", "Ac", "Aa"])
check("chrony that could not be asked is lower case too",
      subsystem_letters(pod(rtc=dict(RTC_EDGE, pps_selected=None)))[1] == "s")
check("no pulse has no S", subsystem_letters(pod(rtc=RTC_NONE)) == ["C", "Ac", "Aa"])
check("no clock document has no S", subsystem_letters(pod(rtc=None)) == ["C", "Ac", "Aa"])

# AN ACCEL NOT CLAIMED GETS NO LETTER.
check("a pod with no accel document gets no A letters",
      subsystem_letters({"camera": {"warm": True, "ready": True}, "rtc": RTC_ON}) == ["C", "S"])

# ---- the row, and the rule that absence has no row ------------------------------------
check("a ready pod's name is upper case", pod_row("campod-se", pod(), 1.0)["name"] == "SE")
check("a pod that is not ready is lower case",
      pod_row("campod-se", pod(ready=False), 1.0)["name"] == "se")
# RETAINED documents outlive the pod, so age decides. This is the mistake that once
# declared cameras up mid-reboot.
check("a stale document gets no row at all",
      pod_row("campod-se", pod(), STALE_AFTER_S + 1) is None)
check("a pod that never published gets no row", pod_row("campod-sw", None, None) is None)
# The pod's own verdict, not one assembled here: pod-link holds both documents (#577).
check("the row trusts the pod's `ready` rather than re-deriving it",
      pod_row("campod-se", {"ready": True}, 1.0)["name"] == "SE")
check("a pod whose letters look good but says not-ready is not ready",
      pod_row("campod-se", pod(ready=False), 1.0)["ready"] is False)

# ---- the gate -------------------------------------------------------------------------
NOW = 100.0
FRESH = {**{c: NOW for c in CAMPODS}, "coordinator": NOW}
ALL = ["SE", "SW", "NE", "NW"]
every = status_from_bus({**{c: pod() for c in CAMPODS}, "coordinator": COORD},
                        FRESH, now=NOW)
check("every campod ready opens the gate", every["gate"] is True)
check("and every campod is drawn", [r["name"] for r in every["rows"]] == ALL,
      str(every["rows"]))

one_waiting = status_from_bus(
    {**{c: pod() for c in CAMPODS}, "campod-nw": pod(ready=False, settled=False),
     "coordinator": COORD},
    FRESH, now=NOW)
check("one campod not ready holds the gate", one_waiting["gate"] is False)

# ABSENCE IS THE IMPORTANT ONE: a pod that is not on the bus has no cell to be lower case
# in, so counting cells is half the gate.
missing = status_from_bus({**{c: pod() for c in CAMPODS[:-1]}, "coordinator": COORD},
                          {**{c: NOW for c in CAMPODS[:-1]}, "coordinator": NOW}, now=NOW)
check("a campod that is not on the bus holds the gate", missing["gate"] is False)
check("and it simply has no cell", [r["name"] for r in missing["rows"]] == ALL[:-1],
      str(missing["rows"]))
check("an empty bus holds the gate rather than crashing",
      status_from_bus({}, {}, now=NOW)["gate"] is False)

# The coordinator's own capture pod, when it exists, is shown and does not gate.
oak = status_from_bus(
    {**{c: pod() for c in CAMPODS},
     "coordinator": dict(COORD, ready=False, camera={"warm": False, "ready": False})},
    FRESH, now=NOW)
check("the oak cell is drawn first", [r["name"] for r in oak["rows"]] == ["oak"] + ALL,
      str([r["name"] for r in oak["rows"]]))
check("a not-ready oak pod does not hold the campods' gate", oak["gate"] is True)
check("no oak document means no oak cell", [r["name"] for r in every["rows"]] == ALL)

# ---- the FC link, without which arming reaches nothing (#669) -------------------------
check("a fresh heartbeat is heard", fc_heard(COORD, 1.0) is True)
check("a heartbeat older than the limit is not",
      fc_heard(dict(COORD, fc=dict(FC_OK, last_heartbeat_mono_s=500.0 - FC_STALE_AFTER_S - 1)),
               1.0) is False)
check("a router that has heard nothing yet is not",
      fc_heard(dict(COORD, fc=dict(FC_OK, last_heartbeat_mono_s=None)), 1.0) is False)
check("no fc document (router down, or last boot's file) is not",
      fc_heard({k: v for k, v in COORD.items() if k != "fc"}, 1.0) is False)
# The age inside the document is as of its publication, so a document that has itself
# gone stale says nothing about now.
check("a stale coordinator document is not", fc_heard(COORD, STALE_AFTER_S + 1) is False)
check("no coordinator document is not", fc_heard({}, None) is False)

silent = status_from_bus(
    {**{c: pod() for c in CAMPODS}, "coordinator": {k: v for k, v in COORD.items() if k != "fc"}},
    FRESH, now=NOW)
check("every campod ready but the FC silent holds the gate", silent["gate"] is False)
check("and the status says the FC is not heard", silent["fc"] is False)
check("every campod ready and the FC heard opens it", every["fc"] is True and every["gate"])

check("the intent topic decides armed, not the pod documents",
      status_from_bus({"coordinator": COORD}, {"coordinator": NOW}, now=NOW,
                      intent={"capture": True})["capture"] is True)
check("free space is read from the coordinator's document", every["free_gb"] == 24.7,
      str(every["free_gb"]))
check("the blink phase alternates with the second",
      status_from_bus({}, {}, now=10.0)["blink"] != status_from_bus({}, {}, now=11.0)["blink"])

# ---- the ground's line (coordinator#683) ----------------------------------------------
def campod(capturing=False, **kw):
    doc = pod(**kw)
    doc["camera"]["capturing"] = capturing
    return doc


OAK_IDLE = {"phase": "stopped", "camera": "stopped-while-waiting", "ready": False,
            "warm": False, "capturing": False}
OAK_WARM = {"phase": "capture", "ready": True, "warm": True, "capturing": False}
line = panel_text({**{c: campod() for c in CAMPODS},
                   "coordinator": dict(COORD_ON, ready=True, camera=OAK_WARM)}, FRESH, NOW)
check("everything ready reads =COSESWNENW", line == "=COSESWNENW", line)
line = panel_text({**{c: campod(capturing=True) for c in CAMPODS},
                   "coordinator": dict(COORD_ON, ready=True,
                                       camera=dict(OAK_WARM, capturing=True))},
                  FRESH, NOW)
check("everything recording reads +", line == "+COSESWNENW", line)
line = panel_text({**{c: campod() for c in CAMPODS},
                   "campod-se": campod(ready=False, settled=False),
                   "coordinator": dict(COORD_ON, ready=True, camera=OAK_WARM)}, FRESH, NOW)
check("one campod syncing: the least advanced state, mixed, and its name lower case",
      line == "<*COseSWNENW", line)
line = panel_text({**{c: campod() for c in CAMPODS},
                   "coordinator": dict(COORD_ON, camera=OAK_IDLE)}, FRESH, NOW)
check("a stopped coordinator camera reads - mixed, co lower case",
      line == "-*coSESWNENW", line)
line = panel_text({"coordinator": dict(COORD_ON, ready=True, camera=OAK_WARM),
                   "campod-sw": campod(), "campod-se": campod()},
                  {"coordinator": NOW, "campod-sw": NOW, "campod-se": NOW - 60}, NOW)
check("a pod not on the bus has no token", line == "=COSW", line)
check("an empty bus reads -", panel_text({}, {}, NOW) == "-")
check("the longest line fits a STATUSTEXT", len("<*COSESWNENW") <= 50)

# ---- the coordinator's S: the same document, from coord-rtc feed --------------------
check("the coordinator's S is upper with chrony on its 1 Hz",
      subsystem_letters(dict(COORD_ON, camera=OAK_WARM)) == ["C", "S"])
check("lower with the 1 Hz arriving and chrony not on it",
      subsystem_letters(dict(COORD, camera=OAK_WARM, rtc=RTC_EDGE)) == ["C", "s"])
check("and absent with no 1 Hz",
      subsystem_letters(dict(COORD, camera=OAK_WARM, rtc=RTC_NONE)) == ["C"])
check("whatever the chip's read says: S is the pulse and chrony, not the label",
      subsystem_letters(dict(COORD, rtc=dict(RTC_ON, state="no_response"))) == ["S"])
_coord_only = status_from_bus({"coordinator": dict(COORD, rtc=RTC_EDGE)},
                              {"coordinator": NOW}, now=NOW)
check("the rtc document alone gives the coordinator a cell",
      [(r["name"], r["letters"]) for r in _coord_only["rows"]] == [("oak", ["s"])],
      str(_coord_only["rows"]))
line = panel_text({**{c: campod() for c in CAMPODS},
                   "coordinator": dict(COORD_ON, ready=True, camera=OAK_WARM, rtc=RTC_EDGE)},
                  FRESH, NOW)
check("chrony off the coordinator's 1 Hz lowers co in the line", line == "=coSESWNENW", line)
line = panel_text({**{c: campod() for c in CAMPODS},
                   "campod-ne": campod(rtc=RTC_EDGE),
                   "coordinator": dict(COORD_ON, ready=True, camera=OAK_WARM)}, FRESH, NOW)
check("and a campod's lowers its own", line == "=COSESWneNW", line)

# ---- the layout fits, which is not something a comment can promise --------------------
# The first version of this panel was written for a 6x8 font. PIL's default is 8 px wide
# with ink from +2 to +12, so rows ran past the width and the footer was drawn through
# the bottom border. These measure the font in use rather than trusting the constants.
_probe = ImageDraw.Draw(Image.new("1", (W, H), 0))
_ink = _probe.textbbox((0, 0), "Ag")
_row_h = _ink[3]
check(f"a full row fits the width (ink to {_probe.textbbox((4, 0), 'C' * CHARS_PER_ROW)[2]})",
      _probe.textbbox((4, 0), "C" * CHARS_PER_ROW)[2] <= W - 2)
check("one more character would not",
      _probe.textbbox((4, 0), "C" * (CHARS_PER_ROW + 1))[2] > W - 2)
check(f"the footer stays on the panel (ink to {FOOTER_Y + _row_h})",
      FOOTER_Y + _row_h <= H)

# The cells: the widest a cell gets is a campod with every letter and a wide name. The
# left column must leave a gap before the right one starts, and the right must end on the
# panel. Cell text has no descenders, so its height is the cap height.
_widest = max((f"{n} C S Ac Aa" for n in ("NW", "SW", "NE", "SE")),
              key=lambda t: _probe.textlength(t, font=CELL_FONT))
_cell = _probe.textbbox((0, 0), _widest, font=CELL_FONT)
check(f"the left column's widest cell stops short of the right column "
      f"(ink to {COL_X[0] + _cell[2]}, right starts {COL_X[1]})",
      COL_X[0] + _cell[2] < COL_X[1] - 1)
check(f"the right column's widest cell ends on the panel (ink to {COL_X[1] + _cell[2]})",
      COL_X[1] + _cell[2] <= W)
check("every campod plus the oak has a cell", len(ROW_Y) * len(COL_X) >= len(CAMPODS) + 1)
for _i in range(len(ROW_Y) - 1):
    check(f"cell row {_i} does not overlap cell row {_i + 1}",
          ROW_Y[_i] + _cell[3] < ROW_Y[_i + 1] + _cell[1])
check("the last cell row does not overlap the footer",
      ROW_Y[-1] + _cell[3] < FOOTER_Y + _ink[1])

# ---- and it actually draws ------------------------------------------------------------
for _name, _status in (("all ready", every), ("one waiting", one_waiting),
                       ("FC silent", silent),
                       ("with an oak pod", oak),
                       ("empty bus", status_from_bus({}, {}, now=NOW))):
    _img = _render(_status)
    check(f"{_name} renders something", _img.getbbox() is not None)
    check(f"{_name} stays inside the panel",
          _img.getbbox()[2] <= W and _img.getbbox()[3] <= H, str(_img.getbbox()))

# NO FC is in the footer, where ARMED/idle was: with the wire silent, idle is the
# router's default rather than the FC's answer.
_nofc = _render(silent).crop((0, FOOTER_Y, 100, H))
_want = Image.new("1", (W, H), 0)
ImageDraw.Draw(_want).text((4, FOOTER_Y), "NO FC 25G", fill="white")
check("a silent FC draws NO FC in the footer",
      list(_nofc.getdata()) == list(_want.crop((0, FOOTER_Y, 100, H)).getdata()))

# The gate is meant to be read across a field, so it is the whole screen inverting rather
# than a word: the ready screen is most of the panel lit, the waiting one is text.
_lit = sum(_render(every).point(lambda v: 1 if v else 0).getdata())
_dim = sum(_render(one_waiting).point(lambda v: 1 if v else 0).getdata())
check(f"READY inverts the screen ({_lit} px lit vs {_dim})", _lit > W * H // 2 > _dim)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    print("RESULT: FAIL")
    sys.exit(1)
print("\nRESULT: PASS")
