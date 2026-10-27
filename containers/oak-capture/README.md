# coordinator-oak-capture

Colour stills, both mono cameras and the IMU from one OAK, in one C++ process. arm64: the
original OAK-D on the coordinator (Pi 4B) now, the OAK-D S2 later. Built on
[`oak-capture-base`](../oak-capture-base/) (depthai-core v3); deployed by
[`stacks/coordinator/oak-capture.container`](../../stacks/coordinator/oak-capture.container).

One process because the OAK cannot be shared: whoever opens it owns every sensor on it.

## What it records

The three cameras run at one rate (`OAK_FPS`, 2 Hz), so each colour frame has a left and a
right frame from the same moment. The monos are global-shutter; the colour sensor is rolling,
so a shadow line that is straight in a mono frame and bent in the colour frame is the
vibration measurement.

| Stream | Source | Written as |
|--------|--------|------------|
| colour | `CAM_A`, full sensor resolution, JPEG-encoded on the OAK | `<MxId>_color_<seq>_<utc>.jpg` |
| left, right | `CAM_B`, `CAM_C`, full resolution, 8-bit | `<MxId>_left_<seq>_<utc>.pgm`, `..._right_...` |
| IMU | the OAK's own, `ACCELEROMETER_RAW` + `GYROSCOPE_RAW` | `<MxId>_<session>.imu.jsonl` |
| every frame retrieved, kept or not | | `<MxId>_<session>.frames.jsonl` |

Each image has a JSON sidecar with the same stem. Everything goes into the session directory
the router also writes, `captures/<node>/<boot-id>/`, flat, which is the shape
`analysis/coordinator_captures.py` indexes.

**Exposure is a ceiling, not a fixed time.** DepthAI offers manual exposure (time and ISO
both fixed) or auto exposure with a limit; it has no fixed-time, auto-gain mode like the
campods'. `OAK_MAX_EXPOSURE_US` is the limit on all three cameras, and every sidecar records
the exposure and ISO the frame actually got.

**Fixed focus.** Autofocus is off and the colour lens is held at `OAK_LENS_POSITION`
(0..255, lower is farther). The startup line prints it, and each colour sidecar records the
`lens_position` the frame got. It is not the position the device was calibrated at, so the
factory colour intrinsics are for a different focus.

### Sidecar fields

| Field | Meaning |
|-------|---------|
| `kind` | `color`, `left` or `right` |
| `seq` | frames written for this stream this session; also in the filename |
| `device_seq` | the OAK's own frame number; a gap is a frame the host never received |
| `monotonic_ns` | DepthAI's host-synced frame time on CLOCK_MONOTONIC, which DepthAI defines as the **end** of exposure (`ImgFrame::getTimestamp(START)` subtracts the exposure from it) |
| `device_timestamp_ns` | the same instant on the OAK's own clock |
| `wall_ns`, `wall_clock_unix`, `wall_clock_utc` | `monotonic_ns` on the wall clock, by `clk` |
| `clk` | a boot/mono/wall triple read at retrieval, the campods' keys |
| `exposure_us`, `iso` | what the frame got |
| `max_exposure_us` | the ceiling it was given |
| `lens_position`, `color_temperature_k`, `jpeg_quality` | colour only |

IMU lines carry `t` (`a` accel m/s², `g` gyro rad/s), the report's `seq`, `monotonic_ns`,
`device_timestamp_ns`, `x`/`y`/`z` and `accuracy`. A packet repeats the slower sensor's latest
report, so only reports with a new sequence number are written.

## Arm gate and status

The control loop is `capture::Session` in [`../capture-common`](../capture-common/), the
campods' conventions ported from `campod-camera/capture.py`; this app is the depthai half.

- **Gate:** `OAK_CAPTURE_FLAG`, the arm file coordinator-mavlink writes. Frames are retrieved
  all the time so the cameras stay warm; nothing is written until the file says armed. The
  IMU is written only while armed too.
- **Status:** `OAK_STATUS_FILE`, the campod camera document (`ready`, `warm`, `capturing`,
  `frames`, ...), which the coordinator's pod-link publishes as this box's camera. `ready` is
  warm: every camera has delivered `OAK_WARMUP_FRAMES`. A failure, at setup or later, is
  published as `phase: failed` with the message in `error`.
- **Session:** the boot id; build manifests go to `<session>/manifests/`.

## Build

The build context is `containers/`, so the image can take `capture-common`. Its tests run
in the image build, without a device.
