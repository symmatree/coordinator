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
    real, sys.stdout = sys.stdout, io.StringIO()
    try:
        rc = fp.push(fc, rows)
        lines = [json.loads(ln) for ln in sys.stdout.getvalue().splitlines()]
    finally:
        sys.stdout = real
    return rc, lines


fp.REPLY_S = 0.01  # the retry loop, without paying real time for it

# 1. The tarball: *.param members in name order, comments dropped, other files ignored.
rows = fp.read_tarball(tarball({
    "./b.param": "# heading\nB_TWO,2\n\nB_THREE,3.5  # trailing\n",
    "./a.param": "A_ONE,1\n",
    "./README.md": "NOT,APARAM\n",
}))
check("reads every *.param member, in name order",
      [(k, v) for k, v, _ in rows] == [("A_ONE", 1.0), ("B_TWO", 2.0), ("B_THREE", 3.5)],
      str(rows))
check("and says where each came from", rows[2][2] == "b.param:4", rows[2][2])
try:
    fp.read_tarball(tarball({"x.param": "NO_COMMA 1\n"}))
    check("a line that is not KEY,VALUE is refused", False)
except ValueError as exc:
    check("a line that is not KEY,VALUE is refused", "x.param:1" in str(exc), str(exc))

# 2. Everything taken: no complaints, exit 0, one set per param.
fc = FakeFC({"A_ONE": 0.0, "B_TWO": 0.0, "B_THREE": 0.0})
rc, lines = run(fc, rows)
check("all taken exits 0", rc == 0, f"rc={rc}")
check("with only the summary", lines == [{"event": "done", "params": 3, "complaints": 0}],
      str(lines))
check("one PARAM_SET each", fc.sets == ["A_ONE", "B_TWO", "B_THREE"], str(fc.sets))
check("and the FC holds them", fc.params == {"A_ONE": 1.0, "B_TWO": 2.0, "B_THREE": 3.5})

# 3. One pass, every complaint reported, the rest still applied.
fc = FakeFC({"A_ONE": 0.0, "B_TWO": 0.0, "RO": 7.0, "CL": 0.0, "DEAF": 0.0},
            deaf={"DEAF"}, readonly={"RO"}, clamp={"CL": 10.0})
rows = [("A_ONE", 1.0, "a:1"), ("MISSING", 1.0, "a:2"), ("RO", 1.0, "a:3"),
        ("CL", 99.0, "a:4"), ("DEAF", 1.0, "a:5"), ("B_TWO", 2.0, "a:6")]
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
check("the summary counts them", lines[-1] == {"event": "done", "params": 6, "complaints": 4},
      str(lines[-1]))

# 4. A value that is not exact in float32 is not a mismatch: both ends carry float32.
fc = FakeFC({"F": 0.0})
rc, lines = run(fc, [("F", 0.1, "f:1")])
check("0.1 round-trips as float32 without a complaint", rc == 0, str(lines))

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
