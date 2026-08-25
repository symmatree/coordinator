# coordinator-vio-tracker

OAK-D `feature_tracker` from [chobitsfan/oak_d_vins_cpp](https://github.com/chobitsfan/oak_d_vins_cpp) (`apm_wiki`), built against [depthai-core](https://github.com/luxonis/depthai-core) v2.25.0 per upstream README.

Pinned refs: `upstream.lock`. Our change to `feature_tracker.cpp` is an **overlay** (`overlay/oak_d_vins_cpp/feature_tracker.cpp`, COPYed over the pinned clone) — kept byte-diffable vs upstream.

## Capture ([#72](https://github.com/symmatree/coordinator/issues/72))

The overlay adds **opt-in capture, concurrent with VIO**: periodic **disparity** frames (PNG — the frame already computed in-pipeline) and **RGB stills** (a `ColorCamera` still branch on the same pipeline, host-encoded JPEG), each with a JSON sidecar (wall/monotonic + **device sensor timestamp**, so they're time-aligned with the VIO features/pose by the same device clock). Enabled when `OAK_CAPTURE_DIR` is set (it is, in `.env`, mounted at `/captures`); **unset it and the tracker is byte-for-byte upstream behaviour** — no color camera, no disk writes. Cadence/quality: `OAK_DISPARITY_HZ` (1.0), `OAK_STILL_HZ` (0.2), `OAK_STILL_RESOLUTION` (12mp), `OAK_JPEG_QUALITY` (92). **Mono VIO-input capture (#125):** `OAK_MONO_HZ` (1.0, `0` disables) also saves the **rectified-left** frame (PNG) — the exact image the left feature tracker consumes, fanned out from `depth->rectifiedLeft`, host-sampled at that rate (~the disparity stream's load on this USB2 link) — so a feature-starvation can be tied to what the camera actually saw (the color still is a different sensor). `OAK_MONO_MAX_EXPOSURE_US` (`0` = no cap) caps the **mono** auto-exposure shutter so AE trades to gain instead of a blurring long exposure in low light — this changes the VIO input itself (distinct from the color-still cap), so bench-verify feature tracking before flying a value. **Still image quality** (motion blur was the killer in flight — auto-exposure ran to ~30 ms @ ISO 110): `OAK_STILL_MAX_EXPOSURE_US` caps the shutter so auto-exposure trades to ISO/gain instead (`0` = no cap); `OAK_STILL_FOCUS` fixes the lens (AF off). The scale is **not** far-to-near: on IMX378, `255` is macro at 8 cm, **`~120`–`130` is infinite focus** (varies module to module), and *lower* values run the lens too close to the sensor array and back out of focus (`depthai` `CameraControl.hpp`). `auto` = leave AF on. This stops AF *hunting* only — the lens stays VCM-suspended, and Luxonis document that under high vibration "even if you set manual focus, the AF coil (which holds the lens) won't be able to keep the lens in place"; a glued fixed-focus module is the mechanical fix. The standalone `oak-still-capture` container is the VIO-*off* case.

Also folds in the `UsbSpeed::HIGH` change (was a Dockerfile `sed`) and adds `SIGTERM` handling for clean `docker stop`.

## Input tee ([#78](https://github.com/symmatree/coordinator/issues/78))

Under the **same `OAK_CAPTURE_DIR` gate and session dir**, the overlay also **tees the estimator's raw input datagrams** -- the `chobits_imu` + `chobits_features` streams -- to `<session>/<node>_<sess>.feat` (+ a `.feat.json` manifest), in the **exact framed format `bin/vio-ipc-record` writes** (`<ddHI>` little-endian: `t_mono, t_unix, socket_id, length`, then the raw payload). Because the tracker is the *sender*, this needs no socket bind and runs **with the estimator live** -- so an armed flight yields a fixture replayable through the real `vins_fusion` offline (`harness/input_replayer.py`, [docs/vio-offline-replay.md](../../docs/vio-offline-replay.md)), exactly like a bench `wave-*.feat`. This is the **armed counterpart to [#42](https://github.com/symmatree/coordinator/issues/42)** (which records the same streams with the estimator *off*, so it needs no tee). Unset `OAK_CAPTURE_DIR` and no `.feat` is written.

## Image contents

- `/opt/coordinator/bin/feature_tracker` -- mono/stereo feature tracking, IMU, disparity, opt-in disparity/still capture; publishes Unix dgram sockets under `/tmp/chobits_*`
- depthai runtime libraries under `/opt/depthai/lib`
- `dumb-init` as PID 1; entrypoint clears stale `chobits_*` socket files then execs `feature_tracker`

## Dockerfile

Multi-stage build over two pinned base images ([containers/README.md](../README.md), [#145](https://github.com/symmatree/coordinator/issues/145)): the builder is [`coordinator-vio-tracker-base`](../vio-tracker-base/) (Debian + toolchain + a prebuilt depthai-core at `/opt/depthai`), which compiles `feature_tracker` with our overlay; the runtime is [`coordinator-vio-runtime-base`](../vio-runtime-base/) (the shared OpenCV runtime) plus the tracker-only extras (`libglib2.0-0`, `libopencv-imgcodecs406`, `libusb-1.0-0`, `udev`) and the depthai libs copied in. The heavy layers (depthai, OpenCV) live in the pinned bases, so an app change re-pulls only the small `feature_tracker` layer. Target platform is **linux/arm64** (Pi 4B hub).

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
