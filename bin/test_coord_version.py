#!/usr/bin/env python3
"""Checks for bin/coord-version. Run by hand: python3 bin/test_coord_version.py

No CI job runs python tests under bin/, harness/ or analysis/ today, so this is
a hand-run check like its neighbours.

What it protects: the probe must ANSWER on a machine that is missing things,
because the caller has to tell "nothing installed" apart from "could not ask"
(#326). A probe that raises on an empty machine collapses those two into one.
"""

import importlib.machinery
import importlib.util
import io
import subprocess
import sys
import tomllib
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_loader(
    "coord_version",
    importlib.machinery.SourceFileLoader("coord_version", str(HERE / "coord-version")),
)
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


def emitted(table, pairs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        cv.emit(table, pairs)
    return buf.getvalue()


# 1. OCI label names become the controlled keys.
check(
    "label name maps to the controlled key",
    cv.key_of("org.opencontainers.image.revision") == "ORG_OPENCONTAINERS_IMAGE_REVISION",
    cv.key_of("org.opencontainers.image.revision"),
)

# 2. What emit() writes is parseable and obeys the value rules. A '"' or '$' in a
#    value would break the parse or expand on source, so they must not survive.
out = emitted("unit_x", {"ORG_OPENCONTAINERS_IMAGE_REVISION": 'ab"cd$ef', "Z_EXTRA": "1"})
parsed = tomllib.loads(out)
check("emit output parses as TOML", "unit_x" in parsed)
check(
    "quote, dollar and backslash are stripped from values",
    parsed["unit_x"]["ORG_OPENCONTAINERS_IMAGE_REVISION"] == "abcdef",
    repr(parsed["unit_x"]["ORG_OPENCONTAINERS_IMAGE_REVISION"]),
)
# The stated grammar is KEY="VALUE" with VALUE matching [^"\\$\n]*. A consumer
# rejects any line that does not match, so every line we emit must.
import re as _re
grammar = _re.compile(r'^[A-Z0-9_]+="[^"\\$]*"$')
bad = [ln for ln in emitted("u", {"A": 'x"y\\z$w', "B": "ok"}).splitlines()
       if ln and not ln.startswith("[") and not grammar.match(ln)]
check("every emitted line matches the stated grammar", not bad, str(bad))
check(
    "controlled keys sort before extras",
    out.index("ORG_OPENCONTAINERS_IMAGE_REVISION") < out.index("Z_EXTRA"),
)
check("table name is sanitised", "[unit_x]" in out)

# 3. The manifest format from #326 round-trips.
man = HERE.parent / ".probe-fixture"
man.write_text(
    '# comment\n'
    'ORG_OPENCONTAINERS_IMAGE_REVISION="0c8b713f9a"\n'
    'FLEET_ROLE="campod"\n'
)
old, cv.FLEET_IMAGE = cv.FLEET_IMAGE, man
got = cv.disk_image()
cv.FLEET_IMAGE = old
man.unlink()
check(
    "disk image manifest is read",
    got.get("ORG_OPENCONTAINERS_IMAGE_REVISION") == "0c8b713f9a" and got.get("FLEET_ROLE") == "campod",
    str(got),
)

# 4. Absence reports rather than raises -- the property the caller depends on.
old, cv.FLEET_IMAGE = cv.FLEET_IMAGE, Path("/nonexistent/fleet-image")
got = cv.disk_image()
cv.FLEET_IMAGE = old
check("absent disk manifest yields an error key", "FLEET_PROBE_ERROR" in got, str(got))

got = cv.checkout(Path("/nonexistent/checkout"))
check("absent checkout yields an error key", "FLEET_PROBE_ERROR" in got, str(got))

# 5. Absence and failure are distinguishable PER KIND, which a per-unit key
#    cannot carry: a kind that failed to enumerate produced no unit to hang an
#    error on. Zero units with no error means the machine has none; an error
#    means we could not ask.
import subprocess as _sp

def probe_env(**env):
    import os
    e = dict(os.environ, **env)
    p = _sp.run([sys.executable, str(HERE / "coord-version")], capture_output=True, text=True, env=e)
    return tomllib.loads(p.stdout)

doc = probe_env(PATH="/nonexistent")  # no docker on PATH at all
enum = doc.get("enumeration", {})
check(
    "docker absent reports a kind-level error",
    enum.get("FLEET_ENUM_CONTAINER") == "docker is not installed"
    and enum.get("FLEET_ENUM_CONTAINER_COUNT") == "0",
    f"{enum.get('FLEET_ENUM_CONTAINER')!r} count={enum.get('FLEET_ENUM_CONTAINER_COUNT')!r}",
)
check(
    "no container tables when enumeration failed",
    not [t for t in doc if t.startswith("container_")],
)
check(
    "disk image read failure stays a UNIT error, not a kind error",
    enum.get("FLEET_ENUM_DISK_IMAGE") == "" and "FLEET_PROBE_ERROR" in doc["disk_image"],
    f"enum={enum.get('FLEET_ENUM_DISK_IMAGE')!r}",
)

# 6. Every unit table carries kind and id, and the ids are unique.
for table, pairs in doc.items():
    if table in ("enumeration", "host"):
        continue
    check(f"{table} carries kind and id",
          "FLEET_UNIT_KIND" in pairs and "FLEET_UNIT_ID" in pairs, str(sorted(pairs))[:60])
ids = [p["FLEET_UNIT_ID"] for t, p in doc.items()
       if t not in ("enumeration", "host") and "FLEET_UNIT_ID" in p]
check("unit ids are unique within the machine", len(ids) == len(set(ids)), str(ids))

# 7. End to end: exit 0 and valid TOML even on this machine, which has no
#    /etc/fleet-image and no reachable docker.
p = subprocess.run([sys.executable, str(HERE / "coord-version")], capture_output=True, text=True)
check("probe exits 0 with things missing", p.returncode == 0, f"rc={p.returncode}")
try:
    doc = tomllib.loads(p.stdout)
    check("probe output parses as TOML", True, f"{len(doc)} tables")
    check(
        "every value is a string",
        all(isinstance(v, str) for t in doc.values() for v in t.values()),
    )
    check("reports a probe version", doc.get("host", {}).get("FLEET_PROBE_VERSION") == "2")
except tomllib.TOMLDecodeError as exc:
    check("probe output parses as TOML", False, str(exc))

# 8. Free space on the data volume (#302): a real filesystem, checked against an
#    independent statvfs rather than against the probe's own call, and only
#    reported when exactly one stack makes "the data volume" unambiguous.
host = doc.get("host", {}) if "doc" in dir() else {}
check(
    "data-volume keys always present",
    {"FLEET_DATA_PATH", "FLEET_DATA_FREE_BYTES", "FLEET_DATA_TOTAL_BYTES"} <= set(host),
    str(sorted(host)),
)
check(
    "no stack installed here, so the data volume is blank rather than guessed",
    host.get("FLEET_DATA_PATH") == "" and host.get("FLEET_DATA_FREE_BYTES") == "",
    f"path={host.get('FLEET_DATA_PATH')!r} free={host.get('FLEET_DATA_FREE_BYTES')!r}",
)

import os as _os

# /var/lib/dpkg stands in for a stack's state root: a real directory under the
# same base, so the call under test takes its normal path.
_probe = Path("/var/lib/dpkg")
_st = _os.statvfs(_probe)
_got = cv.data_volume(["dpkg"])
check("names the path it measured", _got["FLEET_DATA_PATH"] == str(_probe), _got["FLEET_DATA_PATH"])
check(
    "measures the filesystem it names",
    _got["FLEET_DATA_TOTAL_BYTES"] == str(_st.f_blocks * _st.f_frsize),
    f"{_got['FLEET_DATA_TOTAL_BYTES']} vs {_st.f_blocks * _st.f_frsize}",
)
# Not asserted: that free came from f_bavail rather than f_bfree. They are equal
# on this filesystem, so the check could not fail and would be theatre. The
# choice is in the docstring; a box with a root reserve would be needed to test it.
_free, _total = int(_got["FLEET_DATA_FREE_BYTES"]), int(_got["FLEET_DATA_TOTAL_BYTES"])
check(
    "free is a plausible fraction of total, not a copy of it",
    0 < _free < _total,
    f"free={_free} total={_total}",
)
check(
    "several stacks is not a data volume",
    cv.data_volume(["a", "b"])["FLEET_DATA_PATH"] == "",
)
check(
    "a path that is not there reports the path and no numbers",
    cv.data_volume(["definitely-not-a-stack"])["FLEET_DATA_FREE_BYTES"] == "",
)

# 9. Containers: one inspect for every distinct image, not two per container. A campod
#    runs BOTH containers from the same image, so this is the difference between 1
#    dockerd round-trip and 4 -- and dockerd is what costs 2.1-22.0s per call under load.
calls = []
real_run, real_which = cv.run, cv.shutil.which
cv.shutil.which = lambda _: "/usr/bin/docker"


def fake_run(*cmd, timeout=20):
    calls.append(cmd)
    if cmd[:2] == ("docker", "ps"):
        return 0, "campod_camera\timg:main\trunning\ncampod_accel\timg:main\trunning"
    if cmd[:2] == ("docker", "inspect"):
        return 0, '{"org.opencontainers.image.revision":"abc"}\timg@sha256:dd'
    return 1, ""


cv.run = fake_run
tables, err = cv.containers()
inspects = [c for c in calls if c[:2] == ("docker", "inspect")]
check("enumeration succeeded", err == "", err)
check("one inspect call, not one per container", len(inspects) == 1, str(len(inspects)))
check("and it asks for the image once, not twice",
      sum(1 for a in inspects[0] if a == "img:main") == 1, str(inspects[0]))
check("both containers got the labels", sum(
    1 for _, p in tables if p.get("ORG_OPENCONTAINERS_IMAGE_REVISION") == "abc") == 2)
check("both containers got the digest", sum(
    1 for _, p in tables if p.get("FLEET_CONTAINER_IMAGE_DIGEST") == "img@sha256:dd") == 2)
check("no probe error when it worked",
      not any("FLEET_PROBE_ERROR" in p for _, p in tables))

# 10. An image never pushed has no RepoDigests. `index` on an empty list aborts the
#     template for EVERY object in the call, so the guard is not optional.
def fake_no_digest(*cmd, timeout=20):
    calls.append(cmd)
    if cmd[:2] == ("docker", "ps"):
        return 0, "c1\timg:local\trunning"
    if cmd[:2] == ("docker", "inspect"):
        return 0, '{"a":"b"}\t'
    return 1, ""


cv.run = fake_no_digest
tables, _ = cv.containers()
check("no digest is not an error", not any("FLEET_PROBE_ERROR" in p for _, p in tables))
check("and no empty digest key is emitted",
      not any("FLEET_CONTAINER_IMAGE_DIGEST" in p for _, p in tables))
check("the template guards RepoDigests",
      any("if .RepoDigests" in a for c in calls if c[:2] == ("docker", "inspect") for a in c))

# 11. A failed inspect is a per-container error, not a lost unit.
def fake_bad(*cmd, timeout=20):
    if cmd[:2] == ("docker", "ps"):
        return 0, "c1\timg:x\trunning"
    return 1, ""


cv.run = fake_bad
tables, _ = cv.containers()
check("inspect failure still yields the unit", len(tables) == 1)
check("and records why", any("FLEET_PROBE_ERROR" in p for _, p in tables))

cv.run, cv.shutil.which = real_run, real_which

# 12. The dirty check is gone, and with it the working-tree walk.
src = (HERE / "coord-version").read_text()
check("no git status tree walk", "status" not in src or "--porcelain" not in src)
check("FLEET_CHECKOUT_DIRTY retired", "FLEET_CHECKOUT_DIRTY" not in src)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_coord_version: all checks passed")
