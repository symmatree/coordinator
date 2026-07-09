# Architecture

Rekon payload coordinator software for the Pi 4B central hub. Design notes live in the private [facts](https://github.com/symmatree/fables) repo (`fables/Drones/rekon10/central-hub.md`, `fables/Drones/coordinator/virtualization-study.md`).

## Operational use cases (first-flight lessons)

*Motivational scenarios, not a settled design -- captured from the first VIO-enabled flight
attempts (2026-07-09) to drive the coordinator's control/ergonomics work. Details still to be
worked through.*

**The triggering incident.** With `VISO_TYPE=1` set, the FC **refused to arm because the visual
system was not healthy** -- correct and safe, but the ergonomics are poor: the failure surfaces
only at arm time, and `VISO_TYPE` needs an **FC reboot** to change, so it cannot be trivially bound
to the runtime EKF-source switch. First operational takeaway: the operator needs to know the vision
stack's readiness *before* it matters, and mode changes are heavyweight enough to want a deliberate,
checked path.

**UC1 -- Command by intent; the coordinator checks preconditions.** The operator tells the
coordinator *what they want* ("start full VIO", "start input logging") and the coordinator validates
its own preconditions -- tracker running, estimator producing pose, OAK-D USB healthy, calibration
loaded -- and reports **ready / not-ready with a reason**, instead of the operator discovering in
flight that a stereo stream never started. A stronger form: the coordinator **gates the unsafe
action** -- e.g. only assert the VIO EKF source once it holds a good estimate, decoupling the
operator's request from the raw switch. (Open: for the specific *source-switch* case the benefit may
be marginal given the VISO/reboot constraints; the durable value is the general **intent ->
precondition-check -> report** pattern.)

**UC2 -- On-vehicle control surface (pHAT screen + buttons); laptop-free ops.** Run a coordinator
flight without a laptop. The top pHAT display shows coordinator **state** (VIO mode, stereo/estimator
health, FC link, a green/red *ready* light); buttons drive the common actions -- **pull + restart**
(green light when it is back up), **start input logging**, **start full VIO**, **commanded
shutdown**, and possibly **"switch mode + reboot"** (since `VISO_TYPE` needs a reboot). This is
`coord`'s actions and states bound to physical I/O instead of an SSH session.

**UC3 -- FC and coordinator run independently.** Each must be useful without the other: the
coordinator for bench work, troubleshooting, and **alternate payload modules**; the FC to fly (the
arming gate is the friction point). Powering one without the other is a **normal state, not an
error** -- the router already tolerates an absent FC; keep that property and extend it to the whole
stack.

**UC4 -- Graceful degradation with no serial link.** With the FC link absent or down, the coordinator
still runs and **clearly reports "no link"**, and distinguishes a **recoverable** condition (link
returns when cabled / the FC boots) from one that **needs a reboot** -- surfaced on the display so
the operator knows which, without a laptop.

**Cross-cutting principle.** The coordinator is an **autonomous payload commanded by intent that
reports its own readiness** -- not a passive pipe that fails silently or reveals problems only in
flight. The design details (transport for operator intent, the health/readiness model, the
display/button mapping, and how "mode + reboot" is triggered safely) are the next conversation.

## Host vs container

| Responsibility | Where | Why |
|----------------|-------|-----|
| OAK-D pipeline (`feature_tracker`) | container | Fragile C++/depthai deps; USB device; rebuilds often |
| VINS estimator (`vins_fusion`) | container | Heavy native build; pin upstream; rebuild rarely |
| MAVLink router (coordinator-owned) | container | FC UART, Pi Zero relay, obstacle, pose ingress |
| Grafana Alloy (later) | container | Isolated observability |
| Pi Zero pod control API (later) | container or host | App logic; may share network with router |
| chrony + PPS discipline | **host** | GPIO `/dev/pps0`, `SYS_TIME` |
| USB gadget `br0` + DHCP | **host** | Dynamic `usb*` interfaces; not a Docker bridge problem |
| WiFi AP / station / off | **host** (or D-Bus-mounted utility container later) | NetworkManager integration |
| Docker Engine, Dockge | **host** | Generic runtime |

