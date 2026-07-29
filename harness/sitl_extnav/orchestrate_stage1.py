#!/usr/bin/env python3
"""Stage 1: does propagating the VINS reset_counter make EKF3 do a CLEAN position
reset instead of fighting the datum jump as a glitch?

Runs both arms head-to-head, fully in SITL (native x86, no FC hardware, no qemu):

  synthetic pose --unix socket--> [real coordinator-mavlink router.py]
     --MAVLink/tcp serial5--> [arducopter SITL EKF3, EK3_SRC1=ExtNav, GPS off]

Each arm holds a stationary ExtNav position, then STEPS it (a datum jump) and, on
the "on" arm, bumps reset_counter at the step. We watch the EKF's own fused
position (LOCAL_POSITION_NED) settle onto the new datum:
  - reset ON  -> clean ResetPositionNE: EKF snaps to the new datum immediately.
  - reset OFF -> the jump is gated as a glitch: rejected, coasts, slow/late pull-in.

Truth is exact (the ExtNav we command). Output: per-arm EKF-x vs time-since-step,
plus settle-time-to-90%. Writes results JSON for analysis.
"""
import json
import os
import socket
import struct
import subprocess
import sys
import time

os.environ["MAVLINK20"] = "1"
from pymavlink import mavutil  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
ARDUCOPTER = os.path.expanduser("~/ardupilot/build/sitl/bin/arducopter")
COPTER_PARM = os.path.expanduser("~/ardupilot/Tools/autotest/default_params/copter.parm")
EXTNAV_PARM = os.path.join(HERE, "extnav.parm")
ROUTER = os.path.join(REPO, "containers", "coordinator-mavlink", "router.py")
HOME = "-35.363261,149.165230,584,353"
ORIGIN = (int(-35.363261e7), int(149.165230e7), int(584e3))

STEP_M = 3.0        # datum jump (north), well beyond the ~5-sigma innovation gate
WARMUP_S = 10.0     # stationary hold so the EKF locks onto ExtNav at 0
POST_S = 12.0       # observation window after the step
RATE_HZ = 20.0
FEAT = 30           # synthetic feature_count (contract v2 field; unused by router)


def wait_heartbeat(port, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            c = mavutil.mavlink_connection(f"tcp:127.0.0.1:{port}")
        except Exception:
            time.sleep(1); continue
        if c.wait_heartbeat(timeout=5):
            return c
        c.close(); time.sleep(1)
    raise RuntimeError(f"no heartbeat on {port}")


def run_arm(reset_on, instance):
    label = "on" if reset_on else "off"
    port0 = 5760 + 10 * instance
    port5 = 5765 + 10 * instance
    rundir = os.path.join(HERE, "run", f"arm_{label}")
    os.makedirs(rundir, exist_ok=True)
    for f in ("eeprom.bin",):
        try:
            os.remove(os.path.join(rundir, f))
        except OSError:
            pass
    sockpath = f"/tmp/stage1_{label}.sock"

    sitl = subprocess.Popen(
        [ARDUCOPTER, "--model", "quad", "--home", HOME,
         "--defaults", f"{COPTER_PARM},{EXTNAV_PARM}",
         f"--serial5=tcp:{port5}", "-I", str(instance)],
        cwd=rundir, stdout=open(os.path.join(rundir, "sitl.log"), "w"),
        stderr=subprocess.STDOUT)
    router = None
    try:
        print(f"[{label}] SITL pid {sitl.pid}, waiting heartbeat on {port0}...", flush=True)
        c = wait_heartbeat(port0)
        print(f"[{label}] heartbeat, sys {c.target_system}", flush=True)
        c.mav.set_gps_global_origin_send(c.target_system, *ORIGIN)
        c.mav.command_long_send(c.target_system, c.target_component,
                                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                                mavutil.mavlink.MAVLINK_MSG_ID_LOCAL_POSITION_NED,
                                int(1e6 / RATE_HZ), 0, 0, 0, 0, 0)
        time.sleep(2)  # let boot finish + serial5 bind

        router = subprocess.Popen(
            [sys.executable, ROUTER, "--device", f"tcp:127.0.0.1:{port5}",
             "--socket", sockpath, "--source-system", "1", "--source-component", "197"],
            stdout=open(os.path.join(rundir, "router.log"), "w"), stderr=subprocess.STDOUT)
        for _ in range(100):
            if os.path.exists(sockpath):
                break
            time.sleep(0.05)
        time.sleep(1.5)  # router connects to serial5
        print(f"[{label}] router pid {router.pid}, socket {sockpath}", flush=True)

        tx = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

        def send(x, rst):
            # v2 datagram: quat(identity), pos(x,0,0), junk vel, reset_counter, feat
            tx.sendto(struct.pack("<12f", 1, 0, 0, 0, x, 0, 0, 9, 9, 9, rst, FEAT), sockpath)

        series = []  # (t_since_step, ekf_north)
        t0 = time.time()
        step_t = None
        dt = 1.0 / RATE_HZ
        while True:
            now = time.time() - t0
            if now < WARMUP_S:
                send(0.0, 0)
            else:
                if step_t is None:
                    step_t = time.time()
                    print(f"[{label}] STEP to {STEP_M} m north, reset={1 if reset_on else 0}", flush=True)
                send(STEP_M, 1 if reset_on else 0)
                if time.time() - step_t > POST_S:
                    break
            m = c.recv_match(type="LOCAL_POSITION_NED", blocking=True, timeout=dt)
            if m is not None and step_t is not None:
                series.append((round(time.time() - step_t, 3), round(m.x, 4)))
        # settle time: first t_since_step where EKF reached 90% of the step
        thr = 0.9 * STEP_M
        settled = next((t for t, x in series if x >= thr), None)
        pre = [x for t, x in series if t < 0.2]
        summary = {
            "arm": label, "reset_on": reset_on, "step_m": STEP_M,
            "settle90_s": settled,
            "ekf_x_at_0.5s": next((x for t, x in series if t >= 0.5), None),
            "ekf_x_at_2s": next((x for t, x in series if t >= 2.0), None),
            "ekf_x_final": series[-1][1] if series else None,
            "n_samples": len(series),
        }
        return summary, series
    finally:
        for p in (router, sitl):
            if p is not None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        try:
            os.remove(sockpath)
        except OSError:
            pass
        time.sleep(2)  # let ports release before the next arm


def main():
    results = {}
    for reset_on, inst in [(False, 0), (True, 1)]:
        summary, series = run_arm(reset_on, inst)
        results[summary["arm"]] = {"summary": summary, "series": series}
        print(f"[{summary['arm']}] {json.dumps(summary)}", flush=True)
    out = os.path.join(HERE, "run", "stage1_results.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
