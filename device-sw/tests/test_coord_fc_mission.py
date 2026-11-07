#!/usr/bin/env python3
"""Hardware-free test for coord-fc-mission push.

A scripted FC plays ArduPilot's side of the mission protocol (MissionItemProtocol.cpp):
MISSION_COUNT starts an upload, it asks for each item, and a MISSION_ACK ends it; on
read-back item 0 is its own home (AP_Mission::read_cmd_from_storage), not what was sent.

    python3 device-sw/tests/test_coord_fc_mission.py
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MAVLINK20", "1")

_path = Path(__file__).resolve().parent.parent / "cli" / "coord-fc-mission"
spec = importlib.util.spec_from_loader(
    "coord_fc_mission", importlib.machinery.SourceFileLoader("coord_fc_mission", str(_path)))
fm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fm)
from pymavlink import mavutil  # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + label + (f"  {detail}" if detail else ""))
    if not cond:
        ok = False


class Msg:
    def __init__(self, kind, **kw):
        self.kind = kind
        self.mission_type = fm.MISSION
        self.__dict__.update(kw)

    def get_type(self):
        return self.kind


HOME = {"x": 405325000, "y": -800420000, "z": 350.5}


class FakeFC:
    """Holds a mission. `deaf` never answers a count; `result` is the ACK it ends an upload
    with (and, when not accepted, it keeps the old mission); `alter` maps a seq to the
    fields it stores differently; `lost` read-back seqs never arrive."""

    def __init__(self, deaf=False, result=None, alter=None, lost=()):
        self.mission = []
        self.deaf = deaf
        self.result = mavutil.mavlink.MAV_MISSION_ACCEPTED if result is None else result
        self.alter, self.lost = alter or {}, set(lost)
        self.queue, self.counts, self.items_sent = [], 0, []
        self.incoming = None
        self.target_system = self.target_component = 1
        self.mav = self

    def mission_count_send(self, ts, tc, count, mtype):
        self.counts += 1
        if self.deaf:
            return
        self.incoming = [None] * count
        # Traffic the upload must not mistake for its own: another mission type's request.
        self.queue.append(Msg("MISSION_REQUEST", seq=0, mission_type=1))
        self.queue.append(Msg("MISSION_REQUEST", seq=0))

    def mission_item_int_send(self, ts, tc, seq, frame, command, current, autocontinue,
                              p1, p2, p3, p4, x, y, z, mtype):
        self.items_sent.append(seq)
        self.incoming[seq] = {"seq": seq, "current": current, "frame": frame,
                              "command": command, "param1": p1, "param2": p2,
                              "param3": p3, "param4": p4, "x": x, "y": y,
                              "z": fm.as_float32(z), "autocontinue": autocontinue,
                              **self.alter.get(seq, {})}
        if seq + 1 < len(self.incoming):
            self.queue.append(Msg("MISSION_REQUEST", seq=seq + 1))
            return
        if self.result == mavutil.mavlink.MAV_MISSION_ACCEPTED:
            self.mission = self.incoming
        self.queue.append(Msg("MISSION_ACK", type=self.result))

    def _held(self, seq):
        it = dict(self.mission[seq])
        if seq == 0:
            it.update(HOME, current=0, frame=0, command=16)
        return it

    def mission_request_list_send(self, ts, tc, mtype):
        self.queue.append(Msg("MISSION_COUNT", count=len(self.mission)))

    def mission_request_int_send(self, ts, tc, seq, mtype):
        if seq not in self.lost:
            self.queue.append(Msg("MISSION_ITEM_INT", **self._held(seq)))

    def mission_ack_send(self, ts, tc, result, mtype):
        self.acked = result

    def recv_match(self, type=None, blocking=False, timeout=None):
        types = type if isinstance(type, list) else [type]
        while self.queue:
            msg = self.queue.pop(0)
            if msg.get_type() in types:
                return msg
        return None


def run(fc, items):
    """upload, download and compare as main() does: (rc, read-back or None, events)."""
    real, sys.stderr = sys.stderr, io.StringIO()
    try:
        n = fm.upload(fc, items)
        held = fm.download(fc)
        if held is not None:
            n += fm.compare(items, held)
        lines = [json.loads(ln) for ln in sys.stderr.getvalue().splitlines()]
    finally:
        sys.stderr = real
    rc = 3 if held is None else (1 if n else 0)
    return rc, held, lines


# The waits, without paying real time for them.
fm.QUIET_S = 0.06
fm.REPLY_S = 0.01

ARDUPILOT = Path(__file__).resolve().parents[2] / "ardupilot"
TEXT = (ARDUPILOT / "map-test-1.waypoints").read_text()

# 1. The file: every item, positions as degrees * 1e7 exactly as written.
items = fm.parse_waypoints(TEXT)
check("reads every item", len(items) == 9, str(len(items)))
check("a latitude to the 1e-7 degree written",
      items[2]["x"] == 405315904 and items[2]["y"] == -800415319, str(items[2]))
check("which a float32 would not keep", fm.as_float32(40.53159040) * 1e7 != 405315904)
check("altitude and command", items[2]["z"] == 40.0 and items[2]["command"] == 16)
for bad, why in [("", "empty"), ("QGC WPL 100\n", "another version"),
                 ("QGC WPL 110\n0\t1\t0\n", "short line"),
                 ("QGC WPL 110\n1\t0\t3\t16\t0\t0\t0\t0\t1\t2\t3\t1\n", "seq not from 0"),
                 ("QGC WPL 110\n0\t1\t0\t16\t0\t0\t0\t0\tnorth\t2\t3\t1\n", "not a number")]:
    try:
        fm.parse_waypoints(bad)
        check(f"refuses {why}", False)
    except ValueError as exc:
        check(f"refuses {why}", True, str(exc))

# 2. Written back in Mission Planner's format: the repo's files come back byte for byte,
#    so a diff of the read-back against the input shows only what the FC changed.
for name in sorted(p.name for p in ARDUPILOT.glob("*.waypoints")):
    text = (ARDUPILOT / name).read_text()
    check(f"{name} round-trips byte for byte",
          fm.write_waypoints(fm.parse_waypoints(text)) == text)

# 3. Taken: one count, each item once in the order asked, ACK accepted, read back equal.
fc = FakeFC()
rc, held, lines = run(fc, items)
check("taken and read back equal exits 0", rc == 0, str(lines))
check("one count", fc.counts == 1, str(fc.counts))
check("each item once, as asked", fc.items_sent == list(range(9)), str(fc.items_sent))
check("the upload is reported", {"event": "uploaded", "items": 9} in lines, str(lines))
check("the read-back is acknowledged", fc.acked == mavutil.mavlink.MAV_MISSION_ACCEPTED)
check("home comes back as the FC's, and is not a complaint",
      held[0]["x"] == HOME["x"] and not any("complaint" in ln for ln in lines))

# 4. Rejected: reported with the FC's result, and the read-back shows what it kept.
fc = FakeFC(result=mavutil.mavlink.MAV_MISSION_INVALID_SEQUENCE)
fc.mission = [dict(it) for it in items[:3]]
rc, held, lines = run(fc, items)
check("a rejected upload exits 1", rc == 1, f"rc={rc}")
check("with the FC's result", {"complaint": "rejected",
      "result": "MAV_MISSION_INVALID_SEQUENCE"} in lines, str(lines))
check("and the count it still holds", {"complaint": "count", "sent": 9, "fc": 3} in lines,
      str(lines))

# 5. No answer to the count: sent COUNT_SENDS times, then reported.
fc = FakeFC(deaf=True)
rc, held, lines = run(fc, items)
check("no answer exits 1", rc == 1, f"rc={rc}")
check("after the resends", fc.counts == fm.COUNT_SENDS, str(fc.counts))
check("reported as no-reply", lines[0].get("complaint") == "no-reply", str(lines))

# 6. Kept differently: the fields that differ, per item.
fc = FakeFC(alter={4: {"z": 30.0}})
rc, held, lines = run(fc, items)
mism = [ln for ln in lines if ln.get("complaint") == "mismatch"]
check("an item kept differently exits 1", rc == 1, f"rc={rc}")
check("and names the item and field",
      mism == [{"complaint": "mismatch", "seq": 4,
                "fields": {"z": {"sent": 40.0, "fc": 30.0}}}], str(mism))

# 7. A read-back item that never arrives: exit 3, nothing to write.
fc = FakeFC(lost={5})
rc, held, lines = run(fc, items)
check("an incomplete read-back exits 3", rc == 3 and held is None, f"rc={rc}")
check("and says how far it got", lines[-1].get("items_received") == 5, str(lines[-1]))

print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
