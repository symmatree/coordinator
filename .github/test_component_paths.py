#!/usr/bin/env python3
"""Every file a container image COPYs is a path its component watches.

The case this exists for: `containers/fleet-control/Dockerfile` gained
`COPY harness/bench_cycle.py`, and `fleet-control`'s paths in components.json did not gain
`harness/`. So an edit to that script changed the image's contents and built nothing -- the
`changes` job saw no fleet-control path touched, its build job was skipped, and a skipped job
reports success (docs/ci.md). The merge was green, the image was stale, and the next workflow
step ran the previous build. That cost a bench cycle before anyone noticed.

A component's paths are not documentation of intent; they are the complete list of inputs to
its image. This checks the two agree, in the direction that goes wrong silently.

    python3 .github/test_component_paths.py
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
MANIFEST = HERE / "components.json"

failures = []


def check(label, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def copy_sources(dockerfile: pathlib.Path) -> list[str]:
    """The context-relative sources of every COPY that reads the build context.

    `COPY --from=<stage>` reads an earlier stage, not the context, so those are not inputs
    from the repo. Line continuations are joined first.
    """
    text = dockerfile.read_text().replace("\\\n", " ")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not re.match(r"^COPY\s", line, re.IGNORECASE):
            continue
        args = line.split()[1:]
        if any(a.startswith("--from=") for a in args):
            continue
        args = [a for a in args if not a.startswith("--")]
        out.extend(args[:-1])  # the last argument is the destination
    return out


def covered(path: str, patterns: list[str]) -> bool:
    """Does any declared path cover this file? `a/b/**` covers everything under `a/b/`."""
    for p in patterns:
        if p.endswith("/**"):
            if path == p[:-3] or path.startswith(p[:-2]):
                return True
        elif path == p:
            return True
    return False


family = json.loads(MANIFEST.read_text())["containers"]
machinery = family["machinery"]

for name, comp in family["components"].items():
    dockerfile = ROOT / comp["dockerfile"]
    context = comp["context"]
    declared = comp["paths"] + machinery
    for src in copy_sources(dockerfile):
        resolved = src if context == "." else f"{context.rstrip('/')}/{src}"
        resolved = str(pathlib.PurePosixPath(resolved))
        check(f"{name} watches {resolved}", covered(resolved, declared),
              f"COPYed by {comp['dockerfile']} but no path in components.json covers it")

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_component_paths: all checks passed")
