# coordinator-pod-camera

Capture container for the Rekon camera pod (Pi Zero 2 W + Camera Module 3 / IMX708). Pulls JPEG stills at a fixed cadence (default 1 Hz) and writes each frame plus a JSON metadata sidecar to the Zero's **local SD card** (never over USB -- the USB 2.0 bus is for commands only; see [`arm-pods.md`](../../docs/rekon10/arm-pods.md)).

**Status: built, pending hardware bring-up (#23).** Image + CI exist; not yet run on a real Zero + camera. Plan of record: [docs/pi-zero-bringup.md](../../docs/pi-zero-bringup.md).

## What it does

- `capture.py` runs picamera2 + libcamera, captures stills at `POD_CAPTURE_HZ`, and writes `<stem>.jpg` + `<stem>.json` under `/captures/<node>/<session>/`.
- The sidecar records `sensor_timestamp_ns` (libcamera `SensorTimestamp`, CLOCK_BOOTTIME at exposure) plus wall-clock and monotonic time -- the anchor for later PPK-style interpolation against ArduPilot pose logs. Georeferencing comes from GNSS, not here.
- Clean shutdown on SIGTERM/SIGINT so `coord stop` / `docker stop` finishes the in-flight frame and stops the camera.

## Rolling shutter: turning a band pitch into a frequency

The IMX708 full-resolution mode is a **68 ms readout**, not the ~26 ms that older notes
carried -- 26.29 us is the *line* time, and the two got conflated. From the `imx708` kernel
driver, mode `4608x2592`: `line_length_pix = 0x3d20` (15648), `pixel_rate = 595200000`.

| | |
|---|---|
| line time | `15648 / 595200000` = **26.29 us** |
| readout, 2592 rows | **68.1 ms** |
| max frame rate (`vblank_default=58`) | 14.35 fps -- the published CM3 figure, which cross-checks the arithmetic |

So a jello band pitch of *R* rows is `f = 1 / (R x 26.29 us)`:

| line | band pitch | bands across the frame |
|---|---|---|
| ~120 Hz motor rev (E24) | ~317 rows | ~8 |
| 290-360 Hz blade-pass | 106-131 rows | 20-24 |

For reference the OAK-D's IMX378 reads out in 33 ms over 3040 rows (10.9 us line time), so
the same vibration writes **2.4x more bands** here. Binning is a lever if it ever matters:
`2304x1296` drops the line time to 13.36 us and the readout to 17.3 ms.

## Design choices

| Choice | Notes |
|--------|-------|
| Front-end: **picamera2** | Picked over `rpicam-apps` for frame-sync exposure (see below) and easy extension to the Phase 4 control API. |
| Base image | `debian:bookworm-slim` + the **Raspberry Pi apt archive** (`archive.raspberrypi.com`) for matched, Pi-pipeline-aware libcamera + `python3-picamera2`. Stock Debian libcamera enumerates "no cameras" -- the one real container gotcha. Keep `RPI_SUITE` aligned with the host Pi OS release. |
| Camera passthrough | `privileged: true` + `/run/udev` (in `stacks/pod/compose.yaml`); fall back to explicit device mounts if enumeration fails. |
| Frame sync (the oddball bit) | The CM3 has no XVS hardware trigger, so multi-pod alignment uses libcamera **software sync** (one server/pacesetter, the rest clients). `capture.py` has a guarded `POD_SYNC_MODE` hook, **default off** -- the exact picamera2 control surface (`SyncMode` server/client) is not hardware-verified, so a wrong control logs a warning instead of crashing. Wired properly in Phase 3 (#24); standalone capture is unaffected. |
| Exposure cap | libcamera has no max-AE-exposure control, and `FrameDurationLimits` can't stand in (the 12 MP mode's minimum frame duration is already ~70 ms). The lever is the exposure/gain split from libcamera 0.4: `ExposureTimeMode=Manual` + `ExposureTime` pins the shutter while `AnalogueGainMode=Auto` lets the AEGC make up the light in gain. Best-effort -- unsupported means a warning, not a crash, and the sidecar records what the sensor actually did. |
| Focus units | `LensPosition` is **dioptres** (1/metres): `0.0` infinity, `0.5` = 2 m, `2.0` = 0.5 m. Deliberately *not* the OAK-D's 0-255 VCM scale -- `OAK_STILL_FOCUS=125` would ask for 8 mm here. Default `auto` because an uncalibrated fixed position is worse than AF (T10). |
| Build | arm64 in CI ([`.github/workflows/build-pod-camera.yaml`](../../.github/workflows/build-pod-camera.yaml)), pulled on the Zero -- never built on the Zero. |

## Config (env, via `stacks/pod/.env`)

| Var | Default | Meaning |
|-----|---------|---------|
| `POD_NODE_NAME` | hostname | label in filenames + metadata |
| `POD_CAPTURE_DIR` | `/captures` | output dir (bind of `/var/lib/pod/captures`) |
| `POD_CAPTURE_HZ` | `1.0` | captures per second |
| `POD_CAPTURE_WIDTH` / `_HEIGHT` | `0` | `0` = sensor full resolution (4608x2592) |
| `POD_JPEG_QUALITY` | `90` | JPEG quality 1-100 |
| `POD_STILL_MAX_EXPOSURE_US` | `5000` | caps the shutter; `0` = uncapped AE |
| `POD_STILL_FOCUS` | `auto` | `auto` \| `infinity` \| lens position in **dioptres** |
| `POD_SYNC_MODE` | `off` | `off` \| `server` \| `client` (Phase 3) |

## Runtime

```bash
# On the Zero, after host bootstrap (./host/one_time.sh pod).
# stacks/pod/.env ships COMPOSE_PROFILES=capture, so this just works:
coord pull
coord start
coord logs -f pod-camera     # expect: "capture: node=... size=4608x2592 hz=1.0 ..."
ls /var/lib/pod/captures/    # frames accumulating under <node>/<session>/
```
