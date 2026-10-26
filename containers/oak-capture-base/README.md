# coordinator-oak-capture-base

Pinned build foundation for the OAK capture app: a standalone C++ process that records the
OAK's colour camera, both mono cameras and its IMU. arm64, for the OAK on the coordinator
(Pi 4B) now and the OAK-D S2 later, on the coordinator or a Radxa board.

It carries:

- `debian:trixie-slim` **pinned by digest** (refreshed by `containers/pin-base-digests.sh`),
- gcc 14, CMake and Ninja,
- **depthai-core v3** built shared at `/opt/depthai`, pinned in `upstream.lock`.

The app's builder stage `FROM`s this image by digest and its runtime stage copies
`/opt/depthai`, the split described in [../README.md](../README.md).

## depthai-core v3

v3 supports RVC2 devices over USB, which covers both the original OAK-D (`BW1098OBC`) and the
OAK-D S2. On a USB OAK the device firmware ships inside depthai-core, so the pin in
`upstream.lock` is also the camera's firmware version.

Its dependencies come from depthai's own vcpkg manifest (`vcpkg.json`, pinned to a vcpkg
baseline), built from source on the first build of this image. Features the capture app does
not use are configured off in the Dockerfile, OpenCV among them: JPEG is encoded on the camera.

## Cadence

Rebuilds only when this directory changes: a base-digest bump, the apt list, or the depthai pin.
A rebuild becomes a `FROM ...@sha256:` bump in the app, never an implicit move.
