#!/usr/bin/env python3
"""Set the coordinator's DS3234 from this pod's clock, for when there is no GPS to take it from.

THE WAY TO RUN THIS IS THE WORKFLOW: the `set-coordinator-rtc` WorkflowTemplate in tiles
(`tanka/environments/fleet-control/main.jsonnet`) runs it as its one step, in the
fleet-control image, so the time written is the cluster node's clock.

    set-rtc

What it does, in order, and why each is there:

  stop the coordinator -- `coord-rtc systohc` writes the seconds register, which re-phases
    the 1 Hz every campod aligns capture to; its docstring says to do it with capture
    stopped
  read the chip first -- a chip without VCC does not drive MISO and every register reads
    0x00, so systohc would write into nothing and the reboot would come back at the old
    time. That is refused here, with the reading, rather than discovered after
  set the system clock and the chip in one ssh session -- the time is sent once the
    session is open, so the error is the one-way latency and not the ssh handshake
  reboot -- coord-rtc-clock sets the system clock from the chip at boot, and chrony
    follows the chip, so the reboot is the check that the chip holds the time
  compare after -- the coordinator's clock against this pod's, printed, and a failure if it
    is more than MAX_OFFSET_S off

After this, coord-rtc-feed corrects the chip from the FC whenever the coordinator hears
the FC, the FC has UTC and is disarmed.

Environment, all of which the fleet-control pod already has:
  FLEET_URL       the API (default below)
  FLEET_SSH_KEY   private key that reaches pi@coordinator
  FLEET_SSH_USER  login account on the device (default pi)
"""
from __future__ import annotations

import calendar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

FLEET_URL = os.environ.get("FLEET_URL", "https://fleet.tiles.symmatree.com")
NODE = "coordinator"
# The largest coordinator-minus-pod offset after the reboot that counts as set. The chip
# holds whole seconds, and coord-rtc-clock sets the system clock from it at boot.
MAX_OFFSET_S = 1.0
CONTAINER_IMAGE = "/etc/container-image"


def log(msg: str) -> None:
    print(f"[set-rtc] {msg}", flush=True)


def build_revision(path: str = CONTAINER_IMAGE) -> str | None:
    try:
        text = open(path).read()
    except OSError:
        return None
    m = re.search(r'^ORG_OPENCONTAINERS_IMAGE_REVISION="?([0-9a-f]{7,40})"?', text, re.M)
    return m.group(1) if m else None


def api(path: str, method: str = "GET", timeout: int = 300):
    # Named, so fleet-control's request log can tell this script from the page and from curl.
    req = urllib.request.Request(f"{FLEET_URL}{path}", method=method, headers={"User-Agent": "set_rtc"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read() or b"null")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"{method} {path} -> {exc.code}: {body}") from exc


# ---- pure helpers, testable without a coordinator --------------------------------------

def chip_time(status: str) -> int | None:
    """The chip's time from `coord-rtc status`, as a Unix second, or None if it is not a date.

    All-zero registers decode as 2000-00-00T00:00:00, which strptime rejects: that is the
    chip without VCC, not a time.
    """
    m = re.search(r"^time\s+(\S+)", status, re.M)
    if not m:
        return None
    try:
        return calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def offset_s(remote: float, sent: float, received: float) -> float:
    """The remote clock minus ours, taking the remote reading at the round trip's midpoint."""
    return remote - (sent + received) / 2


# ---- the coordinator -------------------------------------------------------------------

def ssh_argv(key: str) -> list[str]:
    host = next((n.get("host") or n["name"] for n in api("/nodes", timeout=60)
                 if n["name"] == NODE), None)
    if not host:
        raise SystemExit("[set-rtc] the roster has no coordinator")
    user = os.environ.get("FLEET_SSH_USER", "pi")
    return ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
            "-o", "StrictHostKeyChecking=accept-new", f"{user}@{host}"]


def ssh(key: str, command: str) -> str:
    done = subprocess.run([*ssh_argv(key), command], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"[set-rtc] `{command}` failed ({done.returncode}): "
                         f"{(done.stderr or done.stdout or '').strip()[:300]}")
    return done.stdout.strip()


def set_clock_and_chip(key: str) -> str:
    """Set the system clock to ours, then the chip from it, in one session.

    The remote side says `ready` once the session is up and then reads the time from
    stdin, so the time is taken here after the handshake rather than before it.
    """
    remote = ('echo ready; read t; sudo date -u -s "@$t" >/dev/null && '
              'sudo coord-rtc systohc')
    proc = subprocess.Popen([*ssh_argv(key), remote], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first = proc.stdout.readline().strip()
    if first != "ready":
        proc.kill()
        raise SystemExit(f"[set-rtc] no session: {first!r} {proc.stderr.read().strip()[:300]}")
    proc.stdin.write(f"{time.time():.6f}\n")
    proc.stdin.close()
    out, err = proc.stdout.read(), proc.stderr.read()
    if proc.wait() != 0:
        # systohc exits 1 when OSF did not clear, which is the chip refusing the time.
        raise SystemExit(f"[set-rtc] systohc failed ({proc.returncode}): "
                         f"{(out + err).strip()[:300]}")
    return out.strip()


def uptime_s() -> float | None:
    """The coordinator's uptime from its bus document, the same field bench-cycle watches."""
    pods = api("/pods", timeout=60).get("pods", [])
    doc = next((p for p in pods if p.get("node") == NODE), None)
    return ((doc or {}).get("raw") or {}).get("as_of_boot_s")


def wait_for_new_boot(before: float, timeout_s: int = 600) -> None:
    """Uptime going backwards, not the document existing: bus documents are retained."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(15)
        try:
            up = uptime_s()
        except (SystemExit, OSError):
            continue  # the broker runs on the coordinator, so this fails while it boots
        if up is not None and up < before:
            return
    raise SystemExit(f"[set-rtc] the coordinator did not come back within {timeout_s} s")


def main() -> int:
    key = os.environ.get("FLEET_SSH_KEY", "/secrets/ssh/id")
    log(f"from {build_revision() or 'no image manifest -- a local run'}")

    api(f"/nodes/{NODE}/stop", method="POST", timeout=120)
    log("coordinator stopped")

    status = ssh(key, "sudo coord-rtc status")
    log(f"chip before:\n{status}")
    if chip_time(status) is None:
        raise SystemExit("[set-rtc] the chip's registers are not a date, so it is not "
                         "answering on SPI (no VCC, or the bus is not reaching it); "
                         "nothing was written")

    log(f"systohc:\n{set_clock_and_chip(key)}")

    before = uptime_s()
    if before is None:
        raise SystemExit("[set-rtc] the coordinator has no uptime on the bus to watch")
    api(f"/nodes/{NODE}/reboot", method="POST", timeout=180)
    log("rebooting, so the system clock comes from the chip")
    wait_for_new_boot(before)

    # Only the clock after the reboot: the stack is running again by now, and the chip is
    # coord-rtc-feed's to read.
    sent = time.time()
    remote = float(ssh(key, "date -u +%s.%N"))
    off = offset_s(remote, sent, time.time())
    log(f"coordinator minus this pod after the reboot: {off:+.3f} s")
    if abs(off) > MAX_OFFSET_S:
        raise SystemExit(f"[set-rtc] the coordinator came back {off:+.3f} s off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
