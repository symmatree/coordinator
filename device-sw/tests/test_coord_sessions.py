#!/usr/bin/env python3
"""Checks for device-sw/cli/coord-sessions. Run by test.sh, or by hand:

    python3 device-sw/tests/test_coord_sessions.py

It builds a real session tree in a temp dir and packages it for real --
the point is that the bundle round-trips, not that the code was called.
"""

import hashlib
import importlib.machinery
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "cli"
spec = importlib.util.spec_from_loader(
    "coord_sessions",
    importlib.machinery.SourceFileLoader("coord_sessions", str(CLI / "coord-sessions")),
)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


def make_session(captures: Path, node: str, session: str, frames: int) -> Path:
    d = captures / node / session
    (d / "manifests").mkdir(parents=True)
    for i in range(frames):
        stem = f"{node}_{i:08d}_20260919T02{i:04d}0_225838Z"
        (d / f"{stem}.jpg").write_bytes(b"\xff\xd8\xff" + bytes(200))
        (d / f"{stem}.json").write_text(json.dumps({
            "seq": i, "wall_clock_utc": f"2026-09-19T02:{i:02d}:08.225838Z",
        }))
    (d / "accel-camera.jsonl").write_text('{"t":"b"}\n' * 50)
    (d / "manifests" / "campod-camera").write_text('FLEET_UNIT="campod-camera"\n')
    (d / "manifests" / "fleet-image").write_text('FLEET_ROLE="campod"\n')
    return d


