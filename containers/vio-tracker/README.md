# coordinator-vio-tracker

OAK-D `feature_tracker` from [chobitsfan/oak_d_vins_cpp](https://github.com/chobitsfan/oak_d_vins_cpp) (`apm_wiki`), built against [depthai-core](https://github.com/luxonis/depthai-core) v2.25.0 per upstream README.

Pinned refs: `upstream.lock`.

## Image contents

- `/opt/coordinator/bin/feature_tracker` -- mono/stereo feature tracking, IMU, disparity; publishes Unix dgram sockets under `/tmp/chobits_*`
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

`.github/workflows/build-vio-tracker.yaml` builds `linux/arm64` on push to `main` and pushes to `ghcr.io/symmatree/coordinator-vio-tracker`. The workflow uses `setup-qemu-action` on the Actions runner.

## Compose

Service name: `vio-tracker` in `stacks/coordinator/compose.yaml` (`tracker` profile). Stack layout and bench steps: [docs/bench-tracker.md](../../docs/bench-tracker.md).
