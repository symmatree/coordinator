# References

Background reading and deployment patterns that influenced coordinator design. **Not** dependencies, forks, or upstreams to contribute back to.

## Rekon hardware and mission

- [central-hub.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/central-hub.md) -- Pi 4B hub, OAK-D, Pi Zero USB, PPS
- [oak-d-mount.md](https://github.com/symmatree/fables/blob/main/fables/Drones/rekon10/oak-d-mount.md) -- VIO mount constraints
- [virtualization-study.md](https://github.com/symmatree/fables/blob/main/fables/Drones/coordinator/virtualization-study.md) -- host vs container tradeoffs (Docker Compose recommendation)

## ArduPilot VIO

- [Luxonis OAK-D (ArduPilot)](https://ardupilot.org/copter/docs/common-vio-oak-d.html) -- upstream wiring and build reference
- [chobitsfan VINS-Fusion `apm_wiki`](https://github.com/chobitsfan/VINS-Fusion/tree/apm_wiki) -- `vio-estimator` hypothesis
- [chobitsfan oak_d_vins_cpp `apm_wiki`](https://github.com/chobitsfan/oak_d_vins_cpp/tree/apm_wiki) -- `vio-tracker` hypothesis
- [chobitsfan mavlink-udp-proxy `apm_wiki`](https://github.com/chobitsfan/mavlink-udp-proxy/tree/apm_wiki) -- reference only; coordinator ships its own router
- [Blueos-oakd-vins](https://github.com/Williangalvani/Blueos-oakd-vins) -- containerized build pattern (different runtime egress)

Coordinator IPC and binary plan: [vio-integration.md](vio-integration.md).

## Operator-pattern influence (generic tooling)

Private notes on a v2 edge stack using Docker + Dockge + `/opt/stacks/` + compose CLI: [openmower-os-stack.md](https://github.com/symmatree/fables/blob/main/fables/OpenMower/openmower-os-stack.md). Coordinator reimplements that **shape** with its own naming and Rekon payload; no shared codebase.

## Upstream tools (unbranded)

- [Dockge](https://github.com/louislam/dockge) -- compose stack UI
- [Docker Engine](https://docs.docker.com/engine/) + [Compose](https://docs.docker.com/compose/)
