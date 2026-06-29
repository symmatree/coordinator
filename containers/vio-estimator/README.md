# coordinator-vio-estimator

`vins_fusion` from [chobitsfan/VINS-Fusion](https://github.com/chobitsfan/VINS-Fusion) (`apm_wiki`) -- the VIO estimator that consumes `vio-tracker`'s IMU + feature streams and publishes pose.

Pinned ref: `upstream.lock`.

## No ROS

The `apm_wiki` branch builds the `vins_estimator` subproject as **plain CMake**: it ships a `fake_ros.h` shim and a socket `main.cpp`, producing the `vins_fusion` binary linked against OpenCV4 + Ceres + Eigen. The `docker/Dockerfile` in that repo is the legacy ROS-kinetic catkin path and is **not** used. `camera_models` (the only roscpp-dependent package) is intentionally not built -- the camera model lives in `feature_tracker`, so features arrive already normalized.

## Image contents

- `/opt/coordinator/bin/vins_fusion` -- VINS-Fusion estimator
- `dumb-init` as PID 1; entrypoint clears stale `chobits_*` sockets, ensures the output dir, then execs `vins_fusion /config/oak_d.yaml` under `stdbuf -oL` (line-buffered logs)

## IPC contract

| Socket | Direction | Payload |
|--------|-----------|---------|
| `/tmp/chobits_imu` | tracker -> estimator (bind here) | IMU packets |
| `/tmp/chobits_features` | tracker -> estimator (bind here) | feature bundles |
| `/tmp/chobits_server` | estimator -> consumer | `float[10]`: quat(w,x,y,z) + pos(x,y,z) + vel(x,y,z) |

All under the shared `${COORDINATOR_IPC_DIR}:/tmp` mount. (`vins_fusion` also offers an opt-in UDP debug feed on port 8800: send it a datagram and it streams odometry back to the sender.)

## Config

`vins_fusion` requires a config path as `argv[1]`; the entrypoint passes `/config/oak_d.yaml` (override with `VINS_CONFIG`). The calibration is seeded by the Ansible coordinator role (`host/ansible/roles/coordinator/files/oak_d.yaml`) into `/var/lib/coordinator/config/` and is meant to be refined from flight -- see [docs/bench-estimator.md](../../docs/bench-estimator.md).

## CI / GHCR

`.github/workflows/build-vio-estimator.yaml` builds natively on `ubuntu-24.04-arm` and pushes to `ghcr.io/symmatree/coordinator-vio-estimator` on push to `main`.

## Compose

Service name: `vio-estimator` in `stacks/coordinator/compose.yaml` (`bench` and `flight` profiles). Bench steps: [docs/bench-estimator.md](../../docs/bench-estimator.md).
