#!/usr/bin/env python3
"""Hardware-free test for the camera wait loop (coordinator #278).

No Pi, no camera, no libcamera: a fake `picamera2` stands in, so the behaviour
that only an absent camera produces can be exercised on a build machine --

  * a camera present on the first probe returns immediately and never sleeps
  * an absent camera is waited for and re-probed, not treated as fatal
  * a camera appearing later is picked up without a process restart
  * SIGTERM during the wait returns promptly instead of hanging for a full
    probe interval (docker stop's grace period is 10 s)
  * the status document pod-link publishes is atomic, carries no wall clock, and
    cannot take capture down when the write fails

The regression this guards is the crash loop: capture.py used to `return 1` on an
absent camera, so docker restarted it and each restart re-paid the picamera2
import.

picamera2 is stubbed rather than imported for real on purpose: the real import is
the expensive thing being avoided, and it has no camera to find on a builder.

Runs at image build time, so a reintroduced crash loop fails the build.

    python3 test_capture_wait.py
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
from contextlib import redirect_stdout
from pathlib import Path

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

# --- the capture gate (#434) -------------------------------------------------
#
# Default-paused is the point: a campod sitting on a bench wrote 29 GB and filled
# its card, which presents as a device that cannot capture rather than as a full
# disk. So the gate's absent/unreadable cases must mean PAUSED -- a gate that fails
# open restores exactly the behaviour being removed.
import tempfile as _tempfile
from pathlib import Path as _Path

_tmp = _Path(_tempfile.mkdtemp())
_flag = _tmp / "campod_capture"

check("absent flag means paused", capture._capture_wanted(_flag) is False)

for _v, _want in (("1", True), ("true", True), ("on", True), ("yes", True),
                  ("0", False), ("", False), ("nonsense", False)):
    _flag.write_text(_v)
    check(f"flag {_v!r} -> {'capture' if _want else 'paused'}",
          capture._capture_wanted(_flag) is _want)

_flag.write_text(" 1\n")
check("surrounding whitespace is tolerated", capture._capture_wanted(_flag) is True)

_unreadable = _tmp / "a-directory-not-a-file"
_unreadable.mkdir()
check("an unreadable flag means paused, not a crash",
      capture._capture_wanted(_unreadable) is False)

check("gating disabled (None) captures continuously",
      capture._capture_wanted(None) is True)

# 7. The status document (pod-link reads this; see docs/pod-bus.md).
import json as _json

with tempfile.TemporaryDirectory() as td:
    _sp = Path(td) / "campod_camera_status"
    _doc = {"node": "campod-sw", "frames": 7, "ready": True, "as_of_mono_s": 12.5}
    capture._write_status(_sp, _doc)
    check("status is written", _sp.is_file())
    check("status round-trips as JSON", _json.loads(_sp.read_text()) == _doc)
    check("no temp file survives the rename",
          [p.name for p in Path(td).iterdir()] == ["campod_camera_status"])

    # Rewritten in place, so a reader always finds one file and never a partial.
    capture._write_status(_sp, {"frames": 8})
    check("status is replaced, not appended", _json.loads(_sp.read_text()) == {"frames": 8})

    # The pod has no RTC: a wall stamp written before time service arrives is
    # wrong by the size of a step that has not happened yet, and nothing in the
    # document would say which side of it produced the number.
    check("the document carries no wall clock",
          not ({"wall_clock_unix", "wall_clock_utc", "wall_ns"}
               & set(_json.loads(_sp.read_text()))))

    # Every failure here is a reason to keep capturing without status, never a
    # reason to stop: the frames are the deliverable.
    try:
        capture._write_status(Path(td) / "no-such-dir" / "status", {"a": 1})
        check("an unwritable status path does not raise", True)
    except Exception as exc:  # noqa: BLE001
        check("an unwritable status path does not raise", False, repr(exc))

    check("status disabled (None) is a no-op", capture._write_status(None, {"a": 1}) is None)

# The sidecar's clock pair, which is what makes a pod-local exposure comparable to
# another pod's. Asserted on the KEYS as much as the values: analysis joins on these
# names, and a rename would silently produce a flight nobody can align.
_clk = capture._clock_triple()
check("clock triple carries boot_ns, mono_ns, wall_ns",
      set(_clk) == {"boot_ns", "mono_ns", "wall_ns"}, ",".join(sorted(_clk)))
check("all three are nanosecond-scale integers",
      all(isinstance(v, int) and v > 0 for v in _clk.values()), repr(_clk))
# Sampled back to back, so boot and mono differ by far less than a frame period. If this
# ever fails the pair is being taken too far apart to convert a timestamp with.
check("boot and mono agree to well under a frame period",
      abs(_clk["boot_ns"] - _clk["mono_ns"]) < 200_000_000,
      f'boot-mono = {_clk["boot_ns"] - _clk["mono_ns"]} ns')
# Wall is an epoch time and the other two are since-boot, so wall must be far larger --
# this catches the transposition that would make every conversion silently wrong.
check("wall is an epoch stamp, not a since-boot one",
      _clk["wall_ns"] > _clk["boot_ns"] * 10, repr(_clk))
_clk2 = capture._clock_triple()
check("successive samples advance", _clk2["mono_ns"] > _clk["mono_ns"])

# ---- the frame-selection rule ------------------------------------------------------
# Pure arithmetic, so it is testable with no camera -- which matters because the rule is
# what makes two pods choose the SAME exposure, and a wrong second is invisible in a single
# pod's data.
_S = 1_000_000_000
check("a frame exactly on the second is offset zero into that second",
      capture._boundary_offset_ns(5 * _S) == (5, 0))
check("a frame mid-second belongs to that second",
      capture._boundary_offset_ns(5 * _S + 600_000_000) == (5, 600_000_000))
# FLOOR, NOT ROUND. Rounding assigns a late frame to the NEXT second, and then the rule
# skips it as "approaching" -- at 1 Hz with an unlucky phase that keeps nothing at all.
check("a frame late in a second is NOT promoted to the next one",
      capture._boundary_offset_ns(6 * _S - 1_000_000) == (5, 999_000_000))
check("offsets are never negative", all(
    capture._boundary_offset_ns(n)[1] >= 0 for n in (0, 1, _S - 1, _S, 7 * _S + 12345)))


def _keepers(exposures):
    """Apply the loop's rule to a list of exposure instants, returning what it keeps."""
    kept, last = [], None
    for t_ns in exposures:
        b, off = capture._boundary_offset_ns(t_ns)
        if b == last:
            continue
        last = b
        kept.append((b, off))
    return kept


