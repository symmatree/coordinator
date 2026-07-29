# SITL ExtNav bench -- EKF3 reset-counter validation (coordinator #67 / #64)

Hardware-free bench that drives ArduPilot **SITL** (the real EKF3, compiled native
x86 -- no FC, no qemu; SITL only simulates the vehicle physics) with **ExtNav as the
horizontal-position source**, GPS disabled, and feeds it visual-odometry pose through
the *real* `coordinator-mavlink` router. It answers: does propagating the VINS
`reset_counter` (PR #149) make EKF3 do a **clean position reset** instead of fighting
the datum jump as a glitch?

```
synthetic pose --unix dgram--> [ real containers/coordinator-mavlink/router.py ]
   --MAVLink / tcp serial5--> [ arducopter SITL, EK3_SRC1=ExtNav, GPS off, EKF3 ]
```

## Files

- `extnav.parm` -- SITL param overlay: `VISO_TYPE=1`, `EK3_SRC1_*=ExtNav`, GPS off,
  `SERIAL5_PROTOCOL=2` (the router's MAVLink port), `LOG_DISARMED=1`. Layered on
  `~/ardupilot/Tools/autotest/default_params/copter.parm`.
- `probe_extnav.py` -- one-shot feasibility check: confirms the EKF navigates on
  injected `VISION_POSITION_ESTIMATE` (origin set with no GPS via `SET_GPS_GLOBAL_ORIGIN`).
- `orchestrate_stage1.py` -- the experiment. Runs **both arms** head-to-head, each with
  a fresh SITL + router, holds a stationary ExtNav position then **steps it 3 m** (a
  datum jump), bumping `reset_counter` on the "on" arm only, and records the EKF's own
  fused position (`LOCAL_POSITION_NED`) settling onto the new datum.
- `results/` -- committed evidence (below). `run/` is gitignored scratch.

## Run

```sh
# both arms, ~70 s, writes run/stage1_results.json
python3 orchestrate_stage1.py
```

Needs `~/ardupilot/build/sitl/bin/arducopter` built at the Copter-4.7.0 tag and
`pymavlink` on the host.

## Result (Stage 1) -- clean reset vs fought glitch

`results/stage1_reset_vs_glitch.png`, data in `results/stage1_results.json`:

| arm | at the 3 m datum jump | EKF north @0.5 s | @2 s | @12 s |
|-----|----------------------|------------------|------|-------|
| **reset propagated** (Rst++) | clean `ResetPositionNE`, **settles in 0.052 s** | 3.00 | 3.00 | 3.00 |
| **reset withheld** (Rst=0) | jump gated as a glitch, rejected | 0.00 | 0.00 | 2.12 (still short) |

So forwarding the reset counter turns a multi-second fought correction (the vehicle
believing it is metres from where the datum says, exactly the E21 interactive-fitness
gate) into an immediate clean reset. This exercises the real EKF3 end-to-end through
the merged PR #149 router.

## Operational notes (for the #64 bench)

- **Do not run SITL with `--speedup > 1` when a GCS connects in this sandbox** -- SITL
  exits shortly after the connection. `--speedup 1` is stable.
- Long-lived SITL must be launched so it is **not reaped as a shell background job**
  (use a managed subprocess or the harness background mechanism, not bare `&`).
- serial0 (`tcp:5760+10*I`) is the control/telemetry channel; `--serial5=tcp:PORT`
  (a TCP server) is the router's vision channel. SITL binds serial5 only **after** a
  client connects to serial0 and boot completes.
- The EKF needs an origin with no GPS: send `SET_GPS_GLOBAL_ORIGIN` (the ArduPilot
  `ViconPosition` autotest recipe).

## Not covered here (Stage 2, separate)

Faithful replay on the **real 260728 flight** (real IMU/dynamics, VIO driving) via
`Tools/Replay` with a ~6-line patch injecting `REPH.resetTime_ms` at the real reset
epochs -- because EKF3-in-replay keys resets off `resetTime_ms`, which the FC derives
from the `reset_counter` change (`AP_VisualOdom_Backend::get_reset_timestamp_ms`).
The flight's counter was pinned at 0, so `RT` was constant -- which is *why* its jumps
were fought (E21). Prereqs: `LOG_REPLAY=1` (confirmed present) and `REPH` frames in the
`.bin` (to verify).
