# Offline VIO replay: regenerating VINS pose from a captured fixture

> **Read first -- pick your execution path (updated 2026-07-27, #85 resolved).** The estimator
> image is **multi-arch** (CI builds amd64 + arm64 into one manifest), so on x86 there is **no
> reason to emulate**. Preference order: **Option C (in-cluster k8s pod/Job)** — native amd64,
> hermetic, NAS already mounted; then **Option B1 (native amd64 rootfs)** — daemonless x86, no
> qemu; then **Option B2 (arm64 under qemu)** — kept as the production-arch (Pi) cross-arch
> reference; then **Option A (native arm64 host)**. The qemu default was the stale bit #85 fixed:
> `pull_estimator_rootfs.py` now takes `--arch` (defaults to host arch → amd64 on x86). Native-vs-qemu
> faithfulness is **measured**, not assumed (E19 in `vio-quality-experiments.md`).
>
> Before following the manual steps, read:
> - Issues **#85** (native amd64 vs arm64-under-qemu; resolved by this doc + `--arch`),
>   **#35** (batch replay harness), **#42** (onboard capture), **#45** (`.feat` extension).
> - [`analysis/README.md`](../analysis/README.md) (how `vio-offline-runner` and the k8s
>   Job path actually work), [`analysis/sitl-validation-experiments.md`](../analysis/sitl-validation-experiments.md)
>   (LA1 fidelity anchor ~1um ARM->x86, LA6 all-up reconstruction, LB1 timing caveat),
>   [`analysis/vio-quality-experiments.md`](../analysis/vio-quality-experiments.md).

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

The estimator image is **multi-arch** (CI builds amd64 + arm64 into one manifest), so on an
x86 box there is no need to emulate. Ways to run it off-vehicle, in preference order:

- **Option C — in-cluster (recommended):** run the *image* as a k8s pod/Job on the (amd64)
  cluster. Native, hermetic, and the NAS `/mnt/flights` is already mounted (sibling of the
  flight-analysis CronJob). This is the path for batch replay over the NAS.
- **Option B1 — native amd64 rootfs (daemonless x86, no qemu):** unpack the native image and
  run the binary directly. The fast fallback when there is no container runtime *and* no cluster.
- **Option B2 — arm64 under qemu-user:** the production-arch (Pi) binary emulated on x86 — a
  cross-arch fidelity reference, not the default.
- **Option A — native arm64 host** (a Pi 5 / any arm64 box with Docker): simplest, matches
  production exactly.

All are documented below.

---

## What you need

- The fixture: a `.feat` + `.feat.json` pair, from either source (same format, consumed
  identically): a **bench** `wave-<ts>.feat` (estimator off, `bin/vio-ipc-record`; see
  [bench-capture.md](bench-capture.md)), or an **in-flight** `<node>_<sess>.feat` teed by the
  `vio-tracker` overlay with the estimator running (#78). The `.feat` extension keeps these raw
  estimator-input streams from colliding with ArduPilot FC `.bin` logs (#45).
- The estimator image `ghcr.io/symmatree/coordinator-vio-estimator` (public on GHCR).
- A `vins_fusion` config. Start from the seed `host/ansible/roles/coordinator/files/oak_d.yaml`,
  **with one change: `multiple_thread: 0`** (see "Determinism" below).
- `harness/input_replayer.py` and `bin/vio-pose-tap` from this repo (plain Python 3,
  no deps). **Options A/B only** — the in-cluster path (C) uses the socket-free offline binary
  and needs no replayer.

## Option C — in-cluster k8s Job (recommended; native amd64, no qemu)

The cluster is amd64, so a pod pulls the **native** image from the multi-arch manifest — no
qemu, no rootfs unpack, no daemon on the notebook. The NAS `flights` share is already bound
as PVC `flight-analysis-flights` (the flight-analysis CronJob's volume), so a Job can read
every `.feat` and write results next to it. This uses the **socket-free deterministic**
entrypoint `vio-offline-runner` (`containers/vio-estimator/offline_runner.py`) — no
`input_replayer`/`vio-pose-tap`, no pacing to worry about; it walks `/mnt/flights`, writes
`<stem>.vinspose.csv` + a provenance sidecar, and skips fixtures already fresh.

A minimal one-off Job (config supplied via ConfigMap from the seed `oak_d.yaml`):

```sh
kubectl -n flight-analysis create configmap vio-oakd \
  --from-file=oak_d.yaml=host/ansible/roles/coordinator/files/oak_d.yaml --dry-run=client -o yaml | kubectl apply -f -
```
```yaml
apiVersion: batch/v1
kind: Job
metadata: { name: vio-offline, namespace: flight-analysis }
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: runner
          image: ghcr.io/symmatree/coordinator-vio-estimator:main
          command: ["/opt/coordinator/bin/vio-offline-runner"]   # walk /mnt/flights; or pass explicit fixtures
          env: [{ name: VINS_CONFIG, value: /config/oak_d.yaml }]
          volumeMounts:
            - { name: flights, mountPath: /mnt/flights }
            - { name: cfg, mountPath: /config }
      volumes:
        - { name: flights, persistentVolumeClaim: { claimName: flight-analysis-flights } }
        - { name: cfg, configMap: { name: vio-oakd } }
```

Watch it: `kubectl -n flight-analysis logs -f job/vio-offline`. Results land on the NAS
alongside each `.feat`. For a **single fixture / hyperparameter sweep** instead of the whole
share, `analysis/tools/vio_param_sweep.py` drives the same `vins_fusion_offline` binary via
`docker run` on a bench box with a daemon.

> **Durable path (follow-up, lives in `tiles`).** The standing version of this — a CronJob
> sibling of the flight-analysis one (schedule + provenance + image-digest stamping) — belongs
> in `tiles/tanka/environments/`, not here, per the repo split in `analysis/README.md`. The
> Job above is the validated ad-hoc trigger; the CronJob is a separate `tiles` PR.

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

## Option B — daemonless rootfs on an x86 box (no container runtime)

When there is no container runtime *and* no cluster to run in (Option C), unpack the image
rootfs and run the binary directly. `pull_estimator_rootfs.py` does the daemonless pull
(anonymous GHCR, reads the OCI index, extracts every layer into one flat `rootfs/`, ~150 MB)
and picks the arch with `--arch` (default: **host arch**, so amd64 on x86):

```sh
# native amd64 (x86 host): no qemu -- the fast default
python3 analysis/tools/pull_estimator_rootfs.py --arch amd64 --out rootfs/
# arm64 (the Pi's production binary), for the qemu cross-arch check
python3 analysis/tools/pull_estimator_rootfs.py --arch arm64 --out rootfs-arm64/
```

**Rehome absolute symlinks (shared gotcha, both arches).** Libraries like `liblapack.so.3`
are **absolute** symlinks (`→ /etc/alternatives/…`). The loader resolves an absolute symlink
target against the **process root** (`/`), not the rootfs — native *and* under qemu — so the
chain breaks (`liblapack.so.3: cannot open shared object file`). The pull rehomes them into
the prefix automatically; re-run standalone if needed (idempotent):

```sh
python3 analysis/tools/pull_estimator_rootfs.py --fix-symlinks rootfs/
```

### B1 — native amd64 (no qemu)

Run the amd64 binary through the rootfs's own loader (hermetic: image glibc + libs, not the
host's), so no chroot/root is needed:

```sh
ROOT=$PWD/rootfs
mkdir -p /tmp/vins            # config output_path must exist
LOADER="$ROOT/lib64/ld-linux-x86-64.so.2"
LIBS="$ROOT/usr/lib/x86_64-linux-gnu:$ROOT/lib/x86_64-linux-gnu"
vio-pose-tap --out pose-<run>.csv &
"$LOADER" --library-path "$LIBS" $ROOT/opt/coordinator/bin/vins_fusion oak_d.yaml &
python3 harness/input_replayer.py wave-<ts>.feat
```

### B2 — arm64 under qemu-user (cross-arch reference)

Same three-process run, arm64 binary emulated (`ROOT` = the `rootfs-arm64/` from `--arch arm64`):

```sh
ROOT=$PWD/rootfs-arm64
mkdir -p /tmp/vins
vio-pose-tap --out pose-<run>.csv &
QEMU_LD_PREFIX=$ROOT qemu-aarch64-static $ROOT/opt/coordinator/bin/vins_fusion oak_d.yaml &
python3 harness/input_replayer.py wave-<ts>.feat
```

`vins_fusion` binds `/tmp/chobits_*` on the **host** kernel (qemu passes the AF_UNIX syscalls
through), so the native-x86 `input_replayer` and `vio-pose-tap` interoperate with the emulated
estimator directly. Performance is **near real time** (~17 poses/s vs ~20 features/s input); a
~300 s capture replays in ~5–6 min.

**Is qemu (B2) faithful enough vs native (B1/C)?** Measured, not assumed (E19 in
`analysis/vio-quality-experiments.md`): for the deterministic offline binary on the same image,
native-amd64 and arm64-under-qemu agree to **sub-mm on a bounded run** (0.3 mm median, 2.3 mm
max position; 0.06° attitude over a 3.25 m trajectory). They only split once the stereo-only
pose runs away mid-flight (the `vins-stereo-only` seed-calibration limit -- on 260712 ~88 s after
takeoff, well before the actual crash), staying within ~2 mm for the first ~75% before that
ill-conditioned tail amplifies the FP difference to tens of metres — a region where neither arch
is ground truth. So the
diverge-vs-stays-bounded verdict is **arch-invariant**; for the #42 relative analysis the
cross-arch difference is well inside the noise. Native is also ~**14× faster** than qemu (25 s vs
343 s for a 300 s fixture). If you need production-exact numerics, use Option A (a real Pi) —
qemu emulates, it is not the Pi's silicon.

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
