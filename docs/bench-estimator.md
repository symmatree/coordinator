# Bench: VIO estimator (full vision chain)

Second coordinator iteration: prove `vins_fusion` in `vio-estimator` consumes the tracker's IMU + feature streams and publishes pose on `/tmp/chobits_server`. Runs the `bench` profile (tracker + estimator). No FC required. Builds on [bench-tracker.md](bench-tracker.md) (#8).

## Prerequisites

- `vio-tracker` bench green ([bench-tracker.md](bench-tracker.md)) -- OAK-D up, non-zero features
- `vio-estimator` image available (`ghcr.io/symmatree/coordinator-vio-estimator:main`, built by CI)
- `/var/lib/coordinator/config/oak_d.yaml` present (seeded by the coordinator Ansible role)

## Calibration is a seed

`oak_d.yaml` ships **seed** values, not a measured calibration -- the plan is to refine the camera/IMU constants from a bundle adjustment over a real flight rather than hand-picking checkerboard corners. So `estimate_extrinsic: 1` and `estimate_td: 1` are on: `vins_fusion` optimizes the cam<->IMU transform and time offset online from the initial guess. If bench initialization is poor, set `estimate_extrinsic: 2` (full self-calibration, no prior) and excite all axes (rotate + translate the rig). The file lives at `/var/lib/coordinator/config/oak_d.yaml`; re-provisioning does **not** overwrite a refined copy (`force: false`).

How that refinement gets captured, validated against the GPS trajectory, blessed, and kept honest over time is the [calibration capture chain](calibration.md) -- the seed here is the starting point that the flight-log pipeline replaces with a measured, serial-keyed calibration.

## Run the bench profile

```bash
# bench profile = vio-tracker + vio-estimator
COMPOSE_PROFILES=bench coord pull
COMPOSE_PROFILES=bench coord start
coord status                       # both Up, no restarts
coord logs -f vio-estimator        # "USE_IMU: 1", "waiting for image and imu...", then init
```

Ordering is not enforced: the tracker tolerates an absent listener (drops packets) and the estimator picks up the continuous stream whenever it binds, so start order between the two does not matter.

## Verify pose output

`vins_fusion` sends a `float[10]` per estimate to the Unix socket `/tmp/chobits_server` (on the host: `${COORDINATOR_IPC_DIR}/chobits_server`, i.e. `/var/lib/coordinator/ipc/chobits_server`): `quat(w,x,y,z)` + `pos(x,y,z)` + `vel(x,y,z)`. Tap it from the host -- bind the socket, then move the rig and watch position/attitude change:

```python
#!/usr/bin/env python3
import socket, struct, os
p = "/var/lib/coordinator/ipc/chobits_server"
try:
    os.unlink(p)
except FileNotFoundError:
    pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.bind(p)
print("listening on", p)
while True:
    d = s.recv(64)
    if len(d) >= 40:
        qw, qx, qy, qz, px, py, pz, vx, vy, vz = struct.unpack("<10f", d[:40])
        print(f"q=({qw:+.3f},{qx:+.3f},{qy:+.3f},{qz:+.3f}) "
              f"p=({px:+.2f},{py:+.2f},{pz:+.2f}) v=({vx:+.2f},{vy:+.2f},{vz:+.2f})")
```

(Alternative: `vins_fusion` also streams the same odometry over UDP to whoever first sends a datagram to its port `8800` -- handy for a remote tap. The Unix socket above is the contract `coordinator-mavlink` will use.)

## Success criteria

- `COMPOSE_PROFILES=bench coord start` brings up both containers; `coord status` shows both `Up`, no crash loop
- `vio-estimator` logs show it reading IMU + features and completing VIO initialization (not stuck at "waiting for image and imu...")
- Pose packets arrive on `/tmp/chobits_server`, and **position/attitude track real motion** when you move the rig

What this does **not** prove: that the pose is metrically correct. Like the tracker bench, green here means "the estimator comes up, consumes the streams, and emits pose-shaped output that moves sanely." True accuracy needs known motion / ground truth and the flight-refined calibration above.

## If it fails

| Symptom | Cause / fix |
|---------|-------------|
| `vio-estimator` exits immediately with usage text | No config arg / missing file. Entrypoint passes `/config/oak_d.yaml`; confirm `/var/lib/coordinator/config/oak_d.yaml` exists and the `:/config` mount is present. |
| `vio-estimator` logs empty | Same block-buffering trap as the tracker; the entrypoint wraps `vins_fusion` in `stdbuf -oL`. If you rebuilt locally, confirm that. |
| Stuck at "waiting for image and imu..." | Tracker not sending, or socket paths not shared. Confirm `vio-tracker` is `Up` and emitting (its bench), and both containers mount the same `${COORDINATOR_IPC_DIR}:/tmp`. |
| Pose diverges / never initializes | Calibration seed too far off, or insufficient motion. Excite all axes; try `estimate_extrinsic: 2`. This is expected pre-refinement -- see the calibration note above. |
| No packets on `/tmp/chobits_server` | Nothing bound the socket when you checked; the tap script binds it. The estimator only sends once it has a pose; confirm it initialized first. |

## Relevant docs

- [vio-integration.md](vio-integration.md) -- IPC layout, binary plan, socket contract
- [architecture.md](architecture.md) -- compose profiles, ipc volume
- [bench-tracker.md](bench-tracker.md) -- the tracker-only baseline (#8)
- [containers/vio-estimator/README.md](../containers/vio-estimator/README.md) -- image build, no-ROS note
