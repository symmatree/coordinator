#!/usr/bin/env python3
"""Rekon campod still-capture loop.

Captures JPEG stills from the Camera Module 3 (IMX708) at a fixed cadence and
writes each frame plus a JSON metadata sidecar to local storage. Frame sync
(one pacesetter/server, the rest clients) is how the pods' frames line up and is
not optional -- CAMPOD_SYNC_LEADER is required. See docs/campod.md.

A pod reports `camera.ready` only when it is both warm and synced, because that
is the question anyone asks of it: will arming now produce usable frames.

Config via environment:
  CAMPOD_NODE_NAME       node label in filenames/metadata (default: hostname)
  CAMPOD_CAPTURE_DIR     output dir (default: /captures)
  CAMPOD_CAPTURE_WIDTH   frame width  (default: 0 = sensor full resolution)
  CAMPOD_CAPTURE_HEIGHT  frame height (default: 0 = sensor full resolution)
  CAMPOD_JPEG_QUALITY    1-100 (default: 90)
  CAMPOD_STILL_MAX_EXPOSURE_US
                      cap the shutter (default: 1000; 0 = uncapped AE)
  CAMPOD_STILL_FOCUS     infinity | <dioptres> (default: infinity)
  CAMPOD_FRAME_DURATION_US  fixed frame duration in microseconds; the camera-sync
                      algorithm requires a FIXED rate and warns every frame without one.
                      Clamped to what the sensor mode allows, and the achieved value is
                      logged (default: 200000, i.e. 5 fps)
  CAMPOD_SYNC_LEADER     REQUIRED. hostname of the pod that paces the others; this pod
                      becomes the sync server if it matches its own name and a client if
                      it does not. There is no unsynced mode: a single pod names itself
                      and runs as a server with no followers
  CAMPOD_WARMUP_FRAMES   frames to capture at startup before going warm (default: 10)
  CAMPOD_CAPTURE_FLAG    path whose contents gate capture; empty disables gating
                      and captures continuously (default: /tmp/campod_capture)
  CAMPOD_STATUS_FILE     path this writes its own state to, for pod-link to publish;
                      empty disables (default: /tmp/campod_camera_status)
"""

import collections
import datetime as dt
import json
import os
import re
import signal
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import simplejpeg
from picamera2 import MappedArray, Picamera2

_stop = False


def _request_stop(signum, _frame):
    global _stop
    _stop = True
    # Not "after current frame": this also fires while waiting for a camera,
    # when there is no frame in flight and never has been.
    print(f"capture: received signal {signum}, stopping", flush=True)


def _env_int(name, default):
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name, default):
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _apply_exposure_cap(picam2, max_us):
    """Cap the shutter, letting auto-exposure trade to gain instead.

    Motion blur was the killer on the OAK-D stills: auto-exposure ran to ~30 ms
    (T9/E18 in analysis/vio-quality-experiments.md), which is why the OAK-D side
    ships OAK_STILL_MAX_EXPOSURE_US=5000. The IMX708 makes it worse, not better --
    full-res readout is 68 ms (2592 rows x 26.29 us line time, from the imx708
    driver's line_length_pix=0x3d20 / pixel_rate=595200000), so a long exposure
    stacks blur on top of a long rolling-shutter window.

    libcamera has no "maximum AE exposure" control, and FrameDurationLimits cannot
    do it either: at 4608x2592 the minimum frame duration is already ~70 ms. The
    control that does work is the exposure/gain mode split added in libcamera 0.4:
    ExposureTimeMode=Manual pins the shutter while AnalogueGainMode=Auto leaves the
    AEGC free to make up the light in gain. That is exactly "cap the shutter, trade
    to ISO".

    Hardware-unverified (no Zero + CM3 has run this yet), so it is best-effort: if
    the split is unsupported the frames still capture, with a warning, and the
    per-frame sidecar records what the sensor actually did.
    """
    if max_us <= 0:
        return None
    try:
        from libcamera import controls  # noqa: PLC0415  (version-dependent)

        picam2.set_controls(
            {
                "ExposureTimeMode": controls.ExposureTimeModeEnum.Manual,
                "ExposureTime": max_us,
                "AnalogueGainMode": controls.AnalogueGainModeEnum.Auto,
            }
        )
        print(f"capture: exposure pinned to {max_us} us, gain left on AEGC", flush=True)
        return max_us
    except Exception as exc:  # noqa: BLE001  the cap is best-effort, capture is not
        print(
            f"capture: WARNING could not pin exposure to {max_us} us: {exc}. "
            "Continuing with full auto-exposure -- expect motion blur in flight; "
            "check the sidecar exposure_us against this cap.",
            flush=True,
        )
        return None


def _apply_focus(picam2, focus):
    """Fix the lens at a given position, if the module has a lens that moves.

    Returns the dioptres applied, or the string "mechanically-fixed" for a module with
    no focus mechanism at all. Still fatal if a MOVABLE lens cannot be pinned.

    A voice-coil module focuses by holding a suspended lens group against springs with
    open-loop current -- no position feedback -- so AF can hunt mid-flight and the group
    is free to be displaced by vibration (docs/campod.md: prefer a locked lens, and do
    not let AF move right before a shot on a vibrating airframe).

    **A module with a fixed lens has neither problem, and must not be treated as a
    failure.** M12-mount and other fixed-lens boards advertise no `AfMode` and no
    `LensPosition`, so setting them raises -- which killed capture at startup and
    presented as a dead camera rather than as an unsupported control. The reason this
    was fatal is that "a run whose lens position is unknown is not worth flying", and on
    a fixed lens the position is known permanently and by construction. So the test is
    whether the control EXISTS, not whether it can be set.

    Units are NOT the OAK-D's 0-255 lens scale: libcamera LensPosition is in DIOPTRES,
    the reciprocal of focus distance in metres. 0.0 is infinity, 0.5 is 2 m, 2.0 is
    0.5 m. Copying OAK_STILL_FOCUS=125 here would ask for 8 mm.
    """
    from libcamera import controls  # noqa: PLC0415  (version-dependent)

    if "LensPosition" not in (picam2.camera_controls or {}):
        print(
            "capture: no LensPosition control -- this module's lens is mechanically "
            "fixed, so there is nothing to pin and nothing for vibration to displace. "
            f"CAMPOD_STILL_FOCUS={focus} is ignored.",
            flush=True,
        )
        return "mechanically-fixed"

    dioptres = 0.0 if focus == "infinity" else float(focus)
    picam2.set_controls(
        {"AfMode": controls.AfModeEnum.Manual, "LensPosition": dioptres}
    )
    distance = "infinity" if dioptres <= 0 else f"{1.0 / dioptres:.2f} m"
    print(f"capture: lens fixed at {dioptres} dioptres ({distance}), AF off", flush=True)
    return dioptres


def _clock_triple():
    """One boot/mono/wall sample, the three read back to back, microseconds apart.

    Converts a frame's CLOCK_BOOTTIME exposure stamp into wall time, which is what the
    selection rule below decides on. The OFFSET is the wanted part, not the sample instant,
    so reading it after the frame costs nothing in accuracy: chrony slews at microseconds
    per second, so an offset sampled a second late is still sub-microsecond here.

    Recorded ALONGSIDE FrameWallClock rather than instead of it, so the two cross-check:
    FrameWallClock is libcamera's own realtime stamp for the frame, and this is the
    device's own conversion. Disagreement between them is itself a finding, and if the
    libcamera on the device has no FrameWallClock this is what is left.

    Same three keys as the accel reader's `t:"clk"` records (accel/main.go), because
    analysis should not need two conventions for one quantity.
    """
    return {
        "boot_ns": time.clock_gettime_ns(time.CLOCK_BOOTTIME),
        "mono_ns": time.monotonic_ns(),
        "wall_ns": time.time_ns(),
    }