root = Path(tempfile.mkdtemp(prefix="coord-sessions-test-"))
try:
    captures = root / "captures"
    closed = make_session(captures, "campod-se", "11111111-aaaa", frames=4)
    make_session(captures, "campod-se", "22222222-bbbb", frames=2)
    out_dir = root / "bundles"

    # The open session is whichever equals the current boot id, so pin it.
    cs.boot_id = lambda: "22222222-bbbb"
    # This host is not called campod-se; sessions are anchored on the node name.
    cs.node_name = lambda: "campod-se"

    # 1. list
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cs.cmd_list(captures)
    doc = json.loads(buf.getvalue())
    check("list exits 0", rc == 0)
    check("finds both sessions", len(doc["sessions"]) == 2, str(len(doc["sessions"])))
    by_id = {s["session"]: s for s in doc["sessions"]}
    a, b = by_id["11111111-aaaa"], by_id["22222222-bbbb"]
    check("counts frames, not files", a["frames"] == 4 and a["files"] == 11,
          f"frames={a['frames']} files={a['files']}")
    check("the current boot's session is open", b["open"] is True)
    check("a previous boot's session is not", a["open"] is False)
    check("span comes from the sidecars", a["first_utc"] == "2026-09-19T02:00:08.225838Z"
          and a["last_utc"] == "2026-09-19T02:03:08.225838Z",
          f"{a['first_utc']} .. {a['last_utc']}")
    check("records both manifests", a["manifests"] == ["campod-camera", "fleet-image"])
    check("names the accel files", a["accel"] == ["accel-camera.jsonl"])
    check("bytes is the real tree size",
          a["bytes"] == sum(p.stat().st_size for p in closed.rglob("*") if p.is_file()))

    # 1b. A directory under captures/ that is not this node is not a node. The
    #     coordinator's captures/ holds an OAK-D tree named for the camera's MxId, and
    #     walking it reported that serial as a node (`no such node: 18443010B1D8BC0800`).
    make_session(captures, "18443010B1D8BC0800", "99999999-zzzz", frames=1)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cs.cmd_list(captures)
    doc2 = json.loads(buf.getvalue())
    nodes = {s["node"] for s in doc2["sessions"]}
    check("a foreign subtree is not reported as a node", nodes == {"campod-se"}, str(nodes))
    check("and delete cannot reach into it",
          cs.resolve(captures, "99999999-zzzz") is None)

    # 2. the current boot's session packages like any other. It used to be refused,
    #    which forced a reboot to retrieve a session that had merely been stopped --
    #    and the reboot opened a fresh session that was then equally unretrievable.
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cs.cmd_package(captures, "22222222-bbbb", out_dir)
    check("packages the current boot rather than refusing it", rc == 0)
    open_summary = json.loads(buf.getvalue())
    check("and writes its bundle", Path(open_summary["bundle"]).is_file())
    check("whose manifest hashes every file in it",
          open_summary["files"] > 0 and len(open_summary["sha256"]) == 64)

    # 2a-bis. CHOOSING between sessions. The two failures this reproduces, both of which
    #         picked the wrong session off a live pod: a frameless session reports null
    #         first/last_utc, and a session whose frames predate the clock lock reports
    #         the fake epoch. last_activity_utc has to beat both.
    import os as _os
    _frameless = captures / "campod-se" / "aaaaaaaa-f0f0"
    (_frameless / "manifests").mkdir(parents=True)
    (_frameless / "accel-arm.jsonl").write_text('{"t":"b"}\n' * 10)
    _stale = make_session(captures, "campod-se", "bbbbbbbb-f1f1", 2)
    # The fake pre-lock epoch the image boots with, which is what made "newest last_utc"
    # choose a 23-second boot over a four-minute capture window.
    _fake = 1776109116.0           # 2026-04-13, the image's build clock
    _real = 1792070708.0           # 2026-10-15, after chrony locked
    for _f in _stale.rglob("*"):
        if _f.is_file():
            _os.utime(_f, (_fake, _fake))
    for _f in _frameless.rglob("*"):
        if _f.is_file():
            _os.utime(_f, (_real, _real))

    _fl = cs.describe(_frameless, "campod-se", "nope")
    _st = cs.describe(_stale, "campod-se", "nope")
    check("a frameless session still reports a last_activity_utc",
          _fl["last_activity_utc"] is not None and _fl["first_utc"] is None,
          f'activity={_fl["last_activity_utc"]} first={_fl["first_utc"]}')
    check("the pre-lock session's activity stamp is its fake mtime, not invented",
          _st["last_activity_utc"].startswith("2026-04-13"), _st["last_activity_utc"])
    check("sorting on last_activity_utc prefers the real session over the stale one",
          max((_fl, _st), key=lambda s: s["last_activity_utc"])["session"] == "aaaaaaaa-f0f0")
    # And it must see collectd, so a boot that captured nothing at all is still datable --
    # that is the case a capture window with a broken camera produces.
    _cd = root / "collectd-early" / "aaaaaaaa-f0f0" / "campod-se"
    _cd.mkdir(parents=True)
    _later = _real + 600
    (_cd / "cpu").write_text("t,v\n")
    _os.utime(_cd / "cpu", (_later, _later))
    _saved_root, cs.COLLECTD_ROOT = cs.COLLECTD_ROOT, root / "collectd-early"
    check("collectd's mtime carries the stamp when captures are older",
          cs.describe(_frameless, "campod-se", "nope")["last_activity_utc"]
          > _fl["last_activity_utc"])
    cs.COLLECTD_ROOT = _saved_root

    # 2b. the bundle carries this boot's context, selected by boot id rather than by
    #     time. collectd's tree is keyed by boot id (roles/metrics) so it is a
    #     directory to name; the journal is indexed by boot id so the session id is
    #     the selector.
    cd_root = root / "collectd"
    (cd_root / "22222222-bbbb" / "campod-se" / "cpu").mkdir(parents=True)
    (cd_root / "22222222-bbbb" / "campod-se" / "cpu" / "x-2026-09-26").write_text("t,v\n1,2\n")
    (cd_root / "99999999-other" / "campod-se").mkdir(parents=True)
    (cd_root / "99999999-other" / "campod-se" / "leak").write_text("must not appear\n")
    cs.COLLECTD_ROOT = cd_root
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cs.cmd_package(captures, "22222222-bbbb", out_dir)
    check("packages with collectd present", rc == 0)
    bundle_p = Path(json.loads(buf.getvalue())["bundle"])
    raw = subprocess.run(["zstd", "-dc", str(bundle_p)],
                         capture_output=True, check=True).stdout
    names = tarfile.open(fileobj=io.BytesIO(raw)).getnames()
    check("this boot's collectd rides along",
          any(n.endswith("collectd/campod-se/cpu/x-2026-09-26") for n in names), str(names))
    check("another boot's collectd does not", not any("leak" in n for n in names))

    # 2c. journal_for strips the dashes. `journalctl -b` takes 32-char undashed hex and
    #     rejects a dashed UUID with "Invalid argument", so passing a session id through
    #     unchanged returned nothing for every session -- silently, until it said so.
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        class R:
            returncode = 0
            stdout = b"journal body\n"
            stderr = b""
        return R()

    real_run, cs.subprocess.run = cs.subprocess.run, fake_run
    try:
        body, err = cs.journal_for("22222222-bbbb-4444-8888-aaaaaaaaaaaa")
    finally:
        cs.subprocess.run = real_run
    check("journal_for asks journalctl for the undashed boot id",
          "22222222bbbb44448888aaaaaaaaaaaa" in seen["cmd"], str(seen["cmd"]))
    check("and no dashed form is passed",
          "22222222-bbbb-4444-8888-aaaaaaaaaaaa" not in seen["cmd"])
    check("and returns the journal body", body == b"journal body\n")
    check("with no error alongside a body", err == "")

    # 2d. Every failing path RETURNS a reason, so the bundle can carry it. A bundle came
    #     off campod-se (2026-10-07) with no journal and no recoverable reason, because
    #     the only account went to stderr and the caller discards stderr on a successful
    #     run.
    def failing_run(code, out=b"", errtext=b""):
        def run(cmd, **kw):
            class R:
                returncode = code
                stdout = out
                stderr = errtext
            return R()
        return run

    for label, fake, want in (
        ("exit 0 with no output", failing_run(0, b""), "exited 0"),
        ("non-zero with stderr", failing_run(1, b"", b"No journal files were found."),
         "No journal files were found."),
        ("non-zero with no stderr", failing_run(1, b"", b""), "no stderr"),
    ):
        real_run, cs.subprocess.run = cs.subprocess.run, fake
        try:
            body, err = cs.journal_for("22222222-bbbb-4444-8888-aaaaaaaaaaaa")
        finally:
            cs.subprocess.run = real_run
        check(f"{label}: no body", body == b"")
        check(f"{label}: a reason is returned", want in err, err)

    def missing_journalctl(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    real_run, cs.subprocess.run = cs.subprocess.run, missing_journalctl
    try:
        body, err = cs.journal_for("22222222-bbbb-4444-8888-aaaaaaaaaaaa")
    finally:
        cs.subprocess.run = real_run
    check("absent journalctl: a reason is returned", "not installed" in err, err)

    # 3. package the closed one, for real
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cs.cmd_package(captures, "11111111-aaaa", out_dir)
    check("packages a closed session", rc == 0)
    summary = json.loads(buf.getvalue())
    bundle = Path(summary["bundle"])
    check("bundle exists where it said", bundle.is_file())
    check("no .partial left behind", not list(out_dir.glob("*.partial")))

    # 4. the summary's hash is the bundle's hash
    actual = hashlib.sha256(bundle.read_bytes()).hexdigest()
    check("reported sha256 is the bundle's", summary["sha256"] == actual,
          f"{summary['sha256'][:16]} vs {actual[:16]}")

    # 5. round-trip: every file comes back, and every hash in the manifest is right
    # The bundle is zstd, which tarfile cannot open directly; decompress through the
    # same CLI that wrote it so the test exercises a real round trip.
    raw_tar = subprocess.run(["zstd", "-d", "-q", "-c", str(bundle)],
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw_tar)) as tar:
        names = tar.getnames()
        mf = json.loads(tar.extractfile("campod-se/11111111-aaaa/manifest.json").read())
        bad = []
        for rel, meta in mf["files"].items():
            member = tar.extractfile(f"campod-se/11111111-aaaa/{rel}")
            if member is None:
                bad.append(f"{rel}: missing")
                continue
            body = member.read()
            if hashlib.sha256(body).hexdigest() != meta["sha256"]:
                bad.append(f"{rel}: hash")
            elif len(body) != meta["bytes"]:
                bad.append(f"{rel}: size")
    check("manifest covers every source file", len(mf["files"]) == 11, str(len(mf["files"])))
    check("every manifest hash matches the packed bytes", not bad, "; ".join(bad[:3]))
    check("the session's own manifests are inside",
          "campod-se/11111111-aaaa/manifests/fleet-image" in names)
    check("nothing from the other session leaked in",
          not [n for n in names if "22222222" in n])

    # 6. a hash that would pass for any content is not a test; corrupt one and re-check
    rel, meta = next(iter(mf["files"].items()))
    check("a wrong hash would be caught",
          hashlib.sha256(b"not the bytes").hexdigest() != meta["sha256"])

    # 7. refuses when the filesystem cannot hold a second copy
    # Big enough that need exceeds free on any real filesystem; 10**9 against a
    # ~2 TB /tmp was not, and the check passed for the wrong reason.
    huge = cs.FREE_MARGIN
    cs.FREE_MARGIN = 10**15
    with redirect_stdout(io.StringIO()):
        rc = cs.cmd_package(captures, "11111111-aaaa", out_dir)
    cs.FREE_MARGIN = huge
    check("refuses when free space will not cover it", rc == 1)
    check("and cleaned up after refusing", not list(out_dir.glob("*.partial")))

    # 8. delete takes a list, and takes the bundle with the session
    make_session(captures, "campod-se", "33333333-cccc", frames=2)
    bundle_before = list(out_dir.glob("campod-se_11111111-aaaa.tar.zst"))
    check("the packaged bundle is on disk before deleting", len(bundle_before) == 1)
    freed_expect = sum(p.stat().st_size for p in closed.rglob("*") if p.is_file()) \
        + bundle_before[0].stat().st_size

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cs.cmd_delete(captures, ["11111111-aaaa", "33333333-cccc"], out_dir)
    res = json.loads(buf.getvalue())
    check("delete exits 0", rc == 0)
    check("removes every session named", not closed.exists()
          and not (captures / "campod-se" / "33333333-cccc").exists())
    check("removes the bundle with its session", not bundle_before[0].exists())
    by_s = {r["session"]: r for r in res["deleted"]}
    check("reports the bundle it removed",
          by_s["11111111-aaaa"]["bundle"].endswith("campod-se_11111111-aaaa.tar.zst"))
    check("counts session plus bundle bytes",
          by_s["11111111-aaaa"]["bytes"] == freed_expect,
          f"{by_s['11111111-aaaa']['bytes']} vs {freed_expect}")
    check("a session with no bundle reports absent",
          by_s["33333333-cccc"]["bundle"] == "absent")
    check("leaves the sessions it was not asked about",
          (captures / "campod-se" / "22222222-bbbb").is_dir())

    # 9. the open session is deletable -- refusing it would make the current boot
    #    the one directory that cannot be pruned without a reboot
    with redirect_stdout(io.StringIO()):
        rc = cs.cmd_delete(captures, ["22222222-bbbb"], out_dir)
    check("deletes the open session rather than refusing it",
          rc == 0 and not (captures / "campod-se" / "22222222-bbbb").exists())
    # The boot's collectd tree goes with it. Nothing else deletes it: there is no age
    # bound on the card (#595), so a delete that left it would leave it forever.
    check("and takes that boot's collectd tree with it",
          not (cs.COLLECTD_ROOT / "22222222-bbbb").exists())
    check("and no other boot's", (cs.COLLECTD_ROOT / "99999999-other").is_dir())

    # A boot that captured nothing still has collectd, and is still prunable by its id.
    (cs.COLLECTD_ROOT / "44444444-dddd" / "campod-se").mkdir(parents=True)
    (cs.COLLECTD_ROOT / "44444444-dddd" / "campod-se" / "cpu").write_text("t,v\n1,2\n")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cs.cmd_delete(captures, ["44444444-dddd"], out_dir)
    _only_cd = json.loads(buf.getvalue())["deleted"][0]
    check("a boot with only collectd is deleted by its id",
          rc == 0 and not (cs.COLLECTD_ROOT / "44444444-dddd").exists()
          and _only_cd["session_dir"] == "absent" and _only_cd["bytes"] == 8,
          str(_only_cd))

    # 10. idempotent: absent is the wanted end state, not a failure
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cs.cmd_delete(captures, ["11111111-aaaa"], out_dir)
    again = json.loads(buf.getvalue())
    check("deleting an absent session is not an error", rc == 0)
    check("and says absent rather than deleted",
          again["deleted"][0]["session_dir"] == "absent" and again["bytes"] == 0)

    # 11. an id that is not a single directory name is refused, not resolved
    for bad in ("../../etc", "a/b", "..", ""):
        with redirect_stdout(io.StringIO()):
            rc = cs.cmd_delete(captures, [bad], out_dir)
        check(f"refuses {bad!r} as a session id", rc == 1)
    check("and /etc still exists", Path("/etc").is_dir())

    # 8b. end to end through the CLI, including the missing-session path
    p = subprocess.run(
        [sys.executable, str(CLI / "coord-sessions"), "--captures-root", str(captures), "list"],
        capture_output=True, text=True)
    check("CLI list exits 0 and emits JSON", p.returncode == 0 and json.loads(p.stdout))
    p = subprocess.run(
        [sys.executable, str(CLI / "coord-sessions"), "--captures-root", str(captures),
         "package", "does-not-exist"], capture_output=True, text=True)
    check("CLI names an unknown session on stderr",
          p.returncode == 1 and "no session" in p.stderr, p.stderr.strip()[:60])
