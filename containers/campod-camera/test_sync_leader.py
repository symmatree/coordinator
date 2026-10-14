#!/usr/bin/env python3
"""Who paces whom, derived from one fleet-wide leader name.

The deployment can only give every pod the same value -- stacks/campod/ is one file copied
to each pod and a converge passes only device_role -- so the server/client split has to come
from each pod comparing that value against its own name.
"""
import importlib.util
import sys
import types
from pathlib import Path

# picamera2 and libcamera are stubbed, the same way test_capture_wait.py does it and for
# the same reason: the real import is the expensive thing and there is no camera on a
# builder. This test needs neither -- _sync_mode_for is pure.
_picam = types.ModuleType("picamera2")
_picam.Picamera2 = type("Picamera2", (), {})
sys.modules["picamera2"] = _picam
_libcamera = types.ModuleType("libcamera")
_libcamera.controls = types.SimpleNamespace()
sys.modules["libcamera"] = _libcamera

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("capture_sync", HERE / "capture.py")
cap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cap)

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


f = cap._sync_mode_for

# The deployed value, on each of the four pods in turn.
check("the named pod is the server", f("campod-sw", "campod-sw") == "server")
check("every other pod is a client",
      [f("campod-sw", n) for n in ("campod-se", "campod-ne", "campod-nw")]
      == ["client", "client", "client"])

# No leader means sync off entirely -- NOT "everyone is a client", which looks enabled
# while nothing paces anything and the frames are no more aligned than before.
check("no leader means off", f("", "campod-sw") == "off")
check("None leader means off", f(None, "campod-se") == "off")

# A leader that is not in the fleet leaves everybody a client. That is a real
# misconfiguration -- a typo in one shared value disables pacing for the whole fleet --
# which is why capture.py logs the leader AND what this pod resolved to.
check("a misspelled leader leaves nobody pacing",
      f("campod-sww", "campod-sw") == "client",
      "capture.py must log the resolution so this is visible")

# Exact match, not a prefix or a substring: campod-s must not make campod-sw the server.
check("the match is exact", f("campod-s", "campod-sw") == "client")
check("and not a suffix either", f("sw", "campod-sw") == "client")

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_sync_leader: all checks passed")
