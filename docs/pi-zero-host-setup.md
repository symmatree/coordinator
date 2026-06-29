# Pi Zero pod host setup (Phase 1)

Fresh SD card -> a Pi Zero 2 W that runs Docker and the pod stack scaffolding, ready for the capture container (Phase 2). The Coordinator equivalent is [host-setup.md](host-setup.md); this reuses the same shared bootstrap with `device_role=pod`.

Scope: **host bootstrap only**. No camera, no USB gadget network, no PPS -- those are Phases 2--4 ([pi-zero-bringup.md](pi-zero-bringup.md)).

## What Phase 1 proves

That a Pi Zero 2 W (512 MB) actually runs Docker + Compose for our workload shape. See the viability discussion in [pi-zero-bringup.md](pi-zero-bringup.md#can-a-pi-zero-2-w-run-docker-for-this-yes----try-it): the expectation is "tight but fine" for one low-duty container; this step is where that gets confirmed on metal.

## 1. Flash and first boot

Raspberry Pi Imager 2.0+, **Raspberry Pi OS (64-bit) Lite** from the online list (container images are `linux/arm64`; Lite is enough headless).

Imager step 4 -- Configure your system:

| Setting | Value |
|---------|-------|
| Hostname | `pod-NNE` (or the camera-node name from arm-pods.md) |
| User / password | operator account, password from 1Password `rpi/pi` |
| SSH | enable, public key (OnePKey identity) |
| WiFi | **lab SSID** -- prep only; in flight the pod has no WiFi, only the USB gadget net |

WiFi here is for `git clone` / `coord pull` during bring-up. Disabling it for flight is a later ops concern, not an Imager step.

Boot, then SSH in:

```bash
ssh <user>@pod-NNE.local
uname -m      # expect aarch64
```

## 2. Clone and bootstrap

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/symmatree/coordinator.git
cd coordinator
./host/one_time.sh pod
```

`one_time.sh pod` runs the shared bootstrap with `device_role=pod`:

1. apt update / dist-upgrade, install Ansible + minimal deps.
2. `docker-host` role: Docker CE + Compose plugin, docker group, enable Docker.
3. `pod` role: pod state dirs (`/opt/stacks/pod`, `/var/lib/pod/{config,captures}`), sync `stacks/pod/` + `coord` from the checkout.
4. Reboot if `dist-upgrade` left `/var/run/reboot-required` set; **re-run until it exits clean** (same loop as the Coordinator).

The camera overlays, `dwc2`/`g_ether`, and `pps-gpio` are **not** applied here -- they land in Phase 3.

## 3. After bootstrap

```bash
newgrp docker          # or re-login, if docker ps says permission denied
docker ps
ls /opt/stacks/pod/
coord status           # auto-detects the sole stack (pod); empty until Phase 2
```

`coord` is the same CLI as on the Coordinator. With only the pod stack installed under `/opt/stacks/`, it defaults to that stack -- no flag needed.

## Out of scope (later phases / issues)

| Item | Phase | Pod role change |
|------|-------|-----------------|
| Capture container | 2 | `pod-camera` image + `stacks/pod` service |
| USB gadget net (`dwc2`/`g_ether`) | 3 | `/boot/firmware/config.txt` overlay + module |
| chrony client + PPS (`pps-gpio`) | 3 | overlay + chrony role (client mode) |
| Control API + status + Alloy | 4 | pod control service + Alloy container |

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `exec format error` | 32-bit OS flashed; re-flash **64-bit** Lite |
| `permission denied` on `docker ps` | `newgrp docker` or re-login (not a reboot) |
| Script exits 1, reboot-required set | re-run `./host/one_time.sh pod` after the host returns |
| `coord` picks the wrong stack | only the pod stack should be under `/opt/stacks/` on a pod; check `ls /opt/stacks/` |
| Out-of-memory during bootstrap | expected pressure point on 512 MB; ensure zram/swap is enabled (Pi OS default) |
