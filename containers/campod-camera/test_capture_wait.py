#!/usr/bin/env python3
"""Hardware-free test for the camera wait loop (coordinator #278).

No Pi, no camera, no libcamera: a fake `picamera2` stands in, so the behaviour
that only an absent camera produces can be exercised on a build machine --

  * a camera present on the first probe returns immediately and never sleeps
  * an absent camera is waited for and re-probed, not treated as fatal
  * a camera appearing later is picked up without a process restart
  * SIGTERM during the wait returns promptly instead of hanging for a full
    probe interval (docker stop's grace period is 10 s)

The regression this guards is the crash loop: capture.py used to `return 1` on an
absent camera, so docker restarted it and each restart re-paid the picamera2
import.

picamera2 is stubbed rather than imported for real on purpose: the real import is
the expensive thing being avoided, and it has no camera to find on a builder.

Runs at image build time, so a reintroduced crash loop fails the build.

    python3 test_capture_wait.py
"""
import importlib.util
import sys
import threading
import time
import types

fake = types.ModuleType("picamera2")


class FakePicamera2:
    """Answers global_camera_info() from a scripted queue, then with []."""

    answers = []

    @staticmethod
    def global_camera_info():
        return FakePicamera2.answers.pop(0) if FakePicamera2.answers else []


fake.Picamera2 = FakePicamera2
sys.modules["picamera2"] = fake
_libcamera = types.ModuleType("libcamera")
_libcamera.controls = types.SimpleNamespace()
sys.modules["libcamera"] = _libcamera

_spec = importlib.util.spec_from_file_location("capture", "capture.py")
capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capture)

# Real cadence is 30 s; compress it so the test costs ~2 s instead of ~2 min.
# The ratio between interval and heartbeat is what is under test, not the values.
capture.CAMERA_PROBE_INTERVAL_S = 0.5
capture.CAMERA_WAIT_HEARTBEAT_PROBES = 2

CAM = {"Model": "imx708_wide", "Id": "/base/soc/i2c0mux/i2c@1/imx708@1a"}
failures = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'  ' + detail if detail else ''}")
    if not cond:
        failures.append(label)


# 1. Camera already there: return it, do not sleep.
capture._stop = False
FakePicamera2.answers = [[CAM]]
t0 = time.monotonic()
got = capture._wait_for_camera()
elapsed = time.monotonic() - t0
check("present on first probe returns the camera", got == [CAM], repr(got))
check("present on first probe does not sleep", elapsed < 0.2, f"{elapsed:.2f}s")

# 2. Absent, then appears: waited for, not fatal. Three misses then a hit.
capture._stop = False
FakePicamera2.answers = [[], [], [], [CAM]]
t0 = time.monotonic()
got = capture._wait_for_camera()
elapsed = time.monotonic() - t0
check("absent camera is not fatal", got == [CAM], repr(got))
check(
    "re-probed until it appeared",
    1.4 < elapsed < 2.3,
    f"{elapsed:.2f}s for 3 x {capture.CAMERA_PROBE_INTERVAL_S}s",
)

# 3. Never appears and SIGTERM arrives: prompt None, no hang.
capture._stop = False
FakePicamera2.answers = []
threading.Thread(
    target=lambda: (time.sleep(0.6), capture._request_stop(15, None)), daemon=True
).start()
t0 = time.monotonic()
got = capture._wait_for_camera()
elapsed = time.monotonic() - t0
check("stop while waiting returns None", got is None, repr(got))
check(
    "stop interrupts the sleep rather than waiting it out",
    elapsed < 1.0,
    f"{elapsed:.2f}s",
)

# 4. A session records what produced it. NAME= drives the filename, both sources
#    land, and no temp file survives the rename.
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as td:
    src, session = Path(td) / "src", Path(td) / "session"
    src.mkdir()
    body = (
        "# comment\n"
        'ORG_OPENCONTAINERS_IMAGE_REVISION="deadbeef"\n'
        'FLEET_UNIT="campod-camera"\n'
    )
    (src / "container-image").write_text(body)
    (src / "fleet-image").write_text('ORG_OPENCONTAINERS_IMAGE_REVISION="0c8b713f9a"\n')
    capture.MANIFEST_SOURCES = (
        (src / "container-image", None),
        (src / "fleet-image", "fleet-image"),
    )
    session.mkdir()
    capture._copy_manifests(session)
    m = session / "manifests"
    check("FLEET_UNIT= drives the manifest filename", (m / "campod-camera").is_file())
    check("manifest content is copied verbatim", (m / "campod-camera").read_text() == body)
    check("the image manifest is recorded too", (m / "fleet-image").is_file())
    check("no temp file survives the rename", not [p for p in m.iterdir() if p.name.startswith(".")])

# 5. A missing manifest is logged, not fatal -- losing frames is worse than an
#    unattributed session.
with tempfile.TemporaryDirectory() as td:
    session = Path(td) / "session"
    session.mkdir()
    capture.MANIFEST_SOURCES = ((Path(td) / "absent", None),)
    capture._copy_manifests(session)
    check("missing manifest does not raise", (session / "manifests").is_dir())

# 6. The manifest this image's Dockerfile actually wrote satisfies #326's format:
#    valid TOML, sourceable by /bin/sh, and carrying the controlled keys. Checked
#    against the real file rather than a fixture, because the two rules that make
#    it sourceable -- no whitespace around '=', values double-quoted, no '$' --
#    are easy to break in a printf and break silently.
import re as _re
import subprocess
import tomllib

# --require-manifest is passed by the Dockerfile, where the file must exist by
# then. A bare local run skips instead of failing, since nothing has written it.
baked = Path("/etc/container-image")
if not baked.is_file():
    if "--require-manifest" in sys.argv:
        check("the image carries /etc/container-image", False, "absent")
    else:
        print("skip  baked-manifest checks (no /etc/container-image; pass --require-manifest to require)")
else:
    text = baked.read_text()
    try:
        parsed = tomllib.loads(text)
        check("baked manifest parses as TOML", True)
    except Exception as exc:
        parsed = {}
        check("baked manifest parses as TOML", False, str(exc))
    check(
        "carries the controlled keys",
        {"ORG_OPENCONTAINERS_IMAGE_SOURCE", "ORG_OPENCONTAINERS_IMAGE_REVISION",
         "FLEET_SOURCE_REF"} <= set(parsed),
        str(sorted(parsed)),
    )
    kv = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    check(
        "no whitespace around '=' (would break `source`)",
        not [ln for ln in kv if _re.search(r"\s=|=\s", ln)],
    )
    check(
        "every value is double-quoted",
        not [ln for ln in kv if not _re.match(r'^[A-Z0-9_]+="[^"]*"$', ln)],
    )
    check("no '$' in any value (sourcing would expand it)", "$" not in text)
    rc = subprocess.run(
        ["/bin/sh", "-c", f'set -a; . {baked}; set +a; [ -n "$FLEET_UNIT" ]'],
    ).returncode
    check("sourceable by /bin/sh with FLEET_UNIT set", rc == 0, f"rc={rc}")

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_capture_wait: all checks passed")