def _boundary_offset_ns(wall_exposure_ns, period_ns=1_000_000_000):
    """Which second this exposure fell in, and how far into it.

    Returns (second_index, offset_ns) with the offset always >= 0 and < period: the second
    is taken by FLOOR, so every exposure belongs to the second it actually happened in.

    A FIELD, NOT A DECISION, since 2026-10-19. `second_offset_us` goes in the ledger and
    the sidecar because it says where in the second a frame sat, which is worth having when
    joining against anything else; nothing branches on it.

    IT USED TO BE THE SELECTION RULE -- keep the first frame of each second, skip the rest
    -- and that rule's documented risk is what retired it: the decision edge sat exactly
    where a frame can land, so a frame falling within the pods' grid-alignment error of a
    second boundary was binned as before by one pod and after by the other, and they kept
    frames a whole period apart. Measured as +/-500 ms outliers in three runs at a 2 Hz
    pin, every one of them traceable to a single dropped or boundary-straddling frame.

    What makes dropping the rule safe is the second camera buffer: libcamera runs the IPA
    once per queued request, so with one buffer we saw every other sensor frame and needed
    a rule to thin what remained. With two we see every frame, so the pin IS the rate --
    and a lost frame now costs a missing frame rather than a mispaired one.
    """
    second = wall_exposure_ns // period_ns
    return second, wall_exposure_ns - second * period_ns


def _clock_triple():
    """One boot/mono/wall sample, the three read back to back, microseconds apart.

    Converts a frame's CLOCK_BOOTTIME exposure stamp into wall time, which is what the
    selection rule below decides on. The OFFSET is the wanted part, not the sample instant,
    so reading it after the frame costs nothing in accuracy: chrony slews at microseconds
    per second, so an offset sampled a second late is still sub-microsecond here.

    Recorded ALONGSIDE FrameWallClock rather than instead of it, so the two cross-check:
    FrameWallClock is libcamera's own realtime stamp for the frame, and this is the
    device's own conversion. Disagreement between them is itself a finding, and if the
    libcamera on the device has no FrameWallClock this is what is left.

    Same three keys as the accel reader's `t:"clk"` records (accel/main.go), because
    analysis should not need two conventions for one quantity.
    """
    return {
        "boot_ns": time.clock_gettime_ns(time.CLOCK_BOOTTIME),
        "mono_ns": time.monotonic_ns(),
        "wall_ns": time.time_ns(),
    }


def _boundary_offset_ns(wall_exposure_ns, period_ns=1_000_000_000):
    """Which second this exposure fell in, and how far into it.

    Returns (second_index, offset_ns) with the offset always >= 0 and < period: the second
    is taken by FLOOR, so every exposure belongs to the second it actually happened in.

    THE SELECTION RULE: keep the first frame of each second, and nothing else.

    What it replaced, and why that failed. The rule used to require the frame to be within
    half a grid period of the second -- the "nearest" frame. That is correct only if every
    frame is seen, and the copy-out costs 123-132 ms (measured), so at a 100 ms grid it is
    not. Our visible subset then sat at a fixed offset, and when that offset fell outside
    the window EVERY second was skipped until the grid drifted back inside. The sensor
    achieves 99982 us against a 100000 us request, which is 180 us/s of drift, so crossing
    a 50 ms window takes ~278 s: measured as holes of 44 s, 84 s and 230 s in one run, with
    warmup alone taking 404 s because warmup frames go through the same rule.

    FLOOR, NOT ROUND, and that distinction is the whole rule. Assigning each frame to the
    NEAREST second means a frame in the back half of a second is "approaching" the next
    one and gets skipped -- so at 1 Hz with the grid phase past the half-second mark,
    nothing is ever kept at all, which is the hole failure again in a different form.
    Flooring gives every frame the second it actually happened in, so the first frame of
    each second is the keeper: one per second at any delivery rate of 1 Hz or faster, no
    window, and the offset says how far into the second it sat.

    THE RISK IT CARRIES, and how to see it: the decision edge now sits exactly where a
    frame can land. If a frame falls within the two pods' grid-alignment error of a second,
    one pod can bin it as before and the other as after, and they pick frames a whole
    period apart. `second_offset_us` is in every sidecar, so that shows up as a
    one-period outlier in the pairing rather than as a quiet error. The fix if it bites is
    to hold a candidate copy and take the genuinely nearest frame, which costs a second
    35.8 MB buffer; not done here, because one buffer was the constraint.
    """
    second = wall_exposure_ns // period_ns
    return second, wall_exposure_ns - second * period_ns


class _FrameLedger:
    """One line per frame RETRIEVED, with its times and what was decided about it.

    WHY. The selection rule is the thing being tuned, and until now the only record was
    the frames it KEPT -- so a run could not distinguish "the pod saw ten frames a second
    and chose this one" from "the pod saw one frame a second and had no choice". Trial 10
    ended on exactly that ambiguity: sw kept its frame at a rock-steady 204 ms into each
    second while se's wandered from 540 to 868 ms, and nothing in the data said whether se
    was dropping frames or never getting them.

    Written on the capture thread with a buffered writer and never fsynced, the same
    discipline as the accel reader (accel/writer.go): at a few hundred bytes a frame this
    is ~2 KB/s, and a buffered append costs microseconds. A power cut loses the tail,
    which is the right trade for a diagnostic.
    """

    def __init__(self, path):
        self._fh = open(path, "a", buffering=1 << 16)
        self.wrote = 0

    def record(self, rec):
        try:
            self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            self.wrote += 1
        except OSError as exc:
            print(f"capture: frame ledger write failed: {exc}", flush=True)

    def close(self):
        try:
            self._fh.flush()
            self._fh.close()
        except OSError:
            pass


# libcamera's own name for our pixel order. picamera2 maps the same way in
# CompletedRequest.save: the format is called BGR888 and simplejpeg is told "RGB".
_SIMPLEJPEG_COLORSPACE = {"BGR888": "RGB", "RGB888": "BGR",
                          "XBGR8888": "RGBX", "XRGB8888": "BGRX"}


def _copy_out(request, dst):
    """Copy the main stream into `dst`, allocating it the first time, and return it.

    ONE BUFFER, REUSED. `request.make_buffer()` allocates a fresh array per call, and at
    35.8 MB glibc serves that with mmap and returns it to the kernel on free -- so every
    kept frame first-touched ~8738 new pages, measured as 8808 minor faults/s while armed
    against 55/s while paused, and the whole footprint arrived at arm time rather than
    during warmup.

    Reuse is safe because exactly one raw frame is in flight by construction: _Serializer
    holds its slot until the encode COMPLETES, and `put` blocks while it is held, so the
    encoder is never reading this buffer while the capture thread writes it. Pooling would
    be needed to make the raw hand-off deeper than one.

    MappedArray is _MappedBuffer plus a reshape that never copies, so `m.array` is the
    camera's own memory shaped (h, w, 3) with the stride accounted for; np.copyto is the
    same single memcpy make_buffer was doing. Injected as a module attribute because it
    needs a mapped request, which a fake camera cannot provide.
    """
    with MappedArray(request, "main", write=False) as m:
        if dst is None or dst.shape != m.array.shape:
            dst = np.empty(m.array.shape, dtype=np.uint8)
        np.copyto(dst, m.array)
    return dst


