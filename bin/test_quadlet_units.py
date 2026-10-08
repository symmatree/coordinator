#!/usr/bin/env python3
"""Run podman's quadlet generator over the stack files and check what it produces.

Asserts on the GENERATED ExecStart, not on the unit files: quadlet ignores a key it
does not recognise in a section it does not read, so a unit with a setting in the wrong
place generates cleanly and runs without it. Checking the input cannot see that.

Skips where podman is absent, which includes the image CI runs on.

    python3 bin/test_quadlet_units.py
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GENERATOR = Path("/usr/libexec/podman/quadlet")
REPO = Path(__file__).resolve().parent.parent

failures = []


def check(label, cond, detail=""):
    if cond:
        print(f"ok   {label}")
    else:
        print(f"FAIL {label}  {detail}")
        failures.append(label)


def generate(stack_dir: Path) -> dict[str, str]:
    """unit name -> generated .service text, via the real generator."""
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
        for unit in stack_dir.glob("*.container"):
            (Path(src) / unit.name).write_text(unit.read_text())
        p = subprocess.run(
            [str(GENERATOR), out],
            env={"QUADLET_UNIT_DIRS": src, "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, timeout=120,
        )
        if p.returncode != 0:
            check(f"{stack_dir.name}: generator exited 0", False, p.stderr.strip()[:300])
            return {}
        # A warning is how quadlet reports a key it could not use.
        noise = [ln for ln in p.stderr.splitlines() if ln.strip()]
        check(f"{stack_dir.name}: generator emitted no warnings", not noise,
              " | ".join(noise)[:300])
        return {f.stem: f.read_text() for f in Path(out).glob("*.service")}


if not GENERATOR.is_file():
    print(f"skip  {GENERATOR} is absent; podman is not installed here")
    sys.exit(0)

for stack in sorted((REPO / "stacks").iterdir()):
    if not stack.is_dir():
        continue
    units = generate(stack)
    if not units:
        continue
    declared = {u.stem for u in stack.glob("*.container")}
    check(f"{stack.name}: every .container produced a .service",
          declared <= set(units), f"missing {sorted(declared - set(units))}")

    target = f"{stack.name}-stack.target"
    check(f"{stack.name}: ships its stack target", (stack / target).is_file())

    for name, text in sorted(units.items()):
        exec_start = next((ln for ln in text.splitlines()
                           if ln.startswith("ExecStart=")), "")
        src = (stack / f"{name}.container").read_text()

        # WantedBy brings the stack up at boot; PartOf makes one stop take it down.
        check(f"{name}: WantedBy the stack target", f"WantedBy={target}" in text)
        check(f"{name}: PartOf the stack target", f"PartOf={target}" in text)

        # A power-up must need no network: docs/deployment-model.md.
        check(f"{name}: never fetches at start", "--pull never" in exec_start,
              exec_start[-200:])

        # A container that dies mid-run stays dead until the next boot.
        check(f"{name}: no restart policy", "Restart=no" in text)

        # Every stop timeout declared in the source has to reach podman.
        declared_timeouts = re.findall(r"--stop-timeout=(\d+)", src)
        for secs in declared_timeouts:
            check(f"{name}: --stop-timeout={secs} reaches the ExecStart",
                  f"--stop-timeout={secs}" in exec_start, exec_start[-200:])
        check(f"{name}: declares a stop timeout at all", bool(declared_timeouts))

        # systemd must outlast podman or it kills the thing doing the waiting.
        m = re.search(r"^TimeoutStopSec=(\d+)", text, re.M)
        check(f"{name}: declares TimeoutStopSec", m is not None)
        if m and declared_timeouts:
            check(f"{name}: systemd outlasts podman's stop timeout",
                  int(m.group(1)) > int(declared_timeouts[0]),
                  f"TimeoutStopSec={m.group(1)} vs --stop-timeout={declared_timeouts[0]}")

        # AddDevice becomes --device= with an equals sign.
        for dev in re.findall(r"^AddDevice=(\S+)", src, re.M):
            check(f"{name}: {dev} passed through",
                  f"--device={dev}" in exec_start, exec_start[-300:])

        # Every declared bind mount reaches the run.
        for vol in re.findall(r"^Volume=(\S+)", src, re.M):
            check(f"{name}: mounts {vol.split(':')[0]}",
                  f"-v {vol}" in exec_start, exec_start[-300:])


        if "--privileged" in src:
            check(f"{name}: privileged", "--privileged" in exec_start)
        if "--pid=host" in src:
            check(f"{name}: host pid namespace", "--pid=host" in exec_start)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_quadlet_units: all checks passed")
