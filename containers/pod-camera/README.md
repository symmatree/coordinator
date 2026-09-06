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

## ADXL345 vibration logging (#211)

`adxl345.py` reads one or more ADXL345s over SPI and writes batched samples as JSONL
into the **same session directory** as the frames, on the **same kernel clock** -- which
is the whole point: accelerometer and camera share one host, so correlating them needs
no NTP, no PPS, and no network.

Opt-in via `POD_ACCEL_DEVICES` (empty disables), supervised separately from the camera
loop so a missing sensor or an unset `dtparam=spi=on` cannot cost you the frames.

| Var | Default | Meaning |
|-----|---------|---------|
| `POD_ACCEL_DEVICES` | *(empty)* | `label:/dev/spidevN.M,...`; empty = off |
| `POD_ACCEL_ODR_HZ` | `3200` | output data rate |
| `POD_ACCEL_RANGE_G` | `16` | 2 \| 4 \| 8 \| 16 |
| `POD_ACCEL_SPI_HZ` | `1500000` | SPI clock |
| `POD_ACCEL_POLL_HZ` | `200` | FIFO poll rate |
| `POD_ACCEL_SEPARATION_M` | *(empty)* | camera-to-arm baseline, recorded in the header |

### Why spidev and not the IIO driver

Checked, not assumed. There **is** a stock `i2c-sensor,adxl345` overlay, and the mainline
IIO driver gained FIFO + watermark support in kernel 6.16 -- but **Pi OS does not ship
it**. The Raspberry Pi archive's `Contents-arm64` lists only
`drivers/input/misc/adxl34x*.ko` (the legacy *input* driver: `/dev/input` events, no IIO
buffer) for every kernel through 6.18.39, and `CONFIG_ADXL345_SPI` appears in no bcm27xx
defconfig. There is no SPI overlay for the part, and generic `anyspi` cannot express
`spi-cpol`/`spi-cpha` (this part is mode 3) or `interrupt-names` -- without which the IIO
driver forces `FIFO_BYPASS` and exposes no buffer. Even with the module built it pushes
X/Y/Z with **no `IIO_TIMESTAMP` channel**, so the timestamps would still be ours to make.

Consequence worth stating plainly: **the INT wire is not needed.** Two fewer conductors
through the camera sandwich.

### Settings that are not arbitrary

| Choice | Why |
|---|---|
| **mode 3, <= 1.5 MHz** | Datasheet: CPOL=1/CPHA=1, 5 MHz ceiling. Separately, 5 us must elapse between FIFO sample reads, and *"for SPI operation at 1.6 MHz or less, the register addressing portion of the transmission is a sufficient delay"* -- so staying under 1.6 MHz removes the CS-deassert dance entirely. A 32-sample drain is 1.2 ms; the FIFO takes 10 ms to fill. |
| **ODR 3200** | The part's native internal rate, so nothing is decimated on the way out. Lower ODRs decimate through a filter the datasheet does not characterise, adding aliasing you cannot separate afterwards. |
| **+/-16 g** | In FULL_RES the scale factor is a constant **3.9 mg/LSB at every range** -- bit depth grows instead (10 bits at 2 g, 13 at 16 g). The wide range is free, and the design doc's own "tens of microns at ~330 Hz" works out to **4-9 g**, so clipping is live. Check the data for rail-hits; do not change parts pre-emptively. |
| **Batch timestamps, raw LSB** | Each FIFO batch carries `CLOCK_BOOTTIME` (matching the camera's `SensorTimestamp`) and `CLOCK_MONOTONIC`, plus the cumulative sample index. Per-sample times are **not** computed here: the datasheet specifies no tolerance on the part's internal clock, so the effective ODR is fitted offline by regressing index against batch time. Baking in a nominal rate would lose the evidence needed to correct it. |
| **Self-test at startup** | Electrostatically actuates the sensor and checks the deflection lands inside the datasheet's per-axis g limits. Catches a cold joint or dead part before the airframe leaves the ground, rather than after a flight of zeros. Logged pass/fail with the measured deltas; a failure warns and keeps logging -- suspect data beats no data. |

### Why two sensors, and why the separation matters

One pixel of image shift on the CM3 is **282 urad** of camera rotation but **705 um** of
camera *translation* at a 2.5 m hover -- rotation is roughly a thousand times more
efficient at writing rolling-shutter banding. A single accelerometer senses rotation only
through its lever arm and under-reads it. Differential acceleration across a known
baseline is the rotational signature:

    theta = (delta_a / d) / (2*pi*f)^2

At 120 Hz over a 150 mm baseline, 1 g of differential acceleration is 115 urad, or
**0.41 px** of image shift. Which is why `POD_ACCEL_SEPARATION_M` needs a measured number
and "near the end of the arm" will not do.

### Output

`/captures/<node>/<session>/accel-<label>.jsonl` -- one header record (device, ODR,
range, scale, self-test result, separation), then one record per FIFO batch:

```json
{"t":"b","i":320,"boot_ns":...,"mono_ns":...,"n":32,"ovr":false,"x":[...],"y":[...],"z":[...]}
```

Append-only and flushed per record, `fsync` at 1 Hz -- the `.feat` lesson from #89: a
power cut costs one record, not the file. At 3200 Hz with two sensors that is roughly
120 kB/s, about 7 MB per flight minute.

## Runtime

```bash
# On the Zero, after host bootstrap (./host/one_time.sh pod).
# stacks/pod/.env ships COMPOSE_PROFILES=capture, so this just works:
coord pull
coord start
coord logs -f pod-camera     # expect: "capture: node=... size=4608x2592 hz=1.0 ..."
ls /var/lib/pod/captures/    # frames accumulating under <node>/<session>/
```
