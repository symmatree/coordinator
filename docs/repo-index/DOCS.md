# coordinator -- Documentation Index

Generated 2026-07-05. Summaries reflect the docs AS OF this date; verify before relying.

## Repo description

`coordinator` is the on-vehicle companion software for the Rekon10 drone platform. It wires an OAK-D camera through a VIO pipeline (feature tracking -> VINS-Fusion pose estimation) and forwards pose to the ArduPilot flight controller over MAVLink, all running in Docker on a Raspberry Pi 4B payload computer. A companion Pi Zero 2 W acts as a camera pod; both devices share a single Ansible bootstrap and `coord` operator CLI.

---

## Top level

| Path | One-line summary | Link |
|------|-----------------|------|
| `README.md` | Repo overview: stack layout, vision pipeline status, quick-start steps, and operator CLI reference. | [README.md](https://github.com/symmatree/coordinator/blob/main/README.md) |

---

## docs/ -- Architecture and runbooks

| Path | One-line summary | Link |
|------|-----------------|------|
| `docs/architecture.md` | System design for the Pi 4B hub: host vs. container split, compose profiles, IPC socket scheme, and planned services. | [docs/architecture.md](https://github.com/symmatree/coordinator/blob/main/docs/architecture.md) |
| `docs/ardupilot-vio.md` | MAVLink wiring from VIO pose to the TBS Lucid H7 FC, including UART params and ArduPilot EKF3 settings. | [docs/ardupilot-vio.md](https://github.com/symmatree/coordinator/blob/main/docs/ardupilot-vio.md) |
| `docs/bench-capture.md` | Runbook for recording raw VIO input streams on-vehicle for offline replay and FC log cross-reference. | [docs/bench-capture.md](https://github.com/symmatree/coordinator/blob/main/docs/bench-capture.md) |
| `docs/bench-estimator.md` | Runbook for benching the full vision chain (tracker + vio-estimator) and verifying pose output without a FC. | [docs/bench-estimator.md](https://github.com/symmatree/coordinator/blob/main/docs/bench-estimator.md) |
| `docs/bench-tracker.md` | Runbook for benching the OAK-D feature tracker alone: first coordinator iteration, no estimator or FC. | [docs/bench-tracker.md](https://github.com/symmatree/coordinator/blob/main/docs/bench-tracker.md) |
| `docs/calibration.md` | Plan for producing, validating, and versioning camera/IMU calibration; describes the off-vehicle workflow and storage conventions. | [docs/calibration.md](https://github.com/symmatree/coordinator/blob/main/docs/calibration.md) |
| `docs/coordinator-mavlink.md` | Design of record for the coordinator MAVLink router: its divergence from the chobits reference, the dPos/dt velocity + honest-covariance model, and follow-ups. | [docs/coordinator-mavlink.md](https://github.com/symmatree/coordinator/blob/main/docs/coordinator-mavlink.md) |
| `docs/coordinator-network.md` | WiFi provisioning and headless recovery for the Pi 4B, including root-cause notes from a 2026-07-04 connectivity loss. | [docs/coordinator-network.md](https://github.com/symmatree/coordinator/blob/main/docs/coordinator-network.md) |
| `docs/host-setup.md` | One-time path from a fresh SD card to a coordinator host ready to run `coord pull` / `coord start`. | [docs/host-setup.md](https://github.com/symmatree/coordinator/blob/main/docs/host-setup.md) |
| `docs/pi-zero-bringup.md` | Plan of record for bringing up a Pi Zero 2 W + Camera Module 3 pod from bare SD card to a functioning capture node. | [docs/pi-zero-bringup.md](https://github.com/symmatree/coordinator/blob/main/docs/pi-zero-bringup.md) |
| `docs/pi-zero-host-setup.md` | Host bootstrap for the Pi Zero pod (Phase 1 only): Docker install and pod stack scaffolding, no camera or USB gadget yet. | [docs/pi-zero-host-setup.md](https://github.com/symmatree/coordinator/blob/main/docs/pi-zero-host-setup.md) |
| `docs/references.md` | Annotated reading list of background material and deployment patterns that influenced coordinator design. | [docs/references.md](https://github.com/symmatree/coordinator/blob/main/docs/references.md) |
| `docs/vio-integration.md` | Working plan for how the vision stack is wired end-to-end via Unix IPC sockets, following the chobitsfan apm_wiki approach. | [docs/vio-integration.md](https://github.com/symmatree/coordinator/blob/main/docs/vio-integration.md) |

---

## containers/ -- Per-container READMEs

| Path | One-line summary | Link |
|------|-----------------|------|
| `containers/coordinator-mavlink/README.md` | Minimal Python MAVLink router that reads pose from the IPC socket and forwards it to the FC over UART. | [containers/coordinator-mavlink/README.md](https://github.com/symmatree/coordinator/blob/main/containers/coordinator-mavlink/README.md) |
| `containers/pod-camera/README.md` | Capture container for the Pi Zero pod: pulls JPEG stills at 1 Hz and writes frames + JSON sidecars to local SD. | [containers/pod-camera/README.md](https://github.com/symmatree/coordinator/blob/main/containers/pod-camera/README.md) |
| `containers/vio-estimator/README.md` | VINS-Fusion (`vio-estimator`) container: consumes tracker IMU + feature streams and publishes pose; no ROS. | [containers/vio-estimator/README.md](https://github.com/symmatree/coordinator/blob/main/containers/vio-estimator/README.md) |
| `containers/vio-tracker/README.md` | OAK-D `feature_tracker` container built against depthai-core v2.25.0; produces IMU and feature streams over IPC sockets. | [containers/vio-tracker/README.md](https://github.com/symmatree/coordinator/blob/main/containers/vio-tracker/README.md) |

---

## host/ -- Ansible provisioning

| Path | One-line summary | Link |
|------|-----------------|------|
| `host/README.md` | Ansible playbook and one-time shell entrypoint for bootstrapping both coordinator and pod devices via `device_role`. | [host/README.md](https://github.com/symmatree/coordinator/blob/main/host/README.md) |

---

## harness/ -- VIO evaluation harness

| Path | One-line summary | Link |
|------|-----------------|------|
| `harness/README.md` | Test harness that drives the real MAVLink router with synthetic/recorded pose datagrams and a fake FC, no hardware required. | [harness/README.md](https://github.com/symmatree/coordinator/blob/main/harness/README.md) |

---

## analysis/ -- Flight log and VIO analysis

| Path | One-line summary | Link |
|------|-----------------|------|
| `analysis/README.md` | Index of analysis notebooks and shared Python library for parsing ArduPilot `.bin` logs and aligning VIO input streams. | [analysis/README.md](https://github.com/symmatree/coordinator/blob/main/analysis/README.md) |
