# VIO evaluation harness

Bracket the real coordinator binaries with fakes and drive them with
synthetic / recorded / idealized input, no hardware. See coordinator #35 for the
full design.

**Router half** (v1) -- exercises the two seams the router owns:

```
pose datagrams -> [REAL router.py] --udpout--> MAVLink --> [fake FC (udpin)]
```

the `chobits_server` byte-contract (`float[10]`) and the outgoing MAVLink
(`ATT_POS_MOCAP` / `VISION_SPEED_ESTIMATE`, the `(x,-y,-z)` axis flip, and the
`TIMESYNC` handshake) -- with zero hardware and no FC.

**Estimator half** -- replays a recorded `chobits_imu`/`chobits_features` fixture
back into a *real* `vins_fusion` to regenerate pose offline:

```
vio-ipc-record fixture -> [input_replayer.py] -> chobits_imu / chobits_features
    -> [REAL vins_fusion] -> chobits_server -> [vio-pose-tap] -> pose CSV
```

The fixture comes from a bench capture with the estimator **stopped** (tracker ->
`vio-ipc-record`, so the recorder can bind the input sockets) -- see coordinator
#42 for the first such captures. Post-hoc pose from captured inputs is the
foundation of the VIO-quality analysis (`analysis/vio-input-alignment.ipynb`).

## Pieces

| File | Role |
|------|------|
| `fake_fc.py` | Fake flight controller: a pymavlink `udpin` endpoint. Receives the VIO messages, exposes them for assertion, runs the FC side of the `TIMESYNC` handshake. Importable, or run standalone to watch a stream. |
| `pose_replayer.py` | The inverse of `vio-pose-tap`: *sends* `float[10]` pose datagrams into `chobits_server` so the router forwards them. Reads `vio-pose-tap` CSV or console-format logs; the "recorded replay" input source. |
| `test_router_stack.py` | The integration test. Spawns the real `router.py` over `udpout`, replays distinct poses, and asserts values + axis flip per pose, then the timesync reply. Runs in CI (`.github/workflows/stack-smoke.yaml`). |
| `input_replayer.py` | Estimator half. Decodes a `vio-ipc-record` fixture (`<ddHI>` frames + `.json` manifest), sorts by `t_mono`, and re-sends the raw `chobits_imu`/`chobits_features` payload bytes into a running real `vins_fusion`. Raw-bytes passthrough so the feature packet schema is irrelevant; blocking sends so nothing is dropped under backpressure. |
| `test_input_replayer.py` | Hardware-free test for `input_replayer`: writes a synthetic fixture, binds fake input sockets drained by reader threads, and asserts byte-exact, correctly-routed, `t_mono`-ordered delivery. |

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

## Replay a capture through the real estimator (three shells)

Regenerate VINS pose from a recorded input fixture. Needs the
`coordinator-vio-estimator` image; run vins with `multiple_thread: 0` for a
deterministic, reproducible pose (edit the mounted `oak_d.yaml`). Bring up the
**estimator alone** -- not the whole `bench` profile -- so the live tracker isn't
also writing to `chobits_imu`/`chobits_features` and corrupting the replay:

```sh
# 1. real vins_fusion only, its input sockets bound (NOT vio-tracker)
COMPOSE_PROFILES=bench docker compose -f stacks/coordinator/compose.yaml up -d vio-estimator
# 2. tap its pose output to CSV
vio-pose-tap --out pose-<run>.csv                 # binds /tmp/chobits_server
# 3. replay the captured inputs into it
python3 harness/input_replayer.py ~/captures/wave-<ts>.bin
```

The pose CSV (`t_unix,t_mono,qw..vz`) then feeds `analysis/vio-input-alignment.ipynb`
for alignment against the FC log. Self-test with no hardware:
`python3 harness/test_input_replayer.py`. See
[docs/bench-estimator.md](../docs/bench-estimator.md) for the estimator bench and
`multiple_thread`/calibration notes.

## Assertion philosophy

Value asserts on the synthetic poses are exact (the router is a pure transform).
Replaying a *captured* stream asserts only that it flows through without crashing
("not crashing / relatively sane") -- exact/within-tolerance pose asserts against
a golden await the timestamped fixture and a frozen reference run.
