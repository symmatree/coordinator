#!/usr/bin/env python3
"""Hardware-free test for coord-fc-params push.

A scripted FC answers each PARAM_SET the way ArduPilot does (GCS_Param.cpp
handle_param_set): PARAM_VALUE with the value it now holds, or PARAM_ERROR for a name it
does not have. The push is one pass, and every answer other than "took it" is reported.

    python3 device-sw/tests/test_coord_fc_params.py
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tarfile
from pathlib import Path

os.environ.setdefault("MAVLINK20", "1")

_path = Path(__file__).resolve().parent.parent / "cli" / "coord-fc-params"
spec = importlib.util.spec_from_loader(
    "coord_fc_params", importlib.machinery.SourceFileLoader("coord_fc_params", str(_path)))
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)
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
        self.__dict__.update(kw)

    def get_type(self):
        return self.kind


class FakeFC:
    """Holds params; answers PARAM_SET as ArduPilot does. `deaf` names get no answer,
    `readonly` names are refused and answered with the old value, `clamp` maps a name to
    the value it ends up holding."""

    def __init__(self, params, deaf=(), readonly=(), clamp=None):
        self.params = dict(params)
        self.deaf, self.readonly, self.clamp = set(deaf), set(readonly), clamp or {}
        self.queue, self.sets = [], []
        self.target_system = self.target_component = 1
        self.mav = self

    def param_set_send(self, ts, tc, pid, value, ptype):
        name = pid.decode()
        self.sets.append(name)
        # Traffic the push must not mistake for its answer.
        self.queue.append(Msg("PARAM_VALUE", param_id="OTHER_PARAM", param_value=0.0))
        if name in self.deaf:
            return
        if name not in self.params:
            self.queue.append(Msg("PARAM_ERROR", param_id=name,
                                  error=mavutil.mavlink.MAV_PARAM_ERROR_DOES_NOT_EXIST))
            return
        if name in self.readonly:
            self.queue.append(Msg("PARAM_ERROR", param_id=name,
                                  error=mavutil.mavlink.MAV_PARAM_ERROR_PERMISSION_DENIED))
            self.queue.append(Msg("PARAM_VALUE", param_id=name,
                                  param_value=self.params[name]))
            return
        self.params[name] = self.clamp.get(name, fp.as_float32(value))
        self.queue.append(Msg("PARAM_VALUE", param_id=name, param_value=self.params[name]))

    # The read-back. `withhold` indices are left out of the first listing only; `lost`
    # ones never arrive at all.
    withhold, lost = set(), set()

    def _value(self, idx):
        name = sorted(self.params)[idx]
        return Msg("PARAM_VALUE", param_id=name, param_value=self.params[name],
                   param_index=idx, param_count=len(self.params))

    def param_request_list_send(self, ts, tc):
        self.lists = getattr(self, "lists", 0) + 1
        for idx in range(len(self.params)):
            if idx not in self.lost and not (self.lists == 1 and idx in self.withhold):
                self.queue.append(self._value(idx))

    def param_request_read_send(self, ts, tc, pid, idx):
        self.rereads = getattr(self, "rereads", 0) + 1
        if idx not in self.lost:
            self.queue.append(self._value(idx))

    def recv_match(self, type=None, blocking=False, timeout=None):
        return self.queue.pop(0) if self.queue else None


def tarball(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def run(fc, rows):
    """push(), returning (1 if it complained else 0, its stderr events)."""
    real, sys.stderr = sys.stderr, io.StringIO()
    try:
        n = fp.push(fc, rows)
        lines = [json.loads(ln) for ln in sys.stderr.getvalue().splitlines()]
    finally:
        sys.stderr = real
    return (1 if n else 0), lines


# The retry and read-back loops, without paying real time for them.
fp.REPLY_S = 0.01
fp.QUIET_S = 0.02

# 1. The tarball: *.param members in name order, comments dropped, other files ignored.
rows = fp.read_tarball(tarball({
    "./b.param": "# heading\nB_TWO,2\n\nB_THREE,3.5  # trailing\n",
    "./a.param": "A_ONE,1\n",
    "./README.md": "NOT,APARAM\n",
}))
check("reads every *.param member, in name order",
      [(k, v) for k, v, *_ in rows] == [("A_ONE", 1.0), ("B_TWO", 2.0), ("B_THREE", 3.5)],
      str(rows))
check("and says where each came from", rows[2][2] == "b.param:4", rows[2][2])
check("and to how many places", [r[3] for r in rows] == [0, 0, 1], str(rows))
try:
    fp.read_tarball(tarball({"x.param": "NO_COMMA 1\n"}))
    check("a line that is not KEY,VALUE is refused", False)
except ValueError as exc:
    check("a line that is not KEY,VALUE is refused", "x.param:1" in str(exc), str(exc))

# 2. Everything taken: no complaints, exit 0, one set per param.
fc = FakeFC({"A_ONE": 0.0, "B_TWO": 0.0, "B_THREE": 0.0})
rc, lines = run(fc, rows)
check("all taken exits 0", rc == 0, f"rc={rc}")
check("with only the summary", lines == [{"event": "pushed", "params": 3, "complaints": 0}],
      str(lines))
check("one PARAM_SET each", fc.sets == ["A_ONE", "B_TWO", "B_THREE"], str(fc.sets))
check("and the FC holds them", fc.params == {"A_ONE": 1.0, "B_TWO": 2.0, "B_THREE": 3.5})

# 3. One pass, every complaint reported, the rest still applied.
fc = FakeFC({"A_ONE": 0.0, "B_TWO": 0.0, "RO": 7.0, "CL": 0.0, "DEAF": 0.0},
            deaf={"DEAF"}, readonly={"RO"}, clamp={"CL": 10.0})
rows = [("A_ONE", 1.0, "a:1", 0), ("MISSING", 1.0, "a:2", 0), ("RO", 1.0, "a:3", 0),
        ("CL", 99.0, "a:4", 0), ("DEAF", 1.0, "a:5", 0), ("B_TWO", 2.0, "a:6", 0)]
rc, lines = run(fc, rows)
by = {ln.get("param"): ln for ln in lines if "param" in ln}
check("any complaint exits 1", rc == 1, f"rc={rc}")
check("a name the FC lacks is reported", by.get("MISSING", {}).get("error")
      == "MAV_PARAM_ERROR_DOES_NOT_EXIST", str(by.get("MISSING")))
check("a refused write is reported", by.get("RO", {}).get("error")
      == "MAV_PARAM_ERROR_PERMISSION_DENIED", str(by.get("RO")))
check("a value the FC did not keep is reported", by.get("CL", {}).get("complaint")
      == "mismatch" and by["CL"].get("fc") == 10.0, str(by.get("CL")))
check("no answer is reported", by.get("DEAF", {}).get("complaint") == "no-reply",
      str(by.get("DEAF")))
check("after the resends", fc.sets.count("DEAF") == 1 + fp.RESENDS, str(fc.sets))
check("each complaint says where the value came from", by["CL"].get("at") == "a:4")
check("one pass: the rest were still applied", fc.params["A_ONE"] == 1.0
      and fc.params["B_TWO"] == 2.0)
check("and nothing was sent twice that was answered",
      [s for s in fc.sets if s != "DEAF"] == ["A_ONE", "MISSING", "RO", "CL", "B_TWO"],
      str(fc.sets))
check("the summary counts them",
      lines[-1] == {"event": "pushed", "params": 6, "complaints": 4}, str(lines[-1]))

# 4. A value that is not exact in float32 is not a mismatch: both ends carry float32.
fc = FakeFC({"F": 0.0})
rc, lines = run(fc, [("F", 0.1, "f:1", 1)])
check("0.1 round-trips as float32 without a complaint", rc == 0, str(lines))
# What the FC answered on 2026-11-04 for COMPASS_OFS_Y, sent as 40.36612: equal to 5 places.
fc = FakeFC({"F": 0.0}, clamp={"F": 40.36611557006836})
rc, lines = run(fc, fp.parse_param_text("F,40.36612\n", "f"))
check("an answer a few float32 steps off agrees to the places written", rc == 0, str(lines))
fc = FakeFC({"F": 0.0}, clamp={"F": 40.36621})
rc, lines = run(fc, fp.parse_param_text("F,40.36612\n", "f"))
check("one off in the last place written is a mismatch", rc == 1, str(lines))
check("6.181582E-05 is written to 11 places", fp.decimal_places("6.181582E-05") == 11)

# 5. The export. Read back the last real export as the FC would serve it, and write it out:
#    it must come back byte for byte, because a PR made from this output should show only
#    what changed. That pins Mission Planner's order (`_` before digits and letters) and
#    its number formatting (integers bare, the shortest float32 decimal, 6.181582E-05).
_export = (Path(__file__).resolve().parents[2] / "ardupilot" / "rekon10-methodi.param").read_text()
_params = {k: fp.as_float32(float(v)) for k, _, v in
           (ln.partition(",") for ln in _export.splitlines())}
fc = FakeFC(_params)
got = fp.read_all(fc)
check("the read-back gets every parameter", got is not None and len(got) == len(_params),
      f"{None if got is None else len(got)} of {len(_params)}")
check("and the export is the last export, byte for byte", fp.export(got) == _export)

# Indices missing from the listing are asked for by index, and then it completes.
fc = FakeFC(_params)
fc.withhold = set(range(0, len(_params), 7))
got = fp.read_all(fc)
check("gaps in the listing are re-read and filled", got is not None
      and len(got) == len(_params) and fc.rereads >= len(fc.withhold),
      f"rereads={getattr(fc, 'rereads', 0)} withheld={len(fc.withhold)}")

# The broadcast PARAM_VALUE after a set carries no listing index; it must not count.
fc = FakeFC({"A": 1.0, "B": 2.0})
fc.queue.append(Msg("PARAM_VALUE", param_id="A", param_value=9.0, param_index=65535,
                    param_count=2))
got = fp.read_all(fc)
check("a value outside the listing is not part of it", got == {"A": 1.0, "B": 2.0}, str(got))

# A parameter that never arrives: no export at all, since a partial one reads as removals.
fc = FakeFC({"A": 1.0, "B": 2.0, "C": 3.0})
fc.lost = {1}
_err, sys.stderr = sys.stderr, io.StringIO()
try:
    got = fp.read_all(fc)
finally:
    sys.stderr = _err
check("an incomplete read-back returns nothing", got is None, str(got))

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
