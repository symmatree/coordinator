# Pi Zero pod bringup

Plan of record for bringing up the first Rekon camera pod node -- a **Pi Zero 2 W + Camera Module 3 (IMX708)** -- from bare SD card to a node that captures stills locally and reports to the Coordinator. Tracked in the coordinator repo for now (the pod may split into its own repo later; the layout below keeps that cheap).

Design source (private fables): [arm-pods.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/arm-pods.md), [central-hub.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/central-hub.md). Coordinator side: [architecture.md](architecture.md), [host-setup.md](host-setup.md).

## Why this lives next to the coordinator code

The pod and the Coordinator are two devices that **must collaborate** (USB gadget network, NTP/PPS time, start/stop, status). Keeping both in one repo lets them share the operator model instead of maintaining two parallel copies:

| Shared asset | How it serves both |
|--------------|--------------------|
| `bin/coord` | One stack-aware CLI. Each device runs only its own stack under `/opt/stacks/*`; `coord` defaults to the sole installed stack. |
| `host/ansible/roles/docker-host` | Docker engine, group, state dirs, reboot loop -- identical on Pi 4B and Pi Zero. |
| `host/ansible/roles/chrony` | Same role, two modes: Coordinator = **server + PPS** ([#11](https://github.com/symmatree/coordinator/issues/11)); Zero = **client + its own PPS wire**. |
| `host/one_time.sh [coordinator\|pod]` | One bootstrap entrypoint, role arg selects device. |
| Dockge `/opt/stacks/*` | Both stacks register with the same UI ([#13](https://github.com/symmatree/coordinator/issues/13)). |

Device-specific code stays small and isolated: `roles/coordinator` (OAK-D udev + vio stack), `roles/pod` (`dwc2`/`g_ether` + `pps-gpio` overlays + camera stack), `containers/pod-camera/`, `stacks/pod/`.

## Repo layout (flat; coordinator and pod are siblings)

```
bin/coord                      shared, stack-aware
host/
  one_time.sh                  shared; one_time.sh [coordinator|pod]
  ansible/
    site.yaml                  shared entry; dispatch on device_role
    roles/
      docker-host/             SHARED: engine, group, state dirs, reboot
      chrony/                  SHARED (mode: server|client)
      coordinator/             OAK-D udev + coordinator stack sync
      pod/                     dwc2/g_ether + pps overlay + pod stack sync
containers/
  vio-tracker/                 coordinator (existing)
  pod-camera/                  pod capture image (arm64)
stacks/
  coordinator/                 existing
  pod/                         pod stack -> Dockge sees both
docs/
  pi-zero-bringup.md           this file
  pi-zero-host-setup.md        Phase 1 image-install runbook
```

There is intentionally **no** nested `pizero/{host,containers,stacks}` mirror of the top level -- that false parallel is awkward to maintain. Dockge already expects multiple stacks under `/opt/stacks/`, so two stack dirs is the native shape.

## Can a Pi Zero 2 W run Docker for this? Yes -- try it.

No demonstrated blocker. The honest constraints:

- **RAM (512 MB) is the binding limit, and the budget fits one low-duty container.** Estimate (not measured): Pi OS Lite headless ~100 MB + `dockerd`/`containerd` ~100 MB + one `picamera2`-class container ~100 MB ~= 300 MB, with zram/swap for spikes. Capture is **1--2 Hz, very low duty cycle** (fables), so steady-state churn is low.
- **The real iteration cost is camera passthrough, not resources.** libcamera in a container needs `/dev/video*`, `/dev/media*`, `/dev/dma_heap`, vchiq, and `/run/udev` visible inside. Known-solvable (privileged or explicit device mounts); this is what Phase 2 exists to shake out.
- **Thermal is hardware-mitigated** in fables (full-length heatsinks + open-core prop-wash), driven by chrony/USB-gadget/SD-writes, not the runtime. Docker idle cost is small; the CPU is mostly idle between frames.
- **Never build on the Zero** -- CI builds arm64, the Zero pulls (the existing repo pattern). The heavy cost never lands on the device.
- **Fallback, not a fork:** if the daemon overhead ever annoys, the same capture binary/config runs natively under systemd; the built image stays the artifact. Note it; don't plan around it.

## Phases

Each phase is one GitHub issue ([#22](https://github.com/symmatree/coordinator/issues/22), [#23](https://github.com/symmatree/coordinator/issues/23), [#24](https://github.com/symmatree/coordinator/issues/24), [#25](https://github.com/symmatree/coordinator/issues/25)). Phases 3 and 4 are the two halves of existing coordinator subsystems -- the pod side of work the Coordinator side already tracks.

### Phase 1 -- Host bootstrap (image, ssh, container-ready) -- [#22](https://github.com/symmatree/coordinator/issues/22)

Bare SD -> a Zero that runs Docker and the pod stack scaffolding, mirroring how the Coordinator is brought up. Runbook: [pi-zero-host-setup.md](pi-zero-host-setup.md).

- Flash Raspberry Pi OS (64-bit) Lite via Imager 2.0+; step-4 hostname (`pod-NNE` etc.), user, SSH key, lab WiFi (prep only -- in flight the pod has no WiFi, only USB gadget net).
- `./host/one_time.sh pod`: shared `docker-host` role + `pod` role state dirs and stack sync.
- Success: SSH in, `docker ps` works, `coord status` shows the (empty) pod stack.

No camera, no gadget net, no PPS yet. This proves Docker runs on the Zero -- the open question from the viability discussion above.

### Phase 2 -- Capture container (stills to local SD, no coordination) -- [#23](https://github.com/symmatree/coordinator/issues/23)

A `pod-camera` container pulls IMX708 stills at 1--2 Hz and writes JPEG + timestamp metadata to the Zero's **own SD card** (never over USB -- fables: USB 2.0 would bottleneck; the bus is for commands only).

- Capture stack (picamera2 vs rpicam-apps) decided when building this container. **The deciding factor is frame-sync exposure, not device passthrough** (passthrough is identical for both): the CM3 has no XVS hardware trigger, so the array relies on libcamera's **software camera-sync** -- one Zero is the **pacesetter/server**, the rest are clients aligning frame timing, with sync messages on the USB net (fables `arm-pods.md`; "<10 us" per libcamera). Whichever front-end most cleanly exposes that sync mode + the server/client role + per-frame timestamp metadata wins. (Understanding to confirm in #23: `rpicam-apps --sync server|client`; picamera2 `SyncMode` controls.)
- Timestamps from `CLOCK_MONOTONIC` per fables, written as sidecar metadata for later PPK-style interpolation against ArduPilot pose logs.
- On-disk layout under `/var/lib/pod/captures/` (host bind mount), naming TBD in the issue.
- Success: `coord start` on the pod, frames accumulate on disk at the target rate, container stays up.

Still standalone -- no network to anyone.

### Phase 3 -- Gadget network + chrony (the pod half of #11 and #12) -- [#24](https://github.com/symmatree/coordinator/issues/24)

Make the pod reachable and time-aligned. This is the **Zero side** of two host subsystems the Coordinator already tracks; the issues explicitly carve the Zero work out as separate.

| Pod side (this phase) | Coordinator side (existing) |
|-----------------------|------------------------------|
| `dwc2` + `g_ether` so the Zero appears as a `usb*` net iface | `br0` + DHCP to absorb `usb*` ifaces -- [#12](https://github.com/symmatree/coordinator/issues/12) |
| chrony **client**: NTP from Coordinator + phase-lock to local PPS wire (`pps-gpio` overlay, GPIO TBD) | chrony **server** + DS3234 PPS distribution -- [#11](https://github.com/symmatree/coordinator/issues/11) |

- Add `dtoverlay=dwc2` + `g_ether` module config and `dtoverlay=pps-gpio,gpiopin=N` to the pod's `/boot/firmware/config.txt` (pod role).
- chrony role in **client** mode: server = Coordinator's bridge address; PPS refclock from `/dev/pps0`.
- Success: Coordinator gets a DHCP lease for the pod and can `ping` it; `ppstest /dev/pps0` on the pod shows 1 Hz; `chronyc sources` shows both the Coordinator and PPS. Bridge survives a pod reboot.

**Contract to settle with #12:** subnet, whether the pod is static or DHCP, and the hostname/addressing scheme the Coordinator uses to find each pod (needed by Phase 4).

### Phase 4 -- Control + status (start/stop, report to Coordinator, Alloy) -- [#25](https://github.com/symmatree/coordinator/issues/25)

The pod accepts start/stop capture commands and reports status; the Coordinator collects it and informationally relays successful-capture telemetry to the FC over MAVLink (**not** a load-bearing timestamp -- operator visibility only, per fables).

- A small **pod control service** (the "Pi Zero pod control API" already reserved in [architecture.md](architecture.md)) exposes start/stop and status. This pairs with the **deferred Pi Zero relay** noted in `coordinator-mavlink` ([#10](https://github.com/symmatree/coordinator/issues/10) / [vio-integration.md](vio-integration.md)).
- **Open contract (decide with #10):** wire format and transport. Candidates: MAVLink over the gadget net (consistent with the FC path, lets the Coordinator forward directly) vs. a small HTTP/gRPC pod API on `br0` (simpler to build/debug, Coordinator translates to MAVLink). The relay is deliberately unspecified in #10 -- this phase defines it.
- **Alloy:** a Grafana Alloy container on the pod for local state capture (logs/metrics: capture rate, disk usage, temp, throttle, chrony offset), shipping to the same observability path the Coordinator uses. Coordinator Alloy is itself a later container ([architecture.md](architecture.md) host/container table).
- Success: Coordinator issues start/stop and sees per-pod capture status; FC log shows the informational capture telemetry; pod metrics/logs visible in Grafana.

## Collaboration contracts (what the two devices must agree on)

These are the seams where pod and Coordinator work must align; each is owned by a paired issue.

| Contract | Pod issue (this track) | Coordinator issue | Must agree on |
|----------|------------------------|-------------------|---------------|
| USB gadget reachability | [#24](https://github.com/symmatree/coordinator/issues/24) | [#12](https://github.com/symmatree/coordinator/issues/12) | subnet, DHCP vs static, per-pod hostname/address |
| Time | [#24](https://github.com/symmatree/coordinator/issues/24) | [#11](https://github.com/symmatree/coordinator/issues/11) | NTP server address, PPS GPIO pin, shared epoch |
| Control + status | [#25](https://github.com/symmatree/coordinator/issues/25) | [#10](https://github.com/symmatree/coordinator/issues/10) | start/stop + status wire format and transport |
| Stack UI | all | [#13](https://github.com/symmatree/coordinator/issues/13) | Dockge `/opt/stacks/*` registration |

## Related docs

- [pi-zero-host-setup.md](pi-zero-host-setup.md) -- Phase 1 runbook
- [host-setup.md](host-setup.md) -- Coordinator equivalent (shared bootstrap shape)
- [architecture.md](architecture.md) -- host/container split, reserved Pi Zero control API
- [vio-integration.md](vio-integration.md) -- deferred Pi Zero relay in `coordinator-mavlink`
- fables: [arm-pods.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/arm-pods.md), [central-hub.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/central-hub.md)
</invoke>