finally:
    shutil.rmtree(root, ignore_errors=True)

# The PPS edge journal is its own namespace, so it is fetched separately and its
# presence is stated either way -- same reason as the main journal: an absent member
# says nothing about whether it was wanted. Most nodes have no buffer leg soldered, so
# absent is the common case and has to be distinguishable from a broken selector.
_calls = []
_real_run = cs.subprocess.run


def _fake_run(cmd, **kw):
    _calls.append(cmd)
    return _real_run(["true"], **kw)


cs.subprocess.run = _fake_run
cs.journal_for("11111111-aaaa-bbbb-cccc-dddddddddddd")
cs.journal_for("11111111-aaaa-bbbb-cccc-dddddddddddd", namespace="pps")
cs.subprocess.run = _real_run
check("the main journal is fetched without a namespace",
      "--namespace=pps" not in _calls[0], str(_calls[0]))
check("the pps journal asks for its namespace by name",
      "--namespace=pps" in _calls[1], str(_calls[1]))
check("both strip the dashes from the session id",
      all("11111111aaaabbbbccccdddddddddddd" in c for c in _calls), str(_calls))

# ---- the journal is read BEFORE the archive stream ------------------------------------
# It used to be read after every session file and the whole collectd tree had gone through
# tar into zstd -- 300+ MB off the same card -- and journalctl then hit its 120 s timeout on
# three bundles of four, always campod-sw, whose session is the bigger one because it
# carries the accel. Capture is quiesced before packaging, so the contention was our own
# pass. Ordering is the fix, and ordering is what this holds: the call has to happen before
# the zstd process exists.
_order = []
_real_popen, _real_run = cs.subprocess.Popen, cs.subprocess.run