class _Serializer:
    """Encodes and writes one kept frame at a time, on its own thread.

    WHY THE WRITE LEAVES THE SENSOR PATH. Measured 2026-10-16: 103 of 105 saves per pod
    took 412-422 ms, and two took 2399 ms and 708 ms. The long one matters more than its
    rarity: the accel reader, a separate container, logged its only FIFO overrun of the
    run inside the same window, so the card stalls everything on the box at once. Holding
    the capture thread across that costs a frame AND whatever the accel loses.

    WHY THE ENCODE MOVED HERE TOO. The camera has ONE buffer (buffer_count=1, and the CMA
    pool cannot hold another -- docs/os-and-driver-handoff.md measures a third set at
    ~145 MiB against a 128 MB pool). So whatever the capture thread holds the request for,
    the sensor has nowhere to put the next frame and that frame is lost. A ~395 ms encode
    on the capture thread therefore halves the usable rate at ANY frame rate: measured at
    5 fps as a 400 ms delivery lattice, and at 1 Hz as a dead-flat 2000 ms cadence.

    So the capture thread copies the frame out and releases: make_buffer() is
    np.array(..., dtype=np.uint8), a real copy, about 36 ms for 35.8 MB. Encode and write
    happen here, off the sensor path, which also keeps the card's occasional multi-second
    stalls off it -- one such stall took 2399 ms and cost the accel reader, a separate
    container, its only FIFO overrun of that run.

    TWO STAGES, BECAUSE THE ENCODE IS REPEATABLE AND THE WRITE IS NOT. Of 105 saves
    measured, 103 took 412-422 ms -- ten milliseconds of spread across a hundred frames --
    and the encode alone measures 389-422 ms, so a 1.7 MB write is normally ~10-20 ms and
    the 2399 ms and 708 ms outliers are the card. With encode and write on one thread, that
    stall held the raw slot, so `put` blocked, so the capture loop stopped retrieving
    entirely: with buffer_count=1 the driver dropped those frames and the ledger has no
    record they ever existed.

    So the encode thread hands 1.7 MB of JPEG to a write thread and takes the next raw
    frame. `put` then waits only on the ~395 ms encode, which is repeatable and well inside
    a 1000 ms keep cadence, and a card stall is absorbed by compressed bytes in RAM.

    STRICTLY ONE RAW FRAME, still: the slot is held until the encode is done, so exactly
    one 35.8 MB copy exists at a time.

    BACK-PRESSURE ARRIVES ONE FRAME AFTER THE CAP. `put` waits on the raw slot, and the
    slot frees when the encoder picks a frame up -- so when the queue is full the encoder
    blocks holding one frame, the next `put` still succeeds, and only the one after that
    waits. The queue is bounded at the cap regardless; the raw copies in flight are the
    one in the slot and the one the encoder holds, as before.

    THE WRITE QUEUE IS CAPPED, because the board has 417 MiB. Compressed frames are 21:1
    cheaper to hold than raw ones -- 1.7 MB against 35.8 MB -- which is what makes queueing
    them affordable at all, not free. Four of them is ~7 MB and covers a stall of about
    four seconds at one keep per second. Past that the encoder blocks and the wait is
    counted: a shortfall should be a number, not frames that quietly never happened.
    """

    # ~7 MB of compressed frames. See the cap note above.
    WRITE_QUEUE_FRAMES = 4

    def __init__(self, encode, write=None):
        # encode(buffer) -> jpeg bytes, and write(path, bytes). Passed in rather than
        # reaching for picamera2 or the filesystem here, so both stages stay testable
        # without a camera and without a card.
        self._encode = encode
        self._write = write or (lambda path, data: path.write_bytes(data))
        self._slot = None
        self._busy = False
        self._queue = collections.deque()
        self._lock = threading.Lock()
        self._ready = threading.Condition(self._lock)
        self._done = threading.Condition(self._lock)
        self._queued = threading.Condition(self._lock)
        self._drained = threading.Condition(self._lock)
        self._stop = False
        self.errors = 0
        # Waited to hand a RAW frame to the encoder: the encode could not keep up.
        self.waits = 0
        # Waited to hand COMPRESSED bytes to the writer: the card could not keep up. Two
        # different problems, which one counter could not tell apart.
        self.write_waits = 0
        self.deepest_queue = 0
        self._encoder = threading.Thread(target=self._encode_loop, name="encoder",
                                         daemon=True)
        self._writer = threading.Thread(target=self._write_loop, name="writer", daemon=True)
        self._encoder.start()
        self._writer.start()

    def put(self, buffer, jpeg_path, sidecar_path, sidecar):
        """Hand over one kept frame. Blocks while the previous one is still encoding."""
        with self._lock:
            if self._slot is not None or self._busy:
                self.waits += 1
                while (self._slot is not None or self._busy) and not self._stop:
                    self._done.wait(30)
            self._slot = (buffer, jpeg_path, sidecar_path, sidecar)
            self._ready.notify()

    def _encode_loop(self):
        while True:
            with self._lock:
                while self._slot is None and not self._stop:
                    self._ready.wait(1)
                if self._slot is None and self._stop:
                    return
                job, self._slot = self._slot, None
                self._busy = True
            buffer, jpeg_path, sidecar_path, sidecar = job
            try:
                data = self._encode(buffer)
            except Exception as exc:  # noqa: BLE001
                self.errors += 1
                print(f"capture: encoding {jpeg_path.name} failed: {exc}", flush=True)
                data = None
            with self._lock:
                if data is not None:
                    if len(self._queue) >= self.WRITE_QUEUE_FRAMES:
                        self.write_waits += 1
                        while (len(self._queue) >= self.WRITE_QUEUE_FRAMES
                               and not self._stop):
                            self._drained.wait(30)
                    self._queue.append((jpeg_path, data, sidecar_path, sidecar))
                    self.deepest_queue = max(self.deepest_queue, len(self._queue))
                    self._queued.notify()
                # The raw copy is released here, whatever happened to it: holding it
                # through the write is the thing this split exists to stop.
                self._busy = False
                self._done.notify()

    def _write_loop(self):
        while True:
            with self._lock:
                while not self._queue and not self._stop:
                    self._queued.wait(1)
                if not self._queue:
                    if self._stop:
                        return
                    continue
                jpeg_path, data, sidecar_path, sidecar = self._queue.popleft()
                self._drained.notify()
            try:
                self._write(jpeg_path, data)
                # The sidecar goes LAST: an image without one is unusable for pairing, so
                # the sidecar's existence is what marks a frame complete.
                self._write(sidecar_path, sidecar)
            except Exception as exc:  # noqa: BLE001
                self.errors += 1
                print(f"capture: writing {jpeg_path.name} failed: {exc}", flush=True)

    def close(self, timeout=30):
        """Finish the frame in hand AND everything queued, so none is lost."""
        with self._lock:
            while (self._slot is not None or self._busy) and not self._stop:
                self._done.wait(timeout)
            while self._queue and not self._stop:
                self._drained.wait(timeout)
            self._stop = True
            self._ready.notify()
            self._queued.notify()
        self._encoder.join(timeout)
        self._writer.join(timeout)


