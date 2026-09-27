#!/usr/bin/env python3
"""Hardware-free test for coord-fc-log's log listing.

The one thing worth pinning here is that the listing is DRAINED TO COMPLETION.
AP_Logger registers the requesting link for a transfer's duration and
`handle_log_request_data` returns unconditionally while one is registered -- so a client
that stops reading as soon as it has the entries it wants leaves the FC listing, and
every subsequent LOG_REQUEST_DATA is discarded in silence. That is a download that
reports 0 bytes with no error, which is what happened on 2026-09-27.

    python3 test_coord_fc_log.py
"""
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

# An explicit loader: the script has no .py extension, so importlib cannot infer one.
_path = Path(__file__).with_name("coord-fc-log")
spec = importlib.util.spec_from_loader(
    "coord_fc_log", importlib.machinery.SourceFileLoader("coord_fc_log", str(_path)))
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)

ok = True


def check(label, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + label + (f"  {detail}" if detail else ""))
    if not cond:
        ok = False


class Entry:
    def __init__(self, i, num, last, size=100, tutc=0):
        self.id, self.num_logs, self.last_log_num = i, num, last
        self.size, self.time_utc = size, tutc


class FakeMav:
    """Minimal stand-in: records what was sent, replays a scripted LOG_ENTRY stream."""

    def __init__(self, entries):
        self.entries = list(entries)
        self.sent = []
        self.target_system = self.target_component = 1
        self.mav = self

    def log_request_list_send(self, *a):
        self.sent.append("list")

    def log_request_end_send(self, *a):
        self.sent.append("end")

    def recv_match(self, type=None, blocking=False, timeout=None):
        return self.entries.pop(0) if self.entries else None


# 1. The happy path: three logs, and the LAST entry is what releases the FC. A client
#    that stopped at "I have three" would leave the listing open.
m = FakeMav([Entry(1, 3, 3), Entry(2, 3, 3), Entry(3, 3, 3)])
logs = fc.list_logs(m)
check("returns every log", sorted(logs) == [1, 2, 3], str(sorted(logs)))
check("reads through to the entry where id == last_log_num", not m.entries)
check("and does NOT cancel on the happy path", "end" not in m.sent, str(m.sent))

# 2. Duplicate ids must not be mistaken for completion. len(logs) reaches num_logs after
#    the third message here, but the listing is not finished until id == last_log_num.
m = FakeMav([Entry(1, 3, 3), Entry(1, 3, 3), Entry(2, 3, 3), Entry(3, 3, 3)])
logs = fc.list_logs(m)
check("a repeated id does not end the drain early", sorted(logs) == [1, 2, 3], str(sorted(logs)))
check("still no cancel", "end" not in m.sent, str(m.sent))

# 3. An empty FC terminates: ArduPilot sends id=0/num_logs=0/last=0.
check("no logs is empty, not a hang", fc.list_logs(FakeMav([Entry(0, 0, 0, 0, 0)])) == {})

# 4. A stall IS the case cancelling repairs -- and only then. The stream dies mid-listing,
#    so it cancels, retries the listing once, and succeeds on the second pass.
m = FakeMav([Entry(1, 3, 3)])            # then None forever -> stall
logs = fc.list_logs(m)
check("a stalled listing cancels", "end" in m.sent, str(m.sent))
check("and re-requests the listing", m.sent.count("list") == 2, str(m.sent))
check("and reports nothing rather than a partial list", logs == {}, str(logs))

# 5. gaps() coalesces missing blocks into runs, block-aligned, the way MAVProxy does.
check("no gaps when everything arrived", fc.gaps(set(range(5)), 5) == [])
check("a single hole is one run", fc.gaps({0, 1, 3, 4}, 5) == [(2, 1)])
check("adjacent holes coalesce", fc.gaps({0, 4}, 5) == [(1, 3)])
check("separate holes stay separate", fc.gaps({2}, 5) == [(0, 2), (3, 2)])
check("nothing arrived is one whole run", fc.gaps(set(), 4) == [(0, 4)])

# 6. Block size must be what one LOG_DATA carries, or offsets stop being aligned. 262144
#    was the old window size and is NOT a multiple of it -- that is why every window
#    after the first began mid-block.
check("BLOCK is the LOG_DATA payload size", fc.BLOCK == 90)
check("the old 256 KiB window was not block-aligned", 262144 % fc.BLOCK != 0)

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