# ONE PER SECOND AT ANY RATE AND PHASE, which the window rule could not do: it required the
# frame to be within half a grid period, so a visible subset sitting outside that skipped
# every second until the grid drifted back -- measured as 44 s, 84 s and 230 s holes, with
# warmup alone taking 404 s. Rates and phases are varied deliberately, including the 1 Hz
# phase that broke the rounding version.
for _grid, _label in ((500_000_000, "2 Hz"), (100_000_000, "10 Hz"), (_S, "1 Hz")):
    for _phase in (0, 37_000_000, 499_999_999, 750_000_000, 999_999_999):
        _exp = [_phase + n * _grid for n in range(40)]
        _kept = _keepers(_exp)
        _secs = [b for b, _ in _kept]
        _span = _exp[-1] // _S - _exp[0] // _S
        check(f"{_label}, phase {_phase // 1000000} ms: one per second, none skipped",
              len(_secs) == len(set(_secs)) and len(_secs) >= _span,
              f"{len(_secs)} kept over {_span} s")
        check(f"{_label}, phase {_phase // 1000000} ms: offset is within the second",
              all(0 <= off < _S for _, off in _kept))
# And the keeper is the FIRST frame of its second, not just any of them: at 10 Hz a second
# holds ten candidates and nine must be dropped.
_ten = [37_000_000 + n * 100_000_000 for n in range(30)]
check("at 10 Hz exactly the first frame of each second is kept",
      [off for _, off in _keepers(_ten)] == [37_000_000] * len(_keepers(_ten)),
      str([off // 1000000 for _, off in _keepers(_ten)]))


# ---- the serializer ----------------------------------------------------------------
# It now encodes as well as writes, and holds STRICTLY ONE frame, because the thing being
# bounded is a 35.8 MB raw copy rather than 1.8 MB of JPEG. These are the properties the
# capture loop depends on and cannot see for itself.
_sd = Path(tempfile.mkdtemp(prefix="serializer-"))
_s = capture._Serializer(lambda buf: b"\xff\xd8" + bytes(buf))
_s.put(b"ONE", _sd / "a.jpg", _sd / "a.json", b"{}")
_s.close()
check("the frame is encoded and written", (_sd / "a.jpg").read_bytes() == b"\xff\xd8ONE")
check("and its sidecar lands too", (_sd / "a.json").read_bytes() == b"{}")
check("close() finished the frame in hand", _s.errors == 0)

# An encode or write that raises must not kill the thread or the session.
_s2 = capture._Serializer(lambda buf: (_ for _ in ()).throw(ValueError("boom")))
_s2.put(b"X", _sd / "bad.jpg", _sd / "bad.json", b"{}")
_s2.put(b"Y", _sd / "good.jpg", _sd / "good.json", b"{}")
_s2.close()
check("a failed frame is counted, not fatal", _s2.errors == 2, f"errors={_s2.errors}")

# STRICTLY ONE: the slot is held until the work COMPLETES, so a second put waits. That is
# what bounds memory to a single raw copy.
_slow = Path(tempfile.mkdtemp(prefix="serializer-slow-"))
_gate = threading.Event()
_s3 = capture._Serializer(lambda buf: (_gate.wait(10), b"\xff\xd8" + bytes(buf))[1])
_s3.put(b"FIRST", _slow / "first.jpg", _slow / "first.json", b"{}")
time.sleep(0.3)
_t = threading.Thread(target=lambda: _s3.put(b"SECOND", _slow / "second.jpg",
                                             _slow / "second.json", b"{}"))
_t.start()
time.sleep(0.3)
check("a second frame waits while the first is still being encoded",
      _t.is_alive() and _s3.waits == 1, f"alive={_t.is_alive()} waits={_s3.waits}")
_gate.set()
_t.join(10)
_s3.close()
check("and both frames were written once it caught up",
      (_slow / "first.jpg").exists() and (_slow / "second.json").read_bytes() == b"{}")
shutil.rmtree(_sd, ignore_errors=True)
shutil.rmtree(_slow, ignore_errors=True)

# ---- main() actually starts, and keeps one frame per second ------------------------
# THE TEST THIS FILE SHIPPED WITHOUT. Everything above exercises helpers; nothing ran
# main(), so #523 shipped a startup line referencing a variable defined below it. It
# raised UnboundLocalError on the first second of every boot, Restart=no left the camera
# dead, and both pods captured nothing for a whole cycle while CI was green.
#
# Time is INJECTED, not waited on: the fake hands out exposures on a scripted 200 ms grid
# and the clock conversion is pinned, so the rule's behaviour is deterministic and the
# test costs milliseconds.
class _FakeRequest:
    def __init__(self, md, saved):
        self._md, self._saved = md, saved

    def get_metadata(self):
        return self._md

    def make_buffer(self, name):
        # The real one returns np.array(..., dtype=np.uint8): a COPY, which is what lets
        # capture.py release the request before encoding. A bytes stand-in is enough here.
        self._saved.append(name)
        return b"RAWFRAME"

    def release(self):
        pass


class _FakeCam:
    """Enough of Picamera2 to run main(): configure, start, hand out frames, stop."""

    def __init__(self, exposures):
        self.exposures, self.i, self.saved, self.controls = exposures, 0, [], {}
        self.options, self.sensor_resolution = {}, (4608, 2592)
        self.camera_controls = {"FrameDurationLimits": (69669, 220535845)}

    def create_still_configuration(self, main=None, **kw):
        # Recorded because WHERE the frame duration is pinned is the thing under test: in
        # the configuration it reaches the IPA, after start() it reaches only the sensor.
        self.config_controls = kw.get("controls") or {}
        return {"main": main or {}}

    def configure(self, cfg):
        pass

    def start(self):
        pass

    def set_controls(self, c):
        self.controls.update(c)
        if "FrameDurationLimits" in c:
            self.camera_controls["FrameDurationLimits"] = c["FrameDurationLimits"]

    def capture_request(self):
        if self.i >= len(self.exposures):
            capture._stop = True
            return _FakeRequest({"SensorTimestamp": self.exposures[-1]}, self.saved)
        ts = self.exposures[self.i]
        self.i += 1
        return _FakeRequest({"SensorTimestamp": ts, "ExposureTime": 4995,
                             "FrameDuration": 199990}, self.saved)

    def capture_metadata(self):
        return {"SyncReady": True, "SyncTimer": 0}

    def camera_configuration(self):
        return {"main": {"format": "BGR888", "size": (4608, 2592)}}

    @property
    def helpers(self):
        cam = self

        class _H:
            def make_image(self, buffer, config):
                return ("image", bytes(buffer))

            def save(self, img, metadata, file_output, format=None):
                file_output.write(b"\xff\xd8\xff" + img[1])

        return _H()

    def stop(self):
        pass

    def close(self):
        pass


_td2 = tempfile.mkdtemp(prefix="capture-smoke-")
# A five-second grid at 200 ms, phase deliberately offset from the second so the rule has
# to choose rather than accept everything: 25 frames in, 5 kept.
_BOOT = 1_000_000_000_000
_exposures = [_BOOT + 37_000_000 + n * 200_000_000 for n in range(25)]
# Boot and wall coincide, so an exposure's wall time is its boot stamp -- which makes the
# second boundaries fall where the arithmetic above says they do.
capture._clock_triple = lambda: {"boot_ns": 0, "mono_ns": 0, "wall_ns": 0}
_cam = _FakeCam(_exposures)
# Patched on the MODULE, not on the fake package: capture.py did `from picamera2 import
# Picamera2` at import time, so it holds its own reference and a package-level swap is
# invisible to it. Getting this wrong is how the first run of this test "passed" nothing.
class _FakeFactory:
    """Stands in for the Picamera2 CLASS: main() both calls the constructor and asks the
    class itself for global_camera_info(), so a bare lambda is not enough."""

    def __new__(cls, *a, **k):
        return _cam

    @staticmethod
    def global_camera_info():
        return [CAM]


capture.Picamera2 = _FakeFactory

_env = {"CAMPOD_CAPTURE_DIR": _td2, "CAMPOD_WARMUP_FRAMES": "0",
        "CAMPOD_STATUS_FILE": str(Path(_td2) / "status"), "CAMPOD_CAPTURE_FLAG": "",
        "CAMPOD_SYNC_LEADER": "", "CAMPOD_FRAME_DURATION_US": "200000"}
_old_env = {k: os.environ.get(k) for k in _env}
os.environ.update(_env)
capture._stop = False
_buf = io.StringIO()
try:
    with redirect_stdout(_buf):
        _rc = capture.main()
except BaseException as _exc:  # noqa: BLE001
    _rc = f"raised {_exc!r}"
finally:
    for _k, _v in _old_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
_out = _buf.getvalue()

# None is main()'s normal return -- `sys.exit(main())` makes that exit 0. The thing under
# test is that it does not RAISE, which is what shipped broken.
# THE PIN HAS TO BE IN THE CONFIGURATION. Set only after start(), it reached the sensor
# but not the IPA's cached min/max -- which is what the sync algorithm divides by -- and the
# server broadcast frame durations of 52.7 s and 38.0 s while the sidecars looked correct.
check("the frame duration is pinned in the configuration, not just after start",
      _cam.config_controls.get("FrameDurationLimits") == (200000, 200000),
      repr(_cam.config_controls))
check("main() starts and runs without raising", _rc in (0, None), f"rc={_rc}  {_out[-300:]}")
check("it logs the selection rule it is running", "the first of each" in _out, _out[:200])
# Five seconds of grid in, one frame per second out. The count is the assertion: the old
# tick-driven loop would have written whatever its own clock asked for.
check("one frame kept per second of exposures", len(_cam.saved) == 5,
      f"{len(_cam.saved)} saved from {len(_exposures)} frames")
_cards = sorted(Path(_td2).rglob("*.json"))
_cards = [c for c in _cards if "manifests" not in str(c)]
if _cards:
    _sc = json.loads(_cards[0].read_text())
    check("the sidecar carries the fields analysis joins on",
          {"exposure_wall_ns", "second_offset_us", "clk", "sensor_timestamp_ns"} <= set(_sc),
          ",".join(sorted(_sc)))
    check("every kept frame is within half a grid period of its second",
          all(abs(json.loads(c.read_text())["second_offset_us"]) <= 100_000 for c in _cards))
else:
    check("the sidecar carries the fields analysis joins on", False, "no sidecars written")
shutil.rmtree(_td2, ignore_errors=True)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_capture_wait: all checks passed")
