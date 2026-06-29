# coordinator-pod-camera

Capture container for the Rekon camera pod (Pi Zero 2 W + Camera Module 3 / IMX708). Pulls stills at 1--2 Hz and writes JPEG + timestamp metadata to the Zero's **local SD card** (never over USB -- the USB 2.0 bus is for commands only; see fables `arm-pods.md`).

**Status: Phase 2, not yet built.** This directory is a placeholder. The Dockerfile, capture program, and the GHCR build workflow land in the Phase 2 issue. Plan of record: [docs/pi-zero-bringup.md](../../docs/pi-zero-bringup.md).

## To be decided when building (Phase 2)

| Decision | Options / notes |
|----------|-----------------|
| Capture stack | `picamera2` (Python; easiest to extend for cadence and the Phase 4 control API) vs. `rpicam-apps` (`rpicam-still` in a loop; smaller image). **Deciding factor: frame-sync exposure** (next row), not device passthrough — passthrough is the same for both. |
| Frame sync (the oddball bit) | The CM3 has no XVS hardware trigger, so the array uses libcamera's **software camera-sync**: one Zero is the **pacesetter/server**, the rest are clients that align frame timing; sync messages ride the USB net (fables `arm-pods.md`; "<10 us" per libcamera). The front-end that cleanest exposes this sync mode + the server/client role + the resulting per-frame timestamp metadata wins. My understanding (confirm when building): `rpicam-apps` surfaces it as `--sync server\|client`; `picamera2` via libcamera sync controls (`SyncMode` server/client + `SyncReady`/timer metadata). |
| Camera passthrough | Common to both front-ends: `privileged: true` or explicit devices (`/dev/video*`, `/dev/media*`, `/dev/dma_heap`, vchiq) + `/run/udev`. Wire into `stacks/pod/compose.yaml`. |
| Timestamping | `CLOCK_MONOTONIC` per fables, written as a sidecar for later PPK-style interpolation against ArduPilot pose logs. |
| On-disk layout | Under `/captures` (bind of `/var/lib/pod/captures`); file naming TBD. |
| Build | arm64 in CI (`.github/workflows/build-pod-camera.yaml`), pulled on the Zero -- never built on the Zero. Mirror `build-vio-tracker.yaml`. |

## Runtime (target)

```bash
# On the Zero, after Phase 1 host bootstrap:
#   COMPOSE_PROFILES=capture in stacks/pod/.env
coord pull
coord start
coord logs -f pod-camera     # expect frames accumulating in /var/lib/pod/captures
```
