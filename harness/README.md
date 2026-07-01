# VIO evaluation harness

Bracket the real coordinator binaries with fakes and drive them with
synthetic / recorded / idealized input, no hardware. See coordinator #35 for the
full design. This is **v1 = the router half**:

```
pose datagrams -> [REAL router.py] --udpout--> MAVLink --> [fake FC (udpin)]
```

It exercises the two software seams the router owns -- the `chobits_server`
byte-contract (`float[10]`) and the outgoing MAVLink (`ATT_POS_MOCAP` /
`VISION_SPEED_ESTIMATE`, the `(x,-y,-z)` axis flip, and the `TIMESYNC` handshake)
-- with zero hardware and no FC. The estimator half (real `vins_fusion` fed a
recorded `chobits_imu`/`chobits_features` fixture) joins later to make this the
full stack smoke; that fixture needs a bench capture with the estimator stopped.

## Pieces

| File | Role |
|------|------|
| `fake_fc.py` | Fake flight controller: a pymavlink `udpin` endpoint. Receives the VIO messages, exposes them for assertion, runs the FC side of the `TIMESYNC` handshake. Importable, or run standalone to watch a stream. |
| `pose_replayer.py` | The inverse of `vio-pose-tap`: *sends* `float[10]` pose datagrams into `chobits_server` so the router forwards them. Reads `vio-pose-tap` CSV or console-format logs; the "recorded replay" input source. |
| `test_router_stack.py` | The integration test. Spawns the real `router.py` over `udpout`, replays distinct poses, and asserts values + axis flip per pose, then the timesync reply. Runs in CI (`.github/workflows/stack-smoke.yaml`). |

## Run the seam test

Needs `pymavlink` (the pinned `containers/coordinator-mavlink/requirements.txt`):

```sh
python3 harness/test_router_stack.py                  # built-in synthetic poses
python3 harness/test_router_stack.py path/to/pose-log # also replay a real capture
```

`ROUTER_PY` overrides where `router.py` is found (defaults to the repo tree, then
`/opt/coordinator/router.py` for running inside the coordinator-mavlink image).

## Drive it by hand (three shells)

```sh
# 1. fake FC
python3 harness/fake_fc.py --port 14550
# 2. real router -> fake FC over UDP (no serial)
python3 containers/coordinator-mavlink/router.py \
    --device udpout:127.0.0.1:14550 --socket /tmp/chobits_server --source-component 197
# 3. replay a captured pose stream into the router's socket
python3 harness/pose_replayer.py --socket /tmp/chobits_server path/to/pose-log
```

`pose_replayer` honors `vio-pose-tap` CSV timestamps (scale with `--speed`), falls
back to `--rate` Hz for console-format logs, or `--fast` for bulk/batch replay.

## Assertion philosophy

Value asserts on the synthetic poses are exact (the router is a pure transform).
Replaying a *captured* stream asserts only that it flows through without crashing
("not crashing / relatively sane") -- exact/within-tolerance pose asserts against
a golden await the timestamped fixture and a frozen reference run.
