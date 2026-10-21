"""The capture loop end to end, against Aravis's built-in simulated camera.

Runs at image build, so it exercises the Aravis, PyGObject, numpy and simplejpeg the
image actually ships: frames kept only while the flag says so, a single-channel JPEG
plus sidecar per kept frame, a ledger line per frame seen, the status document pod-link
reads, and a clean drain on stop. No camera, no board.

--require-manifest also asserts the session recorded the image's own build manifest,
which only exists inside the built image.
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import simplejpeg

import capture
from capture import Aravis


def wait_for(predicate, timeout_s, what):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def status(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def main(require_manifest):
    Aravis.enable_interface("Fake")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        flag = tmp / "capture_flag"
        status_path = tmp / "status"
        env = {
            "FLIR_CAPTURE_DIR": str(tmp / "captures"),
            "FLIR_FRAME_RATE_HZ": "20",
            "FLIR_CAPTURE_FLAG": str(flag),
            "FLIR_STATUS_FILE": str(status_path),
        }
        os.environ.update(env)

        failed = []

        def drive():
            # Paused first: frames are seen and ledgered, nothing is written.
            try:
                wait_for(lambda: status(status_path).get("ready"), 20, "ready")
                wait_for(lambda: status(status_path).get("frames_seen", 0) >= 5, 10,
                         "paused frames")
                assert status(status_path)["frames"] == 0, "wrote frames while paused"
                flag.write_text("1\n")
                wait_for(lambda: status(status_path).get("frames", 0) >= 5, 10, "kept frames")
            except AssertionError as exc:
                failed.append(exc)
            finally:
                # main() runs on this process's main thread, which signal.signal requires,
                # so the stop comes from here either way.
                capture._stop = True

        driver = threading.Thread(target=drive, daemon=True)
        driver.start()
        rc = capture.main()
        driver.join(5)
        if failed:
            raise failed[0]
        assert rc == 0, rc

        doc = status(status_path)
        assert doc["phase"] == "stopped" and doc["ready"] is False, doc
        kept = doc["frames"]
        sessions = list((tmp / "captures").glob("*/*"))
        assert len(sessions) == 1, sessions
        session = sessions[0]

        jpegs = sorted(session.glob("*.jpg"))
        sidecars = sorted(session.glob("*.json"))
        assert len(jpegs) == kept and len(sidecars) == kept, (len(jpegs), len(sidecars), kept)
        assert not list(session.glob(".warmup-probe*")), "warmup probe left behind"

        h, w, cs, sub = simplejpeg.decode_jpeg_header(jpegs[0].read_bytes())
        assert (cs, sub) == ("Gray", "Gray"), (cs, sub)
        side = json.loads(sidecars[0].read_text())
        assert side["file"] == jpegs[0].name and side["size"] == [w, h], side
        for key in ("device_timestamp_ns", "system_timestamp_ns", "frame_id", "clk"):
            assert side[key] is not None, key

        ledger = [json.loads(line) for line in (session / "frames.jsonl").read_text().splitlines()]
        assert len(ledger) == doc["frames_seen"], (len(ledger), doc["frames_seen"])
        decisions = {r["decision"] for r in ledger}
        assert decisions == {"paused", "kept"}, decisions

        if require_manifest:
            assert (session / "manifests" / "flir-camera").is_file(), "no build manifest recorded"
        print(f"test_capture: ok -- {kept} kept of {doc['frames_seen']} seen, "
              f"{w}x{h} Gray JPEG")


if __name__ == "__main__":
    main("--require-manifest" in sys.argv[1:])