class _NoopPopen:
    def __init__(self, *a, **k):
        _order.append("zstd")
        self.stdin = open("/dev/null", "wb")

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _spy_run(cmd, **kw):
    if cmd and cmd[0] == "journalctl":
        _order.append("journalctl")
        class _P:
            returncode, stdout, stderr = 0, b"body\n", b""
        return _P()
    return _real_run(cmd, **kw)


cs.subprocess.Popen, cs.subprocess.run = _NoopPopen, _spy_run
try:
    cs.journal_for("11111111-aaaa-bbbb-cccc-dddddddddddd")
    _order_snapshot = list(_order)
finally:
    cs.subprocess.Popen, cs.subprocess.run = _real_popen, _real_run
check("journal_for is callable without an archive in flight",
      _order_snapshot == ["journalctl"], str(_order_snapshot))
# The ordering itself, read off the source: the two journal reads must appear before the
# Popen that starts zstd. Asserting on the text is weak, but packaging a real session in a
# unit test is not available and the alternative is asserting nothing.
_src = (CLI / "coord-sessions").read_text()
# This harness prints `detail` whether the check passed or not, so it says what was
# compared rather than what would be wrong.
check("both journals are read before zstd starts",
      _src.index("jrn, jrn_error = journal_for(session)") < _src.index('"zstd", "-q"'),
      f"journal_for at {_src.index('jrn, jrn_error = journal_for(session)')}, "
      f"zstd at {_src.index(chr(34) + 'zstd' + chr(34) + ', ' + chr(34) + '-q' + chr(34))}")

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_coord_sessions: all checks passed")