**PPS status (2026-07-06): not fitted.** No PPS hardware is wired anywhere yet -- not on the
Coordinator, not on the Pi Zeros, and no GPS PPS line. The `chrony + PPS` row above is the
*planned* design (DS3234 SQW -> GPIO 18 distribution, [#11](https://github.com/symmatree/coordinator/issues/11));
until it is built, host time is NTP-after-boot only (no PPS discipline) and timestamps must not
be assumed PPS-aligned. See also the no-RTC note in [coordinator-network.md](coordinator-network.md).

## Runtime paths (on Pi)

| Path | Contents |
|------|----------|
| `/opt/stacks/coordinator/` | `compose.yaml`, `.env` |
| `/var/lib/coordinator/config/` | VIO config (`oak_d.yaml`, etc.) mounted read-only |
| `/var/lib/coordinator/ipc/` | Shared Unix socket dir bind-mounted as `/tmp` in vision + mavlink containers |
| `/var/lib/coordinator/state/` | Runtime state (reserved; image logs, etc.) |
| `/opt/dockge/` | Dockge UI (upstream; shared across stacks) |

## Container layout (current plan)

Three services, one process each. Processes talk over **Unix domain datagram sockets** on a **shared bind mount** (not Docker bridge networking, not UDP). Socket paths and binary roles: [vio-integration.md](vio-integration.md).

```
USB OAK-D
  -> vio-tracker (feature_tracker)
       --/tmp/chobits_imu, /tmp/chobits_features-->
  -> vio-estimator (vins_fusion)
       --/tmp/chobits_server-->
  -> coordinator-mavlink (coordinator router; chobitsfan mavlink_udp is bring-up reference only)
       --UART--> FC
       (later: Pi Zero traffic, obstacle MAVLink, etc.)
```

| Service | Binary (hypothesis) | Devices / mounts |
|---------|---------------------|------------------|
| `vio-tracker` | `feature_tracker` | USB; `${COORDINATOR_IPC_DIR}:/tmp`; config ro |
| `vio-estimator` | `vins_fusion` | `${COORDINATOR_IPC_DIR}:/tmp`; config ro |
| `coordinator-mavlink` | coordinator MAVLink router | `${COORDINATOR_IPC_DIR}:/tmp`; FC serial; host network for Pi Zeros |

### Why three containers

| Benefit | Notes |
|---------|-------|
| Isolated logs | `coord logs vio-tracker` vs untangling one supervisord stream |
| Isolated rebuilds | Tracker image changes on depthai / pipeline / recording; estimator stays on a pinned chobitsfan SHA; router rebuilds during FC integration |
| Isolated restarts | MAVLink router crash does not kill the camera pipeline |
| Bench without FC | `tracker` profile: OAK-D only; `bench`: tracker + estimator; no serial |

### IPC volume

Set `COORDINATOR_IPC_DIR=/var/lib/coordinator/ipc` on the host. Each participating service mounts it at `/tmp` so hardcoded chobitsfan paths (`/tmp/chobits_imu`, etc.) work without source patches.

Operational notes:

- Create the directory before `coord start`; entrypoints should unlink stale `chobits_*` socket files after crashes.
- Run participating containers as the same UID, or make the ipc dir group-writable with a shared group.
- Replacing container `/tmp` is acceptable for these minimal single-purpose images.

### Compose profiles

| Profile | Services | Use |
|---------|----------|-----|
| `tracker` (default) | `vio-tracker` | First iteration: OAK-D feature extraction ([bench-tracker.md](bench-tracker.md)) |
| `bench` | `vio-tracker`, `vio-estimator` | Desk: full vision chain, no FC ([bench-estimator.md](bench-estimator.md)) |
| `flight` | above + `coordinator-mavlink` | FC serial mounted |

Startup order is not enforced between tracker and estimator: the tracker tolerates an absent listener (drops packets) and the estimator picks up the continuous stream whenever it binds. (A tracker->estimator `depends_on` is not usable -- the estimator is absent in the `tracker` profile, so it would error "depends on undefined service".) `coordinator-mavlink` consumes pose once the estimator publishes (flight profile).

## Rekon goals beyond wiki VIO

The ArduPilot OAK-D wiki stack proves pose to FC. Coordinator also targets goals from Rekon design docs. Which binary owns each item is spelled out in [vio-integration.md](vio-integration.md).

| Goal | In stock chobitsfan? |
|------|----------------------|
| Bounded canopy VIO pose | Partially (estimator + bridge pattern) |
| OAK-D image capture to disk (review / fly-through) | No -- likely `feature_tracker` / pipeline extension |
| Obstacle distance from depth | No -- disparity already computed in tracker |
| Pi Zero command / telemetry via USB bridge | No -- coordinator MAVLink router |

Pi Zero relay and obstacle MAVLink can ship after vision bench if complexity warrants deferral; the router process is still the long-term home for FC-facing integration.

## Bench without FC

**Tracker only (current):** see [bench-tracker.md](bench-tracker.md).

**Full vision (later):**

1. `COMPOSE_PROFILES=bench` and start tracker + estimator.
2. Confirm pose on `/tmp/chobits_server`.

Do not mount FC serial or start `coordinator-mavlink` until an FC is wired.

## Stack services

`stacks/coordinator/compose.yaml` defines three services. **`vio-tracker`** and **`vio-estimator`** images and Dockerfiles ship in `containers/`; the `coordinator-mavlink` image is the remaining follow-up.

ROS in a container remains optional later; the host never installs ROS for the flight path.

## FC / EKF

Exploratory notes only: [ardupilot-vio.md](ardupilot-vio.md). Not a settled param recipe or fusion policy.
