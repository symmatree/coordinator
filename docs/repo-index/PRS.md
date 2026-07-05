# coordinator -- Merged PRs Index

Generated 2026-07-05 via gh.

| # | Merged | Title |
|---|--------|-------|
| [43](https://github.com/symmatree/coordinator/pull/43) | 2026-07-05 | docs about first handheld flight |
| [38](https://github.com/symmatree/coordinator/pull/38) | 2026-07-01 | harness: router-half VIO seam test (fake FC + pose replayer) |
| [37](https://github.com/symmatree/coordinator/pull/37) | 2026-06-30 | analysis: ardupilot_log.py -- parse_log() and ArduPilot constants |
| [36](https://github.com/symmatree/coordinator/pull/36) | 2026-06-30 | Add vio-ipc-record: raw IPC capture for the replay fixture (#35) |
| [34](https://github.com/symmatree/coordinator/pull/34) | 2026-06-30 | coordinator-mavlink MVP: forward VINS pose to the FC over UART (#10) |
| [33](https://github.com/symmatree/coordinator/pull/33) | 2026-06-30 | Add vio-pose-tap: real tool for the VINS pose stream |
| [29](https://github.com/symmatree/coordinator/pull/29) | 2026-06-30 | docs: calibration capture chain |
| [28](https://github.com/symmatree/coordinator/pull/28) | 2026-06-29 | pod-camera capture image (Phase 2, #23) |
| [27](https://github.com/symmatree/coordinator/pull/27) | 2026-06-29 | Add coordinator-vio-estimator image and wire bench end to end |
| [26](https://github.com/symmatree/coordinator/pull/26) | 2026-06-29 | Pi Zero pod bringup track (Phase 1 scaffold) + shared host refactor |
| [21](https://github.com/symmatree/coordinator/pull/21) | 2026-06-28 | For some reason claude won't commit my changes so I'll make my own pr |
| [20](https://github.com/symmatree/coordinator/pull/20) | 2026-06-28 | Line-buffer vio-tracker logs via stdbuf |
| [19](https://github.com/symmatree/coordinator/pull/19) | 2026-06-28 | Fix OAK-D X_LINK_DEVICE_NOT_FOUND: bind-mount /dev/bus/usb as volume |
| [17](https://github.com/symmatree/coordinator/pull/17) | 2026-06-28 | Add Luxonis udev rules to Ansible bootstrap |
| [16](https://github.com/symmatree/coordinator/pull/16) | 2026-06-28 | Force USB2 mode in vio-tracker for original OAK-D |
| [15](https://github.com/symmatree/coordinator/pull/15) | 2026-06-28 | Fix docker group: lookup('env','USER') instead of ansible_user_id |
| [14](https://github.com/symmatree/coordinator/pull/14) | 2026-06-28 | Fix coordinator_sync_repo boolean coercion in Ansible |
| [6](https://github.com/symmatree/coordinator/pull/6) | 2026-06-15 | Host setup: one_time.sh and host-setup runbook (issue #5) |
| [4](https://github.com/symmatree/coordinator/pull/4) | 2026-06-15 | Fix GHCR push auth in vio-tracker CI |
| [3](https://github.com/symmatree/coordinator/pull/3) | 2026-06-15 | CI: native arm64 build for vio-tracker |
| [2](https://github.com/symmatree/coordinator/pull/2) | 2026-06-15 | vio-tracker: OAK-D feature extraction image and bench stack |
| [1](https://github.com/symmatree/coordinator/pull/1) | 2026-06-14 | Repo skeleton: stack, coord CLI, and docs |

## Themes

- **VIO/Sensor Core**: OAK-D feature extraction, native arm64 CI, GHCR push, USB2 compatibility, USB device binding, log buffering, vio-estimator image, pose-tap tool, mavlink integration, router-half seam test (#1, 2, 3, 4, 16, 19, 20, 27, 33, 34, 38)

- **Hardware & Pod Bringup**: Host bootstrap, Ansible variable fixes, Luxonis udev rules, docker group setup, Pi Zero Phase 1 scaffold with shared host refactor, pod-camera Phase 2 stills capture (#6, 14, 15, 17, 26, 28)

- **Flight Analysis & Provenance**: Calibration capture chain documentation, VIO IPC recording for replay, ardupilot log parsing, Kubernetes CronJob runner with provenance sidecar, RO-Crate evaluation, first handheld flight documentation (#29, 36, 37, 39, 43)

- **Foundation**: Repo skeleton and CI/build tooling (#1, 21)
