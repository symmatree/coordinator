# oak-still-capture

Captures full-resolution color **stills from the OAK-D RGB camera** to disk with per-frame JSON sidecars — the only onboard stills-quality camera. Feeds the mapping/imagery products (optical SfM via OpenDroneMap, timelapse, high-res single stills). Issue: [#72](https://github.com/symmatree/coordinator/issues/72) (this is **phase 1**).

## What it does

- Configures the OAK-D `CAM_A` (RGB / center) camera, triggers a full-res still every `1/OAK_CAPTURE_HZ` seconds, and writes each as `<node>_<seq>_<walltime>.jpg` + a `.json` sidecar under `${OAK_CAPTURE_DIR}/<node>/<session>/`.
- The sidecar records wall clock, host monotonic, the **device sensor timestamp** (`sensor_timestamp_ns` — the field that anchors PPK-style interpolation against ArduPilot pose; times may need later correction), device sequence, exposure, ISO, and resolution.

## Single-USB constraint

The OAK-D is one USB device. This service **owns** it, so it runs in its own `capture` compose profile and **cannot run alongside `vio-tracker`**. Simultaneous VIO + stills means adding an RGB branch to the tracker's DepthAI pipeline — the harder phase-3 integration (#72).

## Config (env, all optional)

| Var | Default | Meaning |
|-----|---------|---------|
| `OAK_NODE_NAME` | hostname | node label in filenames/metadata |
| `OAK_CAPTURE_DIR` | `/captures` | output dir |
| `OAK_CAPTURE_HZ` | `0.5` | captures per second |
| `OAK_RESOLUTION` | `12mp` | `12mp` (4056×3040) \| `4k` \| `1080p` |
| `OAK_JPEG_QUALITY` | `92` | JPEG quality 1–100 |

## Tested

`test_capture.py` drives the frame→disk path (`write_frame` / `build_sidecar`) with a synthetic 12 MP numpy frame — asserts the JPEG decodes to the right size, the filename encodes node/seq/walltime, and the sidecar carries the timing/exposure fields. **Runs at image build time**, so a broken writer fails the build. The depthai device loop is **hardware-gated** (needs an OAK-D) and imported lazily, so the test runs without depthai. Run directly: `python3 test_capture.py` (needs `opencv` + `numpy`).

## Not yet (phases 2–3, #72)

- **FC geotag:** emit `CAMERA_TRIGGER` / logged `CAM` via the router so the FC `.bin` records position per frame.
- **Simultaneous VIO + stills:** RGB still branch inside the tracker pipeline.
- On-device JPEG encode (`VideoEncoder`) to offload the Pi CPU.

## Compose

Service `oak-still-capture` in `stacks/coordinator/compose.yaml` (`capture` profile): `privileged` + `/dev/bus/usb` bind-mount (same MyriadX re-enumeration reason as `vio-tracker`), captures dir mounted at `/captures`.
