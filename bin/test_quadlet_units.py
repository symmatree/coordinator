#!/usr/bin/env python3
"""Run podman's own quadlet generator over the stack files and check what it produces.

These units are the whole deployment: a wrong key is a device that does not capture,
and quadlet fails SOFTLY -- an unrecognised key in the wrong section is passed through
to systemd, which ignores it, so the container starts without the setting and nothing
says so. That is exactly what happened while writing them: `PodmanArgs=--stop-timeout`
sat in [Service], where quadlet does not look, and the generated ExecStart carried no
stop timeout at all. It generated cleanly and would have shipped.

So this asserts on the GENERATED ExecStart rather than on the input, because the input
looking right is what that failure mode gives you.

SKIPS when podman's generator is absent, which it is on the notebook image CI runs.
That is deliberate -- the check is worth having where it can run, and a skip is honest
where it cannot. On a machine with podman:

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
        # A warning is how quadlet reports a key it could not use. Nothing should be
        # warning about units we ship.
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

        # Group membership both ways: WantedBy is what makes the stack come up at boot,
        # PartOf is what makes one `systemctl stop` take the whole stack down.
        check(f"{name}: WantedBy the stack target", f"WantedBy={target}" in text)
        check(f"{name}: PartOf the stack target", f"PartOf={target}" in text)

        # A power-up must need no network (deployment-model.md), so no unit may fetch.
        check(f"{name}: never fetches at start", "--pull never" in exec_start,
              exec_start[-200:])

        # No restart policy anywhere: a container that dies mid-run stays dead until
        # the next boot, and a restart policy once fought the boot path during a
        # shutdown.
        check(f"{name}: no restart policy", "Restart=no" in text)

        # Every stop timeout declared in the source has to reach podman. This is the
        # assertion that would have caught the [Service]-versus-[Container] mistake.
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

        # Device passthrough, which is the payload itself. AddDevice becomes --device=
        # with an equals sign; searching for "--device " finds nothing and looks like a
        # pass.
        for dev in re.findall(r"^AddDevice=(\S+)", src, re.M):
            check(f"{name}: {dev} passed through",
                  f"--device={dev}" in exec_start, exec_start[-300:])

        # Every bind mount declared reaches the run. The camera's /run/udev and the
        # tracker's /dev/bus/usb are both load-bearing and both easy to lose silently.
        for vol in re.findall(r"^Volume=(\S+)", src, re.M):
            check(f"{name}: mounts {vol.split(':')[0]}",
                  f"-v {vol}" in exec_start, exec_start[-300:])

        # Privileged and host-pid are how the camera and the bus link reach hardware
        # and sibling containers respectively.
        if "--privileged" in src:
            check(f"{name}: privileged", "--privileged" in exec_start)
        if "--pid=host" in src:
            check(f"{name}: host pid namespace", "--pid=host" in exec_start)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_quadlet_units: all checks passed")
