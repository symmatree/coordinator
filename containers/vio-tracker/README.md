# coordinator-vio-tracker

OAK-D `feature_tracker` from [chobitsfan/oak_d_vins_cpp](https://github.com/chobitsfan/oak_d_vins_cpp) (`apm_wiki`), built against [depthai-core](https://github.com/luxonis/depthai-core) v2.25.0 per upstream README.

Pinned refs: `upstream.lock`.

## Image contents

- `/opt/coordinator/bin/feature_tracker` -- mono/stereo feature tracking, IMU, disparity; publishes Unix dgram sockets under `/tmp/chobits_*`
- depthai runtime libraries under `/opt/depthai/lib`

## Build

On a Pi 4B (native arm64, fastest):

```bash
docker build -t ghcr.io/symmatree/coordinator-vio-tracker:local containers/vio-tracker
```

From a cross-build host:

```bash
docker buildx build --platform linux/arm64 \
  -t ghcr.io/symmatree/coordinator-vio-tracker:local \
  containers/vio-tracker
```

CI publishes to `ghcr.io/symmatree/coordinator-vio-tracker` on push to `main` and on manual dispatch.

## Run (smoke)

```bash
docker run --rm -it --privileged \
  -v /dev/bus/usb:/dev/bus/usb \
  -v /var/lib/coordinator/ipc:/tmp \
  ghcr.io/symmatree/coordinator-vio-tracker:local
```

Healthy output includes USB speed, device name (`OAK-D` or `OAK-D-Pro`), `imu ok`, and periodic `N features` lines.

## Stack

Use the `tracker` compose profile (see [docs/bench-tracker.md](../../docs/bench-tracker.md)):

```bash
coord start
coord logs -f vio-tracker
```
