# Offline VIO replay: regenerating VINS pose from a captured fixture

Regenerate the `vins_fusion` pose trajectory for a flight **offline**, from a
`vio-ipc-record` input fixture (`chobits_imu` + `chobits_features`), with no OAK-D
and no Pi. This is the estimator half of the batch VIO harness (coordinator #35)
and the front end of the VIO-quality analysis (coordinator #42): it turns a
recorded flight's *inputs* back into a pose trajectory you can compare against the
FC's EKF/GPS ground truth (`analysis/vio-ekf-comparison.ipynb`).

```
wave-<ts>.feat (+ .json)  --[harness/input_replayer.py]-->  chobits_imu / chobits_features
    --> [REAL vins_fusion]  -->  chobits_server  --[bin/vio-pose-tap]-->  pose CSV
```

The estimator binary is `arm64` (it runs on the Pi). You have two ways to run it
off-vehicle: on a **native arm64 host** (a Pi 5 / any arm64 box — simplest, matches
production exactly) or on an **x86_64 host under `qemu-user`** (no hardware, works on
the analysis/notebook box). Both are documented below.

---

## What you need

- The fixture: `wave-<ts>.feat` + `wave-<ts>.feat.json` (from `bin/vio-ipc-record`; see
  [bench-capture.md](bench-capture.md)). The `.feat` extension keeps these raw estimator-input
  streams from colliding with ArduPilot FC `.bin` logs (#45).
- The estimator image `ghcr.io/symmatree/coordinator-vio-estimator` (public on GHCR).
- A `vins_fusion` config. Start from the seed `host/ansible/roles/coordinator/files/oak_d.yaml`,
  **with one change: `multiple_thread: 0`** (see "Determinism" below).
- `harness/input_replayer.py` and `bin/vio-pose-tap` from this repo (plain Python 3,
  no deps).

## Option A — native arm64 (Pi 5 or any arm64 host with Docker)

Bring up **only** the estimator (not the whole `bench` profile — otherwise the live
`vio-tracker` also writes the input sockets and corrupts the replay):

```sh
# real vins_fusion, input sockets bound, config with multiple_thread:0
docker run --rm -v /run/coord-ipc:/tmp -v "$PWD/oak_d.yaml:/config/oak_d.yaml:ro" \
    ghcr.io/symmatree/coordinator-vio-estimator:main &
# tap its pose output
COORDINATOR_IPC_DIR=/run/coord-ipc vio-pose-tap --out pose-<run>.csv &
# replay the captured inputs at real time
COORDINATOR_IPC_DIR=/run/coord-ipc python3 harness/input_replayer.py wave-<ts>.feat
```

## Option B — x86_64 under qemu-user (no hardware; the analysis box)

The analysis host has no container daemon but does have `qemu-aarch64-static`. Pull the
image rootfs daemonless and run the arm64 `vins_fusion` binary directly under qemu. Two
one-time setup steps, then the same three-process run as Option A.

### 1. Pull the arm64 rootfs (daemonless)

Anonymous GHCR pull of the `linux/arm64` manifest, extracting every layer into one flat
`rootfs/` (see `analysis/tools/pull_estimator_rootfs.py` in this repo, ~150 MB):

```sh
python3 analysis/tools/pull_estimator_rootfs.py --out rootfs/
```

### 2. Rehome absolute symlinks (the critical qemu-user gotcha)

Libraries like `liblapack.so.3` are **absolute** symlinks (`→ /etc/alternatives/…`).
`qemu-user` resolves absolute symlink targets against the **host** `/`, not the rootfs,
so the chain breaks (`liblapack.so.3: cannot open shared object file`). Repoint every
absolute symlink into the prefix (the `pull_estimator_rootfs.py` script does this with
`--fix-symlinks`, or run it standalone):

```sh
python3 analysis/tools/pull_estimator_rootfs.py --fix-symlinks rootfs/
```

### 3. Run

```sh
ROOT=$PWD/rootfs
mkdir -p /tmp/vins            # config output_path must exist
vio-pose-tap --out pose-<run>.csv &
QEMU_LD_PREFIX=$ROOT qemu-aarch64-static $ROOT/opt/coordinator/bin/vins_fusion oak_d.yaml &
python3 harness/input_replayer.py wave-<ts>.feat
```

`vins_fusion` binds `/tmp/chobits_*` on the **host** kernel (qemu passes the AF_UNIX
syscalls through), so the native-x86 `input_replayer` and `vio-pose-tap` interoperate
with the emulated estimator directly. Performance is **near real time** (~17 poses/s vs
~20 features/s input); a ~300 s capture replays in ~5–6 min.

**Is qemu faithful enough?** For the #42 question — comparing runs and comparing VINS to
the FC EKF — yes. The analysis is relative, and the pinned binary + libs are byte-for-byte
the deployed image; qemu emulates the NEON/FP the Ceres solver uses. If you need
production-exact numerics, use Option A.

---

## Pacing: replay at real time, NOT `--fast`

**Replay at real time (`input_replayer.py` default), never `--fast`.** The estimator's
socket loop reads **one IMU packet and one feature bundle per poll iteration**, so it
relies on the true ~5:1 IMU:feature *arrival* ratio (100 Hz vs 20 Hz) to fill the IMU
preintegration buffer before each image. `--fast` saturates both sockets, the loop drains
them 1:1, IMU integration is starved ~5×, and the pose diverges as soon as there is real
motion. Real-time pacing reproduces production's interleaving. Use `--speed 0.9` (slightly
under real time) so a transient qemu slowdown never lets the shallow AF_UNIX dgram queues
back up (which would recreate the 1:1 problem). Note the pose CSV is then on a 0.9×-scaled
clock; the comparison notebook rescales by `replay_speed` and re-aligns by motion
cross-correlation, so the absolute scale is not load-bearing.

## Determinism

Set **`multiple_thread: 0`** in the config. With the multi-threaded solver, Ceres
iteration counts and floating-point summation order make the pose non-reproducible
run-to-run; single-threaded, the same fixture yields the same trajectory.

## Expected behaviour and known failure mode

`vins_fusion` holds pose at the origin while the vehicle is stationary (idle on the ground
waiting for GPS), tracks translation once it moves, and — with the **seed** calibration —
**diverges on aggressive rotation** (a barrel roll by hand, or a hard yaw in flight): it
loses the visual constraint and the IMU dead-reckons away to kilometres. So the valid
comparison window is **from the start up to the first aggressive maneuver**, which the
comparison notebook detects from the FC attitude/rates and from where VINS velocity runs
away. Widening that window (better calibration, feature retention under motion) is the
open VIO-quality work.

## Related

- [bench-capture.md](bench-capture.md) — recording the fixture on the vehicle (#35)
- [bench-estimator.md](bench-estimator.md) — the on-vehicle estimator bench (#9)
- `harness/README.md` — the harness (router half + estimator half)
- `analysis/vio-ekf-comparison.ipynb` — EKF-vs-VIO pose comparison (#42)
