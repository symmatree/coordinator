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
    check("reports a probe version", doc.get("host", {}).get("FLEET_PROBE_VERSION") == "1")
except tomllib.TOMLDecodeError as exc:
    check("probe output parses as TOML", False, str(exc))

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_coord_version: all checks passed")
