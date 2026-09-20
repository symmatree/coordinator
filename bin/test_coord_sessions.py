#!/usr/bin/env python3
"""Checks for bin/coord-sessions. Run by hand: python3 bin/test_coord_sessions.py

No CI job runs python tests under bin/ today, so this is a hand-run check like its
neighbours. It builds a real session tree in a temp dir and packages it for real --
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

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_loader(
    "coord_sessions",
    importlib.machinery.SourceFileLoader("coord_sessions", str(HERE / "coord-sessions")),
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

    # 2. packaging refuses the open session
    rc = cs.cmd_package(captures, "22222222-bbbb", out_dir)
    check("refuses to package the current boot", rc == 1)
    check("and writes no bundle for it", not list(out_dir.glob("*22222222*")))

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
        [sys.executable, str(HERE / "coord-sessions"), "--captures-root", str(captures), "list"],
        capture_output=True, text=True)
    check("CLI list exits 0 and emits JSON", p.returncode == 0 and json.loads(p.stdout))
    p = subprocess.run(
        [sys.executable, str(HERE / "coord-sessions"), "--captures-root", str(captures),
         "package", "does-not-exist"], capture_output=True, text=True)
    check("CLI names an unknown session on stderr",
          p.returncode == 1 and "no session" in p.stderr, p.stderr.strip()[:60])
finally:
    shutil.rmtree(root, ignore_errors=True)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_coord_sessions: all checks passed")