def _sync_mode_for(leader, node):
    """server on the leader, client on everyone else. A leader is required.

    ONE VALUE, IDENTICAL ON EVERY POD, because that is the only kind of configuration the
    deployment can currently express: stacks/campod/ is one file copied to every pod and a
    converge passes only device_role, so there is no per-pod env to put "server" in. Each
    pod compares the leader's name against its own, which it already reads from the host's
    /etc/hostname for its capture path (#272).

    Naming a leader rather than electing one is deliberate for now and is the smaller half
    of the problem -- coordinator#501 covers doing it over the bus, where the pods already
    all see one value.

    An unknown leader name leaves every pod a client and nothing paces them, which is why
    the caller logs what it resolved to rather than only that sync is on.

    NO UNSYNCED MODE. A campod is a member of the pair; an unsynced one produces frames
    that cannot be used with the other pod's, which is the whole product. So an unset
    leader is a configuration error rather than a mode -- see main(). Electing a leader
    over the bus instead of naming one is coordinator#501, and is a refinement of this,
    not a reason to keep a path for having none.
    """
    if not leader:
        raise ValueError("no sync leader named")
    return "server" if leader == node else "client"


def _pin_frame_duration(picam2, want_us):
    """Report what the pinned frame duration actually came out as, and re-assert it.

    THE PIN ITSELF IS IN THE CONFIGURATION -- see create_still_configuration in main().
    Setting it only here, after start(), reached the sensor but not the IPA's cached
    min/max, which is what the sync algorithm reads. This remains because the mode clamps
    the request and this is where we find out what we got.

    WHY SYNC NEEDS IT. From libcamera's `ipa_base.cpp`, in the block that runs the sync
    algorithm every frame:

        if (minFrameDuration_ != maxFrameDuration_)
            LOG(IPARPI, Error) << "Sync algorithm enabled with variable framerate. "

    Measured on campod-se with nothing pinning it: 69669 us to 220535845 us -- 14.4 fps to
    220 seconds. The algorithm still RUNS in that state (it takes frameDurationOffset and
    publishes SyncReady regardless), so this is not a refusal; its correction is a
    single-frame nudge to the duration, which is not meaningful when the duration floats.

    CLAMPED BY THE MODE, so this returns what was actually achieved rather than what was
    asked. `applyFrameDurations` clamps into [mode_.minFrameDuration, mode_.maxFrameDuration]
    and publishes the validated pair back as metadata, so a value outside the mode's range
    is silently something else. Reading the range from the camera and saying what we got
    beats a magic number nobody can check.

    5 fps by default rather than the mode's floor: sync needs the duration FIXED, not fast,
    and a Zero 2 W running its sensor flat out for a 1 Hz stills job is heat we do not need.
    """
    lo_hi = picam2.camera_controls.get("FrameDurationLimits")
    if lo_hi is None:
        print("capture: WARNING camera reports no FrameDurationLimits, so the frame "
              "duration cannot be pinned and sync will run against a variable rate",
              flush=True)
        return None
    lo, hi = lo_hi[0], lo_hi[1]
    got = max(lo, min(hi, want_us))
    try:
        picam2.set_controls({"FrameDurationLimits": (got, got)})
    except Exception as exc:  # noqa: BLE001  one control must not stop the camera
        print(f"capture: WARNING could not pin the frame duration: {exc}", flush=True)
        return None
    note = "" if got == want_us else f" (asked {want_us}, mode allows {lo}..{hi})"
    print(f"capture: frame duration pinned to {got} us "
          f"= {1e6 / got:.2f} fps{note}", flush=True)
    return got


def _apply_sync(picam2, mode):
    """libcamera camera-sync configuration. Required, not best-effort.

    The CM3 has no XVS hardware trigger, so multi-campod alignment uses libcamera's
    software sync (one server/pacesetter, the rest clients).

    THERE IS NO UNSYNCED MODE, and this used to warn and continue because the picamera2
    control surface was unverified on hardware. It is verified now -- every run since
    2026-10-18 pairs 248-250 frames within 50 us by it -- and a pod that silently
    continued unsynced would produce frames that cannot be used with the other pod's,
    which is the product. So a control we cannot set is fatal here, at start, rather
    than a pod that never reaches ready and does not say why.

    The trivial case is a server with no followers, which needs nothing special: it
    paces itself and reports SyncReady like any other server.
    """
    from libcamera import controls  # noqa: PLC0415  (version-dependent)

    sync_enum = {
        "server": controls.rpi.SyncModeEnum.Server,
        "client": controls.rpi.SyncModeEnum.Client,
    }[mode]
    picam2.set_controls({"SyncMode": sync_enum})
    print(f"capture: sync mode set to {mode}", flush=True)
    return mode


# A campod with no camera waits for one; it does not exit. Exiting means docker's
# `restart: unless-stopped` brings us straight back, and every restart re-pays the
# picamera2 import only to rediscover the same absent camera. Measured on campod-se
# with no camera attached: 60 restarts in two hours -- one every ~2 min -- at a
# sustained load average of ~8. The Zero 2 W is quad-core, so that is roughly 2x
# oversubscribed rather than 8x, but it is 2x oversubscribed doing nothing.
#
# Worse, the entrypoint runs the accelerometer reader as a child and `exec`s us in
# the foreground, so the container's life is our life. A missing camera was
# therefore killing a working pair of ADXL345s every couple of minutes, chopping
# their record into fragments (~70 s, on the one session captured off that node).
# The entrypoint states the principle for the other half -- "a missing sensor must
# never cost us the frames" -- and this is the converse it did not cover.
#
# Waiting also makes the camera hot-pluggable, which the accelerometer reader
# already is: each probe is a fresh look, so a ribbon reseated on the bench is
# picked up without a restart. The 30 s cadence is the accelerometer retry's, for
# no stronger reason than that one operator-visible retry period beats two.
CAMERA_PROBE_INTERVAL_S = 30.0
# Log cadence while waiting, in probes. The first miss prints immediately; after
# that a heartbeat every 10th probe (5 min) keeps the journal honest about a pod
# that is up but blind, without spamming it.
CAMERA_WAIT_HEARTBEAT_PROBES = 10


