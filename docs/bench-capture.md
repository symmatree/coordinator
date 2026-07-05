# Bench capture: raw VIO inputs for offline replay

Capture the tracker's **input** streams (`chobits_imu` + `chobits_features`) on the vehicle with `vio-ipc-record`, so they can be replayed through the real estimator offline (coordinator #35) and cross-referenced against the FC log (#42). This is distinct from:

- [bench-estimator.md](bench-estimator.md) — prove the estimator emits *pose* live.
- `vio-pose-tap` — record the pose *output* (`chobits_server`).

Here we record the estimator's **inputs**, with the estimator **off**, and regenerate pose offline. No tee needed (see #30).

## Two gotchas that will bite you

1. **The estimator holds the input sockets.** `chobits_imu`/`chobits_features` are single-consumer AF_UNIX dgram sockets; `vio-estimator` binds them in the `bench`/`flight` profiles. The recorder can't bind them until the estimator is stopped. **`coord stop` does *not* stop the estimator** if it's running out of the active profile (it lives in `bench`/`flight`; with `COMPOSE_PROFILES=tracker`, `down` leaves it). Kill it explicitly:
   ```bash
   docker rm -f coordinator_vio_estimator
   ```
2. **Ordering: recorder starts *after* the tracker.** `vio-tracker`'s entrypoint `rm -f`s `chobits_imu`/`chobits_features` on start, which would unlink a recorder that bound them first. So: tracker up and sending → then start the recorder.

## Recipe (as run 2026-07-05)

Over SSH (from WSL use the Windows client so 1Password holds the key — see [coordinator-network.md](coordinator-network.md)). Run one step at a time.

**1. Health check**
```bash
vcgencmd get_throttled            # want 0x0
lsusb | grep -i 03e7              # OAK-D booted: 03e7:f63b
docker ps --format '{{.Names}} {{.Status}}'
```

**2. Estimator off, tracker-only**
```bash
docker rm -f coordinator_vio_estimator     # frees chobits_imu/features
docker ps --format '{{.Names}} {{.Status}}'  # want ONLY vio-tracker
```
(FC side, for a disarmed run: set `LOG_DISARMED=1` so the FC logs while disarmed — **revert after**.)

**3. Start the recorder (background; sudo — the ipc dir is root-owned)**
```bash
OUT=/home/pi/captures/wave-$(date +%Y%m%d-%H%M%S).feat   # .feat, NOT .bin — .bin collides with ArduPilot FC logs (#45)
sudo nohup vio-ipc-record \
  --socket /var/lib/coordinator/ipc/chobits_imu \
  --socket /var/lib/coordinator/ipc/chobits_features \
  --out "$OUT" </dev/null >/home/pi/captures/rec.log 2>&1 &
sleep 3; tail -5 /home/pi/captures/rec.log     # want chobits_imu/features +NB ticks
```
One continuous file per session is fine — segment cases post-hoc from the IMU trace (slate each case with a hold-then-shake if you want markers).

**4. Stop cleanly, then protect the file**
```bash
sudo pkill -TERM -f vio-ipc-record    # SIGTERM -> flush + write <out>.json manifest (do NOT yank)
sudo chown pi:pi "$OUT"*
```
Then **`scp` the fixture off before `shutdown -h`** — the in-progress capture is the one irreplaceable artifact of the run (#41). Only then:
```bash
sudo shutdown -h now                  # wait for green ACT LED to stop before pulling power
```

## Fixture format

`vio-ipc-record` writes framed records `<ddHI>` = `t_mono, t_unix, socket_id, length` then `length` raw bytes, plus a `<out>.json` manifest mapping socket ids → paths. `t_mono` is the alignment clock (the FC `.bin` wall-clock is often unset/1980; align by motion cross-correlation, not timestamps).

## Related
- #35 (replay harness / estimator-half input-replayer — the offline consumer of these fixtures)
- #42 (the 2026-07-05 disarmed-vs-armed capture pair)
- #41 (filesystem robustness / the irreplaceable in-progress file)
- [bench-estimator.md](bench-estimator.md), [vio-integration.md](vio-integration.md)
