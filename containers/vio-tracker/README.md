# coordinator-vio-tracker

OAK-D `feature_tracker` from [chobitsfan/oak_d_vins_cpp](https://github.com/chobitsfan/oak_d_vins_cpp) (`apm_wiki`), built against [depthai-core](https://github.com/luxonis/depthai-core) v2.25.0 per upstream README.

Pinned refs: `upstream.lock`. Our change to `feature_tracker.cpp` is an **overlay** (`overlay/oak_d_vins_cpp/feature_tracker.cpp`, COPYed over the pinned clone) — kept byte-diffable vs upstream.

## Capture ([#72](https://github.com/symmatree/coordinator/issues/72))

The overlay adds **opt-in capture, concurrent with VIO**: periodic **disparity** frames (PNG — the frame already computed in-pipeline) and **RGB stills** (a `ColorCamera` still branch on the same pipeline, host-encoded JPEG), each with a JSON sidecar (wall/monotonic + **device sensor timestamp**, so they're time-aligned with the VIO features/pose by the same device clock). Enabled when `OAK_CAPTURE_DIR` is set (it is, in `.env`, mounted at `/captures`); **unset it and the tracker is byte-for-byte upstream behaviour** — no color camera, no disk writes. Cadence/quality: `OAK_DISPARITY_HZ` (1.0), `OAK_STILL_HZ` (0.2), `OAK_STILL_RESOLUTION` (12mp), `OAK_JPEG_QUALITY` (92). The standalone `oak-still-capture` container is the VIO-*off* case.

Also folds in the `UsbSpeed::HIGH` change (was a Dockerfile `sed`) and adds `SIGTERM` handling for clean `docker stop`.

## Image contents

- `/opt/coordinator/bin/feature_tracker` -- mono/stereo feature tracking, IMU, disparity, opt-in disparity/still capture; publishes Unix dgram sockets under `/tmp/chobits_*`
- depthai runtime libraries under `/opt/depthai/lib`
- `dumb-init` as PID 1; entrypoint clears stale `chobits_*` socket files then execs `feature_tracker`

## Dockerfile

Multi-stage build: compile depthai-core and `feature_tracker` in a builder image; runtime image is `debian:bookworm-slim` with OpenCV and depthai libs copied in. Target platform is **linux/arm64** (Pi 4B hub).

## Local build

From the repo root, same architecture as the Docker daemon (arm64 image on an arm64 builder; cross-build on amd64 needs host arm64 emulation -- without it, `RUN` steps fail with `exec /bin/sh: exec format error`):

```bash
docker build -t ghcr.io/symmatree/coordinator-vio-tracker:local containers/vio-tracker
```

**amd64 host cross-build** (WSL/Ubuntu): `qemu-user-static` and `binfmt-support` on the host (dotfiles `install-tools.ansible.yaml`), a `docker-container` buildx builder, then:

```bash
docker buildx build --platform linux/arm64 --load \
  -t ghcr.io/symmatree/coordinator-vio-tracker:local \
  containers/vio-tracker
```

## CI / GHCR

`.github/workflows/build-vio-tracker.yaml` builds natively on `ubuntu-24.04-arm` and pushes to `ghcr.io/symmatree/coordinator-vio-tracker` on push to `main`.

## Compose

Service name: `vio-tracker` in `stacks/coordinator/compose.yaml` (`tracker` profile). Stack layout and bench steps: [docs/bench-tracker.md](../../docs/bench-tracker.md).