def _wait_for_camera(heartbeat=None):
    """Return libcamera's camera list, blocking until one appears.

    Returns None if a stop signal arrives while waiting.

    heartbeat is called once per probe. A pod with a half-seated ribbon is the
    field fault these have, and it has to keep re-stamping its status while it
    waits: a document that stops advancing reads as a dead process, which is a
    different fault needing a different response.

    Probing BEFORE constructing Picamera2() is load-bearing: Picamera2() on a node
    with no camera raises picamera2's own `IndexError: list index out of range`
    from inside global_camera_info() -- it indexes an empty list. Observed on
    campod-sw during bring-up, where libcamera itself initialised fine (v0.5.2)
    and the only thing in the log was that traceback, which says nothing about
    cameras. The accelerometer reader beside us already reports what it probed and
    what it expected (DEVID 0x00, expected 0xE5) per chip select; this is the
    camera half of the same courtesy. On an arm-mounted pod the likely fault is a
    badly seated ribbon, not an absent module, and an operator needs to tell those
    apart from the log alone.
    """
    cameras = Picamera2.global_camera_info()
    if cameras:
        return cameras

    print(
        "capture: libcamera reports NO cameras. Check the ribbon is seated "
        "(both ends, contacts toward the board) and that this is a campod "
        "image -- camera_auto_detect=1 comes from the vendor config. Waiting "
        f"for a camera, re-probing every {CAMERA_PROBE_INTERVAL_S:.0f}s.",
        flush=True,
    )

    probes = 0
    waited_start = time.monotonic()
    while not _stop:
        # Sleep in slices so SIGTERM stops us promptly rather than up to a full
        # probe interval later -- docker stop's grace period is 10 s.
        deadline = time.monotonic() + CAMERA_PROBE_INTERVAL_S
        while not _stop and time.monotonic() < deadline:
            time.sleep(0.25)
        if _stop:
            break

        if heartbeat is not None:
            heartbeat()
        cameras = Picamera2.global_camera_info()
        probes += 1
        if cameras:
            print(
                "capture: camera appeared after "
                f"{time.monotonic() - waited_start:.0f}s of waiting",
                flush=True,
            )
            return cameras
        if probes % CAMERA_WAIT_HEARTBEAT_PROBES == 0:
            print(
                "capture: still no camera after "
                f"{(time.monotonic() - waited_start) / 60:.0f} min",
                flush=True,
            )

    print("capture: stop requested while waiting for a camera", flush=True)
    return None


# Where the stack file mounts the host's own /etc/hostname.
HOST_HOSTNAME_PATH = Path("/etc/host-hostname")


def _is_ready(warm, sync_ready):
    """Will arming now produce usable frames: warm AND synced.

    THE ONE PLACE THIS IS DECIDED. The question has three readers -- the bench cycle's
    arm gate, the coordinator's front panel, and the fleet-control table -- and the panel
    is the one that matters, because in the field it is the only one: no network, no
    table, someone standing in front of a 128 px screen deciding whether to arm. A reader
    cannot combine booleans, so this pod combines them.

    It was two definitions before #577 and they disagreed. `camera.ready` meant warm
    only; the arm gate separately required `sync_ready`, and the panel did not, so the
    panel showed a ready pod for the ~80 s between warm and synced.

    `sync_ready is True` rather than truthy because the field is absent until the pod has
    evaluated it, and "has not looked yet" must not read as ready.
    """
    return bool(warm) and sync_ready is True


def _capture_wanted(flag_path):
    """Should we be writing frames right now?

    A FILE, READ PER TICK, rather than a signal. The distinction matters: a signal
    cannot say "keep running but stop retrieving", which is the whole state this
    exists to hold -- the camera must stay start()ed, because its buffers are what
    we warmed up for. Measured on two pods, identical to the byte: 99.6 MiB of
    dma_buf against a 128 MiB CMA reservation, so there is 28 MiB of headroom and a
    second set could not be allocated even transiently. Anything that calls
    picam2.stop() pays the whole startup again.

    Same shape as the arm file coordinator-mavlink already writes for vio-tracker
    (#88): an atomic write someone else does, a stat() we do. It costs nothing on a
    box where invoking a binary is measured in seconds, and the writer can be
    anything -- today a person, later whatever carries arm state to the pods (#25).

    Absent means paused. A gate whose failure mode is "capture everything forever"
    is the behaviour we are removing.
    """
    if flag_path is None:
        return True
    try:
        return flag_path.read_text().strip() in ("1", "true", "on", "yes")
    except OSError:
        return False


def _write_status(path, payload):
    """Publish our own state where pod-link can read it. Best-effort, always.

    The mirror of _capture_wanted: that reads a file somebody else writes, this
    writes one somebody else reads. Same medium (tmpfs, so no card writes), same
    atomic temp-and-rename so a single read cannot land on a half-written
    document, and no new dependency in this process -- one resident MQTT client
    per pod is the footprint budget and it is pod-link's.

    NO WALL CLOCK IN HERE. The pod has no RTC, so a wall stamp written before time
    service arrives is wrong by the size of the step that has not happened yet,
    and nothing in the document would say which side of it produced the number.
    monotonic is time since boot and is true at every moment of a session; the
    accel reader's status and the sidecars agree with it.

    Wrapped broadly and deliberately: every failure mode here -- a full tmpfs, a
    path that is not there, a permissions surprise -- is a reason to carry on
    capturing without status, never a reason to stop capturing. The frames are
    the deliverable.
    """
    if path is None:
        return
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except OSError:
        pass


# Baked into the image at build time; a module var so the test can redirect it.
MANIFEST_SOURCES = (
    (Path("/etc/container-image"), None),
    (Path("/etc/fleet-image"), "fleet-image"),
)


def _copy_manifests(session_dir: Path) -> None:
    """Record what produced this session, in <session>/manifests/.

    /etc/container-image is baked in at image build time and carries the commit
    the payload was built from; /etc/fleet-image is baked into the card image.
    Copying both means a capture directory says what wrote it without anyone
    having to ask docker, or correlate against a registry that may have moved on.

    Written at session start. Both containers do this and may race, so each file
    goes to a temp name and is renamed into place.

    A missing manifest is logged, not fatal: losing the capture would be a worse
    outcome than an unattributed one, and the log says which.
    """
    out = session_dir / "manifests"
    out.mkdir(parents=True, exist_ok=True)
    for src, name in MANIFEST_SOURCES:
        try:
            body = src.read_text()
        except OSError as exc:
            print(f"capture: no {src} to record ({exc}); this session is unattributed", flush=True)
            continue
        if name is None:
            m = re.search(r'^FLEET_UNIT="([^"]*)"', body, re.M)
            name = m.group(1) if m else src.name
        # mkstemp, not the pid: the accel reader writes the same manifests from its own
        # container, where both are pid 2, so a pid-named temp was one file.
        fd, tmp = tempfile.mkstemp(dir=out, prefix=f".{name}.")
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.chmod(tmp, 0o644)
        Path(tmp).replace(out / name)


def _boot_id() -> str:
    """The kernel's boot id, which names the session."""
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _node_name():
    """The name of the HOST, not of the container.

    socket.gethostname() in a container is the container ID -- measured on
    campod-se: e2e7f038824a, while the host was campod-se -- and it changes on
    every recreate. So it cannot be the primary: it would scatter one pod's
    captures across a new directory per restart, which is worse than collecting
    them under one wrong name.

    The stack file bind-mounts the host's /etc/hostname read-only. That makes the
    host the single source of truth and is byte-identical on all four pods --
    there is no per-unit value in a shared file to get wrong, which is exactly
    what #272 was: one literal in a file describing four machines.

    gethostname stays as a last resort, and outside a container it is correct.
    """
    override = os.getenv("CAMPOD_NODE_NAME")
    if override:
        return override
    try:
        name = HOST_HOSTNAME_PATH.read_text(encoding="utf-8").strip()
        if name:
            return name
    except OSError:
        pass
    return socket.gethostname()


