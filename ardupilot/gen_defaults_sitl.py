#!/usr/bin/env python3
"""Dump the ArduCopter code-default parameter set by booting SITL bare.

SITL runs the *real* firmware, so a fresh-eeprom boot with no param overlay
initializes to the code defaults for the build's version -- the authoritative
answer for any param whose default a static source-scan can't resolve (macros,
DEFAULT_POINTER constructors, #if branches). We deliberately do NOT layer
Tools/autotest/default_params/copter.parm (a testing overlay).

Caveats:
  - SITL is its own board. Board-specific hwdef defaults (serial protocols,
    INS_FAST_SAMPLE, BATT_MONITOR, NTF_LED_TYPES, RELAY pins, ...) come from the
    real board's hwdef, not SITL -- those are already resolved in overrides.csv
    from the TBS_LUCID_H7 hwdef. Use this dump for code-defaults, and cross-check
    board-gated params against the board hwdef.
  - A few params are compiled out of SITL (OSD layout, scripting heap): resolve
    those from the source macro instead.

Usage:
  python3 ardupilot/gen_defaults_sitl.py [out.parm]
Requires ~/ardupilot/build/sitl/bin/arducopter built at the target tag, pymavlink.
Prints the firmware version it booted so you can confirm it matches the FC.
"""
import os
import subprocess
import sys
import tempfile
import time

from pymavlink import mavutil

ARDUCOPTER = os.path.expanduser("~/ardupilot/build/sitl/bin/arducopter")
OUT = sys.argv[1] if len(sys.argv) > 1 else "sitl_defaults.parm"
PORT = 5760


def main():
    rundir = tempfile.mkdtemp(prefix="sitl_defaults_")  # fresh eeprom -> code defaults
    sitl = subprocess.Popen(
        [ARDUCOPTER, "--model", "quad", "--home", "37.0,-122.0,0,0",
         "--serial0", f"tcp:{PORT}"],
        cwd=rundir, stdout=open(os.path.join(rundir, "sitl.log"), "w"),
        stderr=subprocess.STDOUT)
    try:
        m = mavutil.mavlink_connection(f"tcp:127.0.0.1:{PORT}")
        m.wait_heartbeat(timeout=30)
        m.mav.command_long_send(
            m.target_system, m.target_component,
            mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES, 0, 1, 0, 0, 0, 0, 0, 0)
        v = m.recv_match(type="AUTOPILOT_VERSION", blocking=True, timeout=5)
        if v:
            fv = v.flight_sw_version
            print(f"booted firmware {(fv >> 24) & 0xff}.{(fv >> 16) & 0xff}.{(fv >> 8) & 0xff}")

        m.mav.param_request_list_send(m.target_system, m.target_component)
        params, expected, t0 = {}, None, time.time()
        while time.time() - t0 < 60:
            msg = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=5)
            if msg is None:
                if expected and len(params) >= expected:
                    break
                continue
            params[msg.param_id] = msg.param_value
            expected = msg.param_count
            if expected and len(params) >= expected:
                break
        with open(OUT, "w") as f:
            for k in sorted(params):
                f.write(f"{k},{params[k]:.8g}\n")
        print(f"wrote {len(params)} params to {OUT}")
    finally:
        sitl.terminate()
        try:
            sitl.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sitl.kill()


if __name__ == "__main__":
    main()
