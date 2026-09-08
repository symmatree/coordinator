#!/usr/bin/env python3
"""test_router_telem -- the router's coordinator-side telemetry record.

No serial port, no FC: build message stubs, run them through `log_telem`, and assert
the projection, the per-type rate limit, and that a mode change is never dropped.

The mode-change case is the one worth pinning. Everything else in this log is a
sampled signal where losing a line costs resolution; a mode change is an edge that
bounds the interpretation of everything around it, so it is forced past the rate
limit. A test that only checked the happy path would not notice that regressing.

Run: python3 harness/test_router_telem.py   (exit 0 = pass)
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "containers", "coordinator-mavlink"))

import router  # noqa: E402


class Msg:
    def __init__(self, mtype, **fields):
        self._t = mtype
        self.__dict__.update(fields)

    def get_type(self):
        return self._t


def lines(fh):
    return [json.loads(l) for l in fh.getvalue().splitlines() if l.strip()]


def test_projects_only_whitelisted_fields():
    fh = io.StringIO()
    router.log_telem(fh, Msg("SYSTEM_TIME", time_unix_usec=1786712345000000,
                             time_boot_ms=4242, secret="nope"), 1_000_000_000, {}, 1.0)
    (rec,) = lines(fh)
    assert rec["type"] == "SYSTEM_TIME"
    assert rec["time_unix_usec"] == 1786712345000000 and rec["time_boot_ms"] == 4242
    assert "secret" not in rec
    assert rec["monotonic_ns"] == 1_000_000_000


def test_unknown_types_are_not_logged():
    fh = io.StringIO()
    router.log_telem(fh, Msg("SCALED_IMU2", xacc=1), 1_000_000_000, {}, 1.0)
    assert lines(fh) == []


def test_rate_limit_is_per_type():
    """A 2 Hz stream and a 1 Hz stream should each land at the limit, not compete."""
    fh, seen = io.StringIO(), {}
    for k in range(6):                       # 0.0 .. 2.5 s, one of each type per 0.5 s
        t = int(k * 0.5 * 1e9)
        router.log_telem(fh, Msg("VFR_HUD", alt=1.0, groundspeed=0.0), t, seen, 1.0)
        router.log_telem(fh, Msg("SYS_STATUS", voltage_battery=25000), t, seen, 1.0)
    got = lines(fh)
    for want in ("VFR_HUD", "SYS_STATUS"):
        n = len([r for r in got if r["type"] == want])
        assert n == 3, f"{want}: expected 3 at 1 Hz over 2.5 s, got {n}"


def test_mode_change_is_not_dropped_by_the_rate_limit():
    fh, seen = io.StringIO(), {}
    router.log_telem(fh, Msg("HEARTBEAT", custom_mode=0, base_mode=81,
                             system_status=3), 0, seen, 1.0)
    # 0.1 s later, well inside the 1 Hz window: throttled when unforced...
    router.log_telem(fh, Msg("HEARTBEAT", custom_mode=5, base_mode=81,
                             system_status=4), 100_000_000, seen, 1.0)
    assert len(lines(fh)) == 1
    # ...and kept when the caller flags it as a change.
    router.log_telem(fh, Msg("HEARTBEAT", custom_mode=5, base_mode=81,
                             system_status=4), 100_000_000, seen, 1.0, force=True)
    got = lines(fh)
    assert len(got) == 2 and got[-1]["custom_mode"] == 5


def main():
    import traceback
    failed = []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("  ok   %s" % name)
        except Exception:
            failed.append(name)
            print("  FAIL %s" % name)
            traceback.print_exc()
    print("RESULT: %s" % ("PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