def main():
    node = _node_name()
    out_dir = Path(os.getenv("CAMPOD_CAPTURE_DIR", "/captures"))
    width = _env_int("CAMPOD_CAPTURE_WIDTH", 0)
    height = _env_int("CAMPOD_CAPTURE_HEIGHT", 0)
    quality = _env_int("CAMPOD_JPEG_QUALITY", 90)
    max_exposure_us = _env_int("CAMPOD_STILL_MAX_EXPOSURE_US", 1000)
    focus = (os.getenv("CAMPOD_STILL_FOCUS") or "infinity").strip().lower()
    # Required: there is no unsynced mode (_sync_mode_for says why). A pod with no leader
    # named cannot know whether it is the pacesetter, and the quadlet has set this on every
    # campod since sync landed -- so an empty one is a broken deployment, which should say
    # so at start rather than produce unusable frames.
    sync_leader = (os.getenv("CAMPOD_SYNC_LEADER") or "").strip()
    if not sync_leader:
        print("capture: CAMPOD_SYNC_LEADER is required -- name the pacesetter pod "
              "(a single pod names itself and runs as the server)", flush=True)
        return 2
    frame_duration_us = _env_int("CAMPOD_FRAME_DURATION_US", 200_000)
    buffer_count = _env_int("CAMPOD_BUFFER_COUNT", 1)
    warmup_frames = _env_int("CAMPOD_WARMUP_FRAMES", 10)
    flag_raw = os.getenv("CAMPOD_CAPTURE_FLAG", "/tmp/campod_capture")
    flag_path = Path(flag_raw) if flag_raw.strip() else None
    status_raw = os.getenv("CAMPOD_STATUS_FILE", "/tmp/campod_camera_status")
    status_path = Path(status_raw) if status_raw.strip() else None


    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    # The session is the boot id: this binary and the accel binary both use it to
    # agree on an output path (#211). No fallback, this is linux functionality.
    session = _boot_id()
    session_dir = out_dir / node / session
    session_dir.mkdir(parents=True, exist_ok=True)
    _copy_manifests(session_dir)

    # One mutable document, stamped and written by publish(). Everything a
    # consumer needs to tell the three states apart that look alike from outside:
    # blind (no camera), warm but holding (warm, not capturing), and capturing.
    #
    # `ready` IS THE ARM ANSWER AND NOTHING ELSE: will this pod's frames be usable if
    # you arm now. It is warm AND synced, computed here, because a reader standing in
    # front of the panel cannot combine two booleans and three readers that each
    # combine them their own way is how they drift apart. `warm` and `sync_ready` stay
    # as the detail for the network case, where there is room for it.
    state = {
        "node": node,
        "session": session,
        "camera": "waiting",
        "phase": "startup",
        "ready": False,
        "ready_at_mono_s": None,
        # The camera is started and its buffers are allocated: warmup is done and the
        # copy-encode-write path has been exercised once. NOT an arm answer on its own
        # -- it was called `ready` until #577 and read as one, roughly 80 s before the
        # pair was actually aligned.
        "warm": False,
        "warm_at_mono_s": None,
        "capturing": False,
        "frames": 0,
        "last_frame_mono_s": None,
        # SyncTimer deliberately is NOT here -- it changes every frame and belongs in
        # the sidecar beside the frame it describes.
        "sync_ready": None,
    }

    def refresh_ready():
        """`ready` from the two facts that make it, and the moment it first held."""
        state["ready"] = _is_ready(state["warm"], state["sync_ready"])
        if state["ready"] and state["ready_at_mono_s"] is None:
            state["ready_at_mono_s"] = round(time.monotonic(), 3)

    def publish():
        refresh_ready()
        state["as_of_mono_s"] = round(time.monotonic(), 3)
        _write_status(status_path, state)

    publish()
    cameras = _wait_for_camera(heartbeat=publish)
    if cameras is None:  # SIGTERM while waiting
        state["camera"] = "stopped-while-waiting"
        publish()
        return 0
    state["camera"] = "present"
    publish()
    print(
        "capture: libcamera sees "
        + ", ".join(
            f"{c.get('Model', '?')} @ {c.get('Id', '?')}" for c in cameras
        ),
        flush=True,
    )

    picam2 = Picamera2()
    size = (width, height) if width and height else picam2.sensor_resolution
    # THE PIN GOES IN THE CONFIGURATION, not after start(). Measured 2026-10-17: with it
    # applied by set_controls() after start, the sensor ran at the requested duration --
    # the sidecars show it -- but the IPA kept the sensor mode's own range, logged
    # "Sync algorithm enabled with variable framerate. 69669.35us 220535845.16us" every
    # run, and the sync server broadcast frameDuration values of 52.7 s and 38.0 s. The
    # client computes framePeriodErrors and its "nearest frame" from that number, so every
    # correction was calculated against a frame period two orders of magnitude out.
    #
    # picamera2's create_still_configuration injects its own
    # {"FrameDurationLimits": (100, 1000000000)} and then applies `| controls`, so a value
    # passed here wins. rpicam-apps does the same thing -- it sets FrameDurationLimits in
    # its control list before starting the camera, never after.
    # BUFFER COUNT IS WHAT THE IPA TICKS ON. libcamera runs the IPA -- and so the sync
    # algorithm -- once per queued application request (`tryRunPipeline` returns early when
    # `requestQueue_` is empty), not once per sensor frame. Measured at a 100 ms period: a
    # retrieval every 2 sensor frames with nothing else running, 3 while an encode and a
    # card write are in flight, 4 after a frame we copied out. The frame-duration offset the
    # sync client writes stands for every frame in that gap, so its corrections moved the
    # error by the gap multiple instead of cancelling it.
    #
    # picamera2 defaults the raw stream to main's size, so `size` sets the sensor mode and
    # both buffer sizes with it.
    config = picam2.create_still_configuration(
        main={"size": size},
        buffer_count=buffer_count,
        controls={"FrameDurationLimits": (frame_duration_us, frame_duration_us)},
    )
    picam2.configure(config)
    picam2.options["quality"] = quality

    # Resolved here, not in config, so the log line names BOTH the leader and what this
    # pod became. "sync is on" without "and I am a client" is the state where every pod is
    # a client, nothing paces them, and the frames are no more aligned than before.
    sync_mode = _sync_mode_for(sync_leader, node)
    print(f"capture: sync leader is {sync_leader!r}; this pod ({node}) is {sync_mode}",
          flush=True)
    sync_active = _apply_sync(picam2, sync_mode)
    picam2.start()

    # After start(): controls are applied to the running camera, and a failed
    # control must not prevent the camera coming up at all.
    exposure_cap = _apply_exposure_cap(picam2, max_exposure_us)
    focus_active = _apply_focus(picam2, focus)
    pinned_us = _pin_frame_duration(picam2, frame_duration_us)

    print(
        f"capture: node={node} dir={session_dir} size={size[0]}x{size[1]} "
        f"every frame at the pinned rate, no selection "
        f"quality={quality} exposure_cap={exposure_cap or 'none'} "
        f"focus={focus_active} sync={sync_active} "
        f"frame_duration={pinned_us or 'unpinned'} buffers={buffer_count}",
        flush=True,
    )

    seq = 0
    # The one raw buffer, allocated by the warmup exercise below and reused for every kept
    # frame after it. See _copy_out.
    scratch = None
    # The encode the worker runs. Bound here because it needs the stream config, and kept
    # out of _Serializer so that class is testable with no camera.
    main_cfg = picam2.camera_configuration()["main"]

    # simplejpeg straight off the buffer, which is what CompletedRequest.save does under
    # FASTER_JPEG. The path it replaces built a PIL image over the frame, encoded into a
    # BytesIO and copied the result out again with getvalue().
    colorspace = _SIMPLEJPEG_COLORSPACE[main_cfg["format"]]

    def _encode_jpeg(buffer):
        return simplejpeg.encode_jpeg(buffer, quality, colorspace, "420")

    serializer = _Serializer(_encode_jpeg)
    ledger = _FrameLedger(session_dir / "frames.jsonl")
    # The second we have already written a frame for, so a second gets one frame even
    # though several frames fall inside the window when the grid is much faster.
    unstamped = 0
    # Warm up, then hold warm. The warmup frames are real and are RETRIEVED, which
    # is what pays the camera's own allocations -- the import is ~16-20 MiB/s of reads
    # for the first minute and a half, and doing that while the operator waits to arm
    # costs flight time he is paying for in battery.
    #
    # They are no longer WRITTEN. Nothing is written while disarmed, so the encode and
    # the card write are first exercised by the first armed frame rather than during
    # warmup, where they used to be. That is a real change in what warmup proves: it
    # settles the sensor and allocates the buffers, and no longer demonstrates the
    # write path end to end.
    phase = "warmup" if warmup_frames > 0 else "capture"
    capturing = True
    warm_logged = False
    # Latched, because SyncReady "will continue to signal the value true for the rest of
    # the session" -- so once seen there is nothing to re-check, and no reason to keep
    # paying a metadata retrieval per frame.
    sync_ready_seen = False
    sync_waits = 0
    # Frames RETRIEVED, against `seq` which counts frames kept. The pair is the thing the
    # selection rule has to be judged on: one per second out of ten seen and one per
    # second out of one seen look identical in the captures and are not the same state.
    frames_seen = 0
    state["phase"] = phase
    publish()
    try:
        while not _stop:
            now = time.monotonic()

            # FRAMES SEEN, NOT FRAMES KEPT. Warmup is the sensor settling and the
            # buffers being allocated, which happens per frame RETRIEVED -- and since
            # every frame is now retrieved whatever the state, that is the count that
            # moves. `seq` counts frames written, and nothing is written while
            # `phase == "warmup"` because the keep rule below reads that as paused, so
            # counting warmup in `seq` was a deadlock: warmup could not end, so `ready`
            # was unreachable on every boot, armed or not. Both pods sat in warmup for
            # 15 minutes on 2026-10-17 and the cycle timed out waiting for warm.
            if phase == "warmup" and frames_seen >= warmup_frames:
                phase = "capture"
                state["phase"] = phase

            if phase == "capture":
                wanted = _capture_wanted(flag_path)
                if not warm_logged:
                    # The device records its own time-to-warm. Reading it afterwards
                    # costs nothing; watching for it costs the thing being measured.
                    warm_at = time.monotonic()
                    print(f"capture: WARM after {frames_seen} warmup frames at "
                          f"{warm_at:.1f}s since start, "
                          f"{'capturing' if wanted else 'paused'}", flush=True)
                    warm_logged = True
                    capturing = wanted
                    # Warm is half of #434's readiness signal, and this process holds
                    # the ground truth for it: the camera is started and its buffers are
                    # allocated. RSS and refault rates are proxies for this line, useful
                    # for validating it once, not for watching. The other half is sync,
                    # below; publish() combines them into `ready`.
                    state["warm"] = True
                    state["warm_at_mono_s"] = round(warm_at, 3)
                    state["capturing"] = capturing
                    publish()
                elif wanted != capturing:
                    capturing = wanted
                    print(f"capture: {'resumed' if capturing else 'paused'} at "
                          f"{time.monotonic():.1f}s ({seq} frames written)", flush=True)
                    state["capturing"] = capturing
                    publish()
            # EVERY FRAME IS RETRIEVED, EXAMINED AND RECORDED, including while paused.
            #
            # The old shape returned to the top of the loop without retrieving while
            # paused, and ran the SyncReady wait BELOW that -- so sync only began
            # negotiating once capture was armed, and spent the start of a window
            # discarding frames. At 2 Hz with libcamera's 100-frame ready_frame default
            # that is ~50 s of a window. It also meant `sync_ready` was null on the bus
            # for any paused pod, so there was no way to know before arming whether the
            # pair would sync at all.
            #
            # So: one retrieval per frame, whatever the state. A metadata read and a
            # release costs no encode and no card write, and it is what lets a paused pod
            # reach sync while idle -- #434's idle-ready state -- and report it.
            request = picam2.capture_request()
            keep = False
            try:
                metadata = request.get_metadata()
                exposure_boot_ns = metadata.get("SensorTimestamp")
                clk = _clock_triple()
                sync_ready_now = metadata.get("SyncReady")

                if not sync_ready_seen and sync_ready_now:
                    sync_ready_seen = True
                    state["sync_ready"] = True
                    # SyncTimer at the crossing is the headline number: at this moment it
                    # "indicates how closely synchronised the client believes it is with
                    # the server". The one frame where that reading means anything.
                    print(f"capture: SYNC READY as {sync_active} after {sync_waits} "
                          f"waiting frames; SyncTimer at the crossing is "
                          f"{metadata.get('SyncTimer')} us", flush=True)
                    publish()
                elif not sync_ready_seen:
                    sync_waits += 1
                    state["sync_ready"] = False
                    if sync_waits == 1 or sync_waits % 50 == 0:
                        print(f"capture: waiting for sync as {sync_active}, "
                              f"{sync_waits} frames so far, SyncTimer "
                              f"{metadata.get('SyncTimer')} us", flush=True)
                        publish()

                if exposure_boot_ns is None:
                    # No timestamp, no basis for choosing, and nothing that could be
                    # paired with the other pod's frame.
                    decision = "no_timestamp"
                    unstamped += 1
                    if unstamped == 1 or unstamped % 50 == 0:
                        print(f"capture: {unstamped} frames with no SensorTimestamp, "
                              "dropped -- cannot place them in time", flush=True)
                    exposure_wall_ns = boundary = offset_ns = None
                else:
                    exposure_wall_ns = exposure_boot_ns + (clk["wall_ns"] - clk["boot_ns"])
                    boundary, offset_ns = _boundary_offset_ns(exposure_wall_ns)
                    # NO SELECTION. The pin is the rate: every frame that arrives
                    # armed and synced is kept. See _boundary_offset_ns for what the
                    # rule was and why choosing per second cost pairs rather than frames.
                    if phase != "capture" or not capturing:
                        decision = "paused"
                    elif not sync_ready_seen:
                        decision = "sync_wait"
                    else:
                        decision = "kept"
                        keep = True

                # THE LEDGER: every frame, its times, and what happened to it. Written
                # before the keep, so a frame appears even if the encode later fails.
                ledger.record({
                    "t": "f",
                    "seen": frames_seen,
                    "decision": decision,
                    "phase": phase,
                    "capturing": bool(capturing),
                    "sensor_timestamp_ns": exposure_boot_ns,
                    "frame_wall_clock_ns": metadata.get("FrameWallClock"),
                    "exposure_wall_ns": exposure_wall_ns,
                    "second_offset_us": None if offset_ns is None else round(offset_ns / 1000),
                    "clk": clk,
                    "sync_ready": sync_ready_now,
                    "sync_timer_us": metadata.get("SyncTimer"),
                    "frame_duration_us": metadata.get("FrameDuration"),
                    "kept_seq": seq if keep else None,
                })
                frames_seen += 1
                state["frames_seen"] = frames_seen

                # WARMUP PAYS FOR THE FIRST ARMED FRAME. One full copy, encode and card
                # write, on the first frame we are handed, with the file removed again --
                # so the allocation, the encoder's own first-touch and the first write
                # into a fresh session directory all happen before anyone can arm.
                # Arming then changes behaviour, not footprint. Measured without it: the
                # process grew ~79 MiB at arm and faulted 8808 pages/s doing it.
                if scratch is None:
                    scratch = _copy_out(request, scratch)
                    probe = session_dir / ".warmup-probe.jpg"
                    probe.write_bytes(_encode_jpeg(scratch))
                    size_kb = probe.stat().st_size // 1024
                    probe.unlink()
                    print(f"capture: warmup exercised the copy, the encode and a card "
                          f"write ({scratch.nbytes // 1024} kB in, {size_kb} kB out)",
                          flush=True)

                if not keep:
                    continue

                wall = dt.datetime.fromtimestamp(exposure_wall_ns / 1e9, dt.timezone.utc)
                stem = f"{node}_{seq:08d}_{wall.strftime('%Y%m%dT%H%M%S_%f')}Z"
                jpeg_path = session_dir / f"{stem}.jpg"
                # COPY OUT AND GET CLEAR, into the buffer allocated during warmup, so
                # the request is released before the encode rather than after it. See
                # _copy_out for why one buffer can be reused.
                scratch = _copy_out(request, scratch)
                buffer = scratch
            finally:
                request.release()
            # The copy is done and the camera is free; the encode has not happened yet.
            sidecar_mono = time.monotonic_ns()

            sidecar = {
                "node": node,
                "seq": seq,
                "file": jpeg_path.name,
                # CHANGED MEANING, and loudly because consumers join on it: these are now
                # the EXPOSURE instant, converted from SensorTimestamp, where they used to
                # be whenever the loop got around to stamping the frame -- which was a
                # median 578 ms after the shutter. `monotonic_ns` below is still a
                # post-save reading, so the old quantity has not been lost.
                "wall_clock_utc": wall.isoformat().replace("+00:00", "Z"),
                "wall_clock_unix": wall.timestamp(),
                "exposure_wall_ns": exposure_wall_ns,
                "monotonic_ns": sidecar_mono,
                # SensorTimestamp is CLOCK_BOOTTIME ns at exposure -- the field
                # that anchors PPK-style interpolation against ArduPilot pose. Per
                # control_ids_core.yaml it is "the time when the first row of the image
                # sensor active array is exposed", i.e. frame START, which is the quantity
                # camera sync aligns -- and, now, what this frame was selected on.
                "sensor_timestamp_ns": exposure_boot_ns,
                # THE CROSS-POD FIELD, and libcamera's own answer to the question this
                # pod's selection rule computes by hand. Same instant as SensorTimestamp
                # but CLOCK_REALTIME: "Being a wall clock measurement, it can be used to
                # synchronise timing across different devices" (control_ids_core.yaml),
                # and it is the exact value the sync algorithm compares -- ipa_base.cpp
                # reads FrameWallClock into syncParams.wallClock and sync.cpp aligns on it.
                #
                # So it both cross-checks `exposure_wall_ns` above, which is derived from
                # `clk`, and is the field to prefer when the two disagree.
                #
                # Null if the libcamera on the device predates the control, which is worth
                # seeing in the data rather than papering over with a reconstruction.
                "frame_wall_clock_ns": metadata.get("FrameWallClock"),
                # This device's own boot->wall conversion for the line above, and the same
                # keys the accel reader writes.
                "clk": clk,
                # How far this exposure sat from the second it was kept for. The frame-level
                # evidence that two pods chose the same instant: subtract the pair. Offsets
                # near +/-100 ms mean the grid's phase is at the edge of the decision
                # window -- see _boundary_offset_ns.
                "second_offset_us": round(offset_ns / 1000),
                "exposure_us": metadata.get("ExposureTime"),
                "analogue_gain": metadata.get("AnalogueGain"),
                "digital_gain": metadata.get("DigitalGain"),
                # What was asked for vs what the sensor did -- if the control
                # split is unsupported these disagree, and the frame says so.
                "exposure_cap_us": exposure_cap,
                "lens_position": metadata.get("LensPosition"),
                "af_state": metadata.get("AfState"),
                # Distinguishes "no voice coil on this module" from "metadata absent":
                # both leave lens_position null, but only the first means the lens
                # cannot be displaced by vibration. A fixed-lens frame reads
                # "mechanically-fixed" here.
                "focus_commanded": focus_active,
                # libcamera's own account of the camera-sync state, per frame.
                #
                # SyncReady goes true on the first frame server and clients agree they are
                # synchronised, and stays true for the session. SyncTimer counts
                # microseconds to that moment and keeps reporting afterwards -- and at the
                # crossing it "indicates how closely synchronised the client believes it is
                # with the server" (control_ids_rpi.yaml), which makes it a MEASUREMENT and
                # not a flag. That is why it is here per frame rather than on the bus.
                #
                # Always server or client now: there is no unsynced mode.
                "sync_mode": sync_active,
                "sync_ready": metadata.get("SyncReady"),
                "sync_timer_us": metadata.get("SyncTimer"),
                # Rolling-shutter window. Band pitch in rows -> Hz needs the
                # line time; see containers/campod-camera/README.md.
                "frame_duration_us": metadata.get("FrameDuration"),
                "size": [size[0], size[1]],
                "sync_mode": sync_active,
                "phase": phase,
            }
            # One handoff per kept frame: the worker encodes, writes the image, then the
            # sidecar. An image with no sidecar is unusable for pairing, so the sidecar
            # landing is what marks the frame complete.
            serializer.put(buffer, jpeg_path, session_dir / f"{stem}.json",
                           json.dumps(sidecar).encode())
            seq += 1
            # Count plus the time of the last one. A count alone cannot distinguish
            # "capturing" from "stopped at 412", and a consumer must not have to
            # poll this pod to find out -- polling a capturing campod is the one
            # thing that reliably breaks it.
            state["frames"] = seq
            state["last_frame_mono_s"] = round(sidecar_mono / 1e9, 3)
            publish()
            if seq % 30 == 0:
                print(f"capture: {seq} frames -> {session_dir}", flush=True)
    finally:
        picam2.stop()
        # Drain before announcing a count, so the number logged is the number on the card.
        # SIGTERM gets 90 s here (--stop-timeout in the quadlet), and a drain is one write.
        serializer.close()
        ledger.close()
        state["phase"] = "stopped"
        state["warm"] = False
        state["capturing"] = False
        # publish() recomputes `ready` from `warm`, so a stopped camera reports
        # not-ready without this having to say so twice.
        publish()
        extra = ""
        if (serializer.waits or serializer.write_waits or serializer.errors
                or serializer.deepest_queue > 1):
            extra = (f" ({serializer.waits} waited on the encoder, "
                     f"{serializer.write_waits} waited on the card, "
                     f"write queue reached {serializer.deepest_queue} of "
                     f"{serializer.WRITE_QUEUE_FRAMES}, "
                     f"{serializer.errors} errors)")
        print(f"capture: stopped after {seq} frames kept of {frames_seen} seen"
              f"{extra}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
