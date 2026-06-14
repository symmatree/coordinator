# Bench: OAK-D feature tracker

First coordinator iteration: prove `feature_tracker` runs in `vio-tracker` with the OAK-D on USB. No VINS estimator or FC required.

## Prerequisites

- Raspberry Pi 4B with USB 3 port for the OAK-D
- Docker Engine + Compose plugin ([host/ansible/install-coordinator.yaml](../host/ansible/install-coordinator.yaml))
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

## Healthy signs

In `vio-tracker` logs you should see:

1. `Usb speed: ...` (expect SUPER for USB3 port)
2. `Device name: OAK-D` (or `OAK-D-PRO`) and product name
3. `imu ok`
4. Periodic `N features` lines (N > 0 with the camera pointed at texture)

The process exits cleanly on `SIGINT` / `docker stop`.

## If it fails

| Symptom | Things to check |
|---------|-----------------|
| No USB device | Cable/port; `lsusb` shows Luxonis; container has `/dev/bus/usb` and `privileged: true` |
| depthai / XLink error | Power (OAK-D wants USB3 current); try another port/cable |
| `0 features` | Lens cap, pointed at blank wall, or extreme motion blur |
| Image pull denied | `docker login ghcr.io` if the package is private |

## IPC sockets (optional check)

With `${COORDINATOR_IPC_DIR}` mounted at `/tmp`, the tracker creates:

- `/var/lib/coordinator/ipc/chobits_2222` (bind)
- sends to `chobits_imu` and `chobits_features` (no listener required for this bench)

Later, with `vio-estimator` running, the same mount wires tracker to VINS.

## Local image build (no GHCR)

On the Pi:

```bash
git clone https://github.com/symmatree/coordinator.git
cd coordinator
docker build -t ghcr.io/symmatree/coordinator-vio-tracker:local containers/vio-tracker
```

Set `VIO_TRACKER_VERSION=local` in `.env` only if you tag locally; compose uses the image name from `compose.yaml`.

## Next iteration

- `vio-estimator` image consuming `/tmp/chobits_*`
- Coordinator MAVLink router on `flight` profile
