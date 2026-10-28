# coordinator-flir-camera

Still capture for a FLIR Blackfly S on USB3 Vision. Captures free-running at a fixed rate
(default 1 Hz) and writes each frame as a single-channel JPEG plus a JSON sidecar to local
storage. The camera is described in
[docs/rekon10/flir-blackfly-s.md](../../docs/rekon10/flir-blackfly-s.md); the pod that runs
this image, its host configuration and its stack are in [docs/blackfly.md](../../docs/blackfly.md).

**Status:** the blackfly stack runs it as `blackfly-camera`
(`stacks/blackfly/blackfly-camera.container`). On 2026-10-28, under the stack's earlier
`rampod` name, it ran on blackfly against the real camera: on the pod bus `ready`, streaming
at 1.0 Hz, 5320x4600 Mono8. No armed capture has gone through the bus yet.

## What it does

- `capture.py` opens the first camera Aravis lists, asks for `Mono8` and sets
  `AcquisitionFrameRate`. Aravis's FLIR path enables the rate and turns its auto mode off
  first. Whatever rate the camera accepts is logged and published.
- Every frame the stream returns is copied out, and the buffer goes straight back to the
  stream. Frames are written only while the capture flag says so. A writer thread encodes
  with simplejpeg's `GRAY` colorspace and `Gray` subsampling, so the output is a real
  one-channel JPEG.
- Before reporting warm, the encode and a card write run once on a real frame (the warmup
  probe).
- It shares campod-camera's file contract, so pod-link and session collection need
  nothing new:
  - the capture flag it reads;
  - the status document it writes, from which pod-link reads `ready`;
  - `/captures/<node>/<boot id>/` with `manifests/` and a `frames.jsonl` ledger line for
    every frame seen.
- `ready` is `warm` AND frames arriving. A camera that stops delivering reads `no-frames`,
  which makes it not ready until frames resume.

## Timestamps in the sidecar

| Field | What it is |
|---|---|
| `device_timestamp_ns` | The camera's own clock, from the stream leader. Not related to host time yet. |
| `system_timestamp_ns` | Host `CLOCK_REALTIME` when Aravis received the frame's leader. Later than the exposure by a transfer latency nobody has measured. |
| `clk` | boot/mono/wall read together just after the frame was popped. Same keys as the accel reader and campod. |

Nothing here records the exposure instant on host time. The camera's GPIO is how to get
it: a trigger input on Lines 0/2/3, or an `ExposureActive` output on Line 1/2.

## Config

| Var | Default | Meaning |
|-----|---------|---------|
| `FLIR_CAPTURE_DIR` | `/captures` | output dir |
| `FLIR_FRAME_RATE_HZ` | `1.0` | `AcquisitionFrameRate` to ask for |
| `FLIR_JPEG_QUALITY` | `90` | JPEG quality 1-100 |
| `FLIR_CAPTURE_FLAG` | `/tmp/campod_capture` | pod-link's capture flag |
| `FLIR_STATUS_FILE` | `/tmp/campod_camera_status` | the camera document pod-link publishes |

## What the device has to provide

USB access to the camera (Aravis goes through libusb on `/dev/bus/usb`) and a usbfs buffer
limit above one frame. Both are host configuration, set where
[docs/blackfly.md](../../docs/blackfly.md) says.
