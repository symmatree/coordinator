# Bench: OAK-D feature tracker

First coordinator iteration: prove `feature_tracker` runs in `vio-tracker` with the OAK-D on USB. No VINS estimator or FC required.

## Prerequisites

- Raspberry Pi 4B with USB 3 port for the OAK-D
- Host bootstrap complete ([host-setup.md](host-setup.md) -- `./host/one_time.sh`)
- Stack at `/opt/stacks/coordinator/` and `coord` on `PATH`
- `/var/lib/coordinator/ipc` created (playbook does this)

## Compose profile

Default `.env` uses `COMPOSE_PROFILES=tracker` -- only `vio-tracker` starts.

| Profile | Services |
|---------|----------|
| `tracker` | `vio-tracker` |
| `bench` | `vio-tracker` + `vio-estimator` (estimator image not shipped yet) |
| `flight` | bench + `coordinator-mavlink` |

## Pull and start

```bash
# After host bootstrap; set VIO_TRACKER_VERSION in .env (e.g. main or CI sha tag)
coord pull
coord start
coord status
coord logs -f vio-tracker
```

## If it fails

| Symptom | Cause / fix |
|---------|-------------|
| `X_LINK_DEVICE_NOT_FOUND` ("Failed to find device after booting"), crash-looping | depthai uploads firmware, the MyriadX resets and **re-enumerates at a new `/dev/bus/usb` node** (watch the node number climb in `ls /dev/bus/usb/001/`). The compose service must bind-mount `/dev/bus/usb` as a `volumes:` entry, **not** `devices:` — a `devices:` mapping is a static snapshot taken at container start, so the post-boot node is invisible. Verified on this rig 2026-06-28. |
| `X_LINK_DEVICE_ALREADY_IN_USE` | A previous (crashed) run still holds the XLink session, or the device is left booted (`lsusb` shows `03e7:f63b`). Stop the stack, wait a few seconds for it to settle back to the bootloader (`03e7:2485`), then start again. Power-cycling the OAK-D also clears it. |
| `lsusb` shows nothing under `03e7` | Cable/port; original OAK-D needs a data-capable USB cable. The container must have `/dev/bus/usb` mounted and `privileged: true`. |
| Stuck negotiating USB3 / link errors on an original OAK-D | First-wave OAK-D (ROM bootloader v0.0.28, no SPI flash) can't do USB3. The image patches `feature_tracker.cpp` to force `dai::UsbSpeed::HIGH` (USB2) at device open. |
| Image pull fails | Network; tag in `.env` (`VIO_TRACKER_VERSION=main`); `docker pull ghcr.io/symmatree/coordinator-vio-tracker:main` to isolate registry issues. |

A healthy run: `docker inspect -f '{{.RestartCount}}' coordinator_vio_tracker` stays `0`, `lsusb` shows the device booted as `03e7:f63b`, and `/var/lib/coordinator/ipc/chobits_2222` exists.

## IPC sockets (optional check)

With `${COORDINATOR_IPC_DIR}` mounted at `/tmp`, the tracker creates:

- `/var/lib/coordinator/ipc/chobits_2222` (bind)
- sends to `chobits_imu` and `chobits_features` (no listener required for this bench)

Later, with `vio-estimator` running, the same mount wires tracker to VINS.

## Local image build

```bash
docker build -t ghcr.io/symmatree/coordinator-vio-tracker:local containers/vio-tracker
```

See [containers/vio-tracker/README.md](../containers/vio-tracker/README.md) for cross-build on amd64. Tag `local` in `.env` only if you built that tag locally.

## Next iteration

- `vio-estimator` image consuming `/tmp/chobits_*`
- Coordinator MAVLink router on `flight` profile
