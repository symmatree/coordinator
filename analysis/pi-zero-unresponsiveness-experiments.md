# Experiments: Pi Zero 2 W unresponsiveness (campod startup and teardown)

A campod (Zero 2 W, 512 MB nominal / `MemTotal` ~417 MiB, no swap, SD card) becomes
barely able to make progress at two points in its life: **starting the capture stack**
and **stopping it**. In the worst cases the machine stops answering SSH for minutes and
sometimes resets uncleanly.

Scoped as one document across both ends deliberately. It is **not** established that
they share a cause. They are here together because they share the whole investigative
apparatus -- the same counters, the same blind spots, the same clock problems -- and
because the one signature measured at both ends is the same: a sustained ~20 MiB/s of
**reads** with writes near zero, not a write storm.

"Unresponsive", not "frozen". The machine is making progress throughout, far too
slowly; every observed episode either completed eventually or the box reset.

Related: [#355](https://github.com/symmatree/coordinator/issues/355) (the teardown bug),
[#316](https://github.com/symmatree/coordinator/issues/316) (CMA and footprint, where
the startup-side numbers were first measured).

## Data

| source | what it gives | where | caveats |
|---|---|---|---|
| collectd | disk read/write octets, per-core cpu incl. iowait, memory absolute, thermal, interface | `/var/log/collectd/<node>/`, 10s **raw counters**, 7d retention | **stops early in shutdown** -- the teardown window is blank, not quiet. Counters reset across a boot, producing one garbage negative sample |
| journal | ansible task invocations, async countdowns, systemd shutdown sequence, `coord-throttle-log` every 30s | `journalctl -b -N` | persistent (#100) |
| docker logs | `capture: received signal 15`, frame counts, accel session summaries | `docker logs -t <container>` | survive reboots; containers are reused, not recreated |
| `docker inspect` | `ExitCode`, `FinishedAt` | | 137 = 128+9 = SIGKILL |
| **per-process counters** | `rchar`/`wchar` vs `read_bytes`/`write_bytes`; minor and major fault counts | `/proc/<pid>/io`, fields 10-13 of `/proc/<pid>/stat` | **the only source that attributes I/O to a process** -- collectd is whole-disk. Must be sampled *during* the episode: read twice ~20s apart and difference. Used this way by os-and-driver-guy on the import storm |
| off-box TCP/banner probe | responsive / accepts-TCP-but-silent / no-TCP | notebook | **only measures sshd**, says nothing about docker or load |

Counters not rates was the right call: gaps across a power cut are visible and the
rates are still derivable.

## Theories

### T1 -- Page-cache thrash: a large allocation evicts the working set, which then refaults off a ~20 MiB/s card -- **leading**

Supported at both ends of the life cycle.

Teardown, campod-se 2026-09-20, across a stop:

    02:55:12Z  cached 156.0 MiB  free 27.2  used 177.0   read 0.02 MiB/s  write 1.67
    02:56:14Z  cached  81.8 MiB  free 44.7  used 233.3   read 17.8        write 1.71
    02:58:14Z  cached  90.4 MiB  free 37.2  used 232.0   read 20.7        write 0.02

~56 MiB allocated, ~70 MiB of page cache evicted. No swap, so only file-backed pages
are reclaimable; everything whose text was evicted must be re-read.

**The attribution comes from per-process counters, not from the disk totals.** On the
import storm, os-and-driver-guy sampled `/proc/<pid>/io` twice 20s apart and found
`rchar` near zero while `read_bytes` was large -- i.e. the process was not issuing
`read()` calls at all. The volume was page faults on mapped text being pulled back in.
That is what distinguishes T1 from "something is reading a lot of files", and no
whole-disk counter can make that distinction.

Startup, from #316: `workingset_refault_file` ~6,000 pages/s during the camera import
against 6.9/s idle. 6000 x 4 KiB = **~24.6 MB/s**, which is the read rate measured at
both ends. That arithmetic agreeing is the strongest single piece of evidence here.

**Not established:** what allocates the ~56 MiB (see T5), and whether refaults are
cause or consequence.

### T2 -- Deferred writeback all coming due at once -- **weakened, not disproven**

Predicts a write burst on stop. Measured: writes *fall* from ~1.6 MiB/s to ~0.0-0.5
during the storm, in all three stops with samples.

Not disproven, because reads can be performed **in service of** writes: low write volume
at the block layer is not "nothing is being written".

**btrfs compression is not available as that mechanism for these observations.**
`compress=zstd` reached every fstab line only between `dotfiles-symm` `f1bb7db`
(2026-09-12) and `011eb43` (2026-09-16), which removed it; before 09-12 it was on some
lines and silently cleared by a later mount. E1-E11 were taken on `campod-pi-20260918`
(`2bc2aec`) and `campod-pi-20260919` (`1622c85`), both built after the removal, and
`/proc/mounts` on those cards carries no `compress`. An observation from a card flashed
inside that four-day window would be a different matter.

Also relevant: a graceful reboot, which completes in 52s without the storm, **lost no
data** (see ledger E6). If the storm were the data-preservation work, skipping it should
have cost something.

### T3 -- Session runtime / accumulation drives it -- **open, all observations consistent, no counterexample**

Every stop observed is ordered consistently: the one that completed had the smallest
session; all three that failed had larger.

Non-linearity is **expected**, not surprising -- a saturating I/O channel behaves as
"fine below X, hopeless above X". A threshold lower than guessed is therefore not
evidence against a threshold. What would count against T3 is a **success at a larger
session than a failure**, which has not been seen.

Mechanism sketch (Seth's): pages are quietly evicted over a long session, and the storm
is everything being needed back at once. This is the only proposal so far that explains
a runtime correlation without hand-waving, and it is the same mechanism as T1.

### T4 -- Free disk space drives it -- **not isolated; do not credit**

Correlates (successes at 22 G free, failures at ~8-9 G) but is **confounded with T3**:
in every observation, more free space also meant a smaller session. Nothing separates
them. An earlier write-up of mine claimed a success under load "ruled it out"; that
reasoning was wrong, because that success also had more free space.

Separating them needs a run with a large session and plenty of free space, or the
reverse.

### T5 -- The docker CLI plus compose plugin are the ~56 MiB allocation -- **candidate, unconfirmed**

Measured on campod-sw 2026-09-23: `/usr/bin/docker` is **42.6 MiB** and
`/usr/libexec/docker/cli-plugins/docker-compose` **28.8 MiB** -- 71.5 MiB for the pair.
(The earlier figures here, 43.5 and 47.2 MiB, were amd64 from a workstation.) Faulting in
~56 MiB is 78% of both binaries, so the fit is tighter than the amd64 numbers suggested.
Nothing has confirmed the allocator, and no measurement has been taken of what the
resident set actually is on the Pi.

Consistent with the containerd asymmetry in T7: `ctr` is small and containerd was
already resident.

### T6 -- The systemd watchdog causes the unclean resets -- **candidate only; the one direct check reads against it**

`RuntimeWatchdogSec=1m` from stock RPi `40-rpi-enable-watchdog.conf`; systemd pings
`/dev/watchdog0` every 30s and the BCM2835 resets the board if PID 1 stops for 60s.

`/sys/class/watchdog/watchdog0/bootstatus` reads `0`, and **it always will on this
hardware** -- that ambiguity is resolvable from the driver. `bcm2835_wdt.c` declares
`options = WDIOF_SETTIMEOUT | WDIOF_MAGICCLOSE | WDIOF_KEEPALIVEPING`, without
`WDIOF_CARDRESET`, and never assigns `bootstatus` anywhere; `PM_RSTS_HADWRH_SET`
(`0x00000040`) is defined and never used. So the field is not reporting "did not fire",
it is not reporting at all, and no amount of collecting it will distinguish the cases.

If a reset ever does need attributing, the firmware publishes the raw `PM_RSTS` value at
`/proc/device-tree/chosen/bootloader/rsts` (`0x20` on every normal boot measured
2026-09-19/20, `0x21` after `reboot '1'`), and the driver names bit 6 `HADWRH`. Whether
the firmware sets it on a watchdog reset is unverified.

No check was made of whether PID 1 was missing pings before or during a stop.

Treat as unevidenced. Two boots ended with no systemd shutdown sequence at all, so
*something* reset them; the watchdog is one candidate among others.

### T7 -- dockerd specifically degrades, not the container runtime beneath it -- **observed; mechanism unknown**

Same box, same moment:

| layer | capture running | capture stopped |
|---|---|---|
| `cat /proc/loadavg` | 48-59 ms | -- |
| `ctr --namespace moby containers list` | 126-213 ms | 123-287 ms |
| `docker ps -q` | 2.1 / 5.6 / 10.2 / 10.7 / 22.0 s | 136-223 ms |
| `docker compose ps -q` | 40.3 s | 3.0 s |

containerd and the kernel are unaffected; dockerd alone slows by ~100x. Everything
routed through it inherits that, which is why `compose stop` spent ~20s before
delivering a signal.

**Columns are ~8h apart, not a controlled A/B.** The "stopped" column was taken during a
converge, so converge load alone does not cause the degradation.

Mechanism unknown. dockerd's data-root is on the contended card and the json-file driver
puts container stdout there (`/var/lib/docker/containers/<id>/<id>-json.log` was observed
failing with ENOSPC when the card filled); containerd's hot path for these queries does
not obviously touch it the same way. Untested.

## Evidence ledger

| id | date | observation | n |
|---|---|---|---|
| E1 | 09-19 | Stop via ansible, 9.5 GB session: never returned, SSH stopped answering, unclean reset. No SIGTERM reached the camera -- still logging frames at +23s | 1 |
| E2 | 09-19 | Same, second occurrence. Camera still logging at +85s, no SIGTERM | 1 |
| E3 | 09-20 | `docker compose stop` by hand, ~0.6 GB session: **rc=0 in 51s**. SIGTERM delivered at +20s. Camera exit 137, accel exit 0 | 1 |
| E4 | 09-20 | Stop via ansible, 2.14 GB session, 22m47s uptime: async ceiling hit at 180s (`Timeout exceeded`), **containers still running**, no SIGTERM. SSH unanswerable ~6 min. Load 43.31 after. **The I/O outlived the killed command by ~6 min** | 1 |
| E5 | 09-20 | Graceful `systemctl reboot`: down-to-up **52s**, no storm observed (but see C3) | 1 |
| E6 | 09-20 | Same reboot, data check: jpgs 6346->6394, sidecars 6347->6394 (mid-write pair completed), last pre-reboot frame sha256 **unchanged**, last frame decodes at 4608x2592, both accel files grew past their 128 KiB alignment (final partial buffer written). **No loss** | 1 |
| E7 | 09-20 | Same reboot: SIGTERM 13:38:31.503Z, both containers exit **0** at 13:39:06.50Z = **35.0s**, within 30 ms of each other. Under systemd's 90s ceiling, so not truncated by it | 1 |
| E8 | 09-19/20 | Read/write direction across three stops: reads 17-21 MiB/s, writes collapsing to ~0. Normal capture is the inverse (~1.6 write, ~0.01 read) | 3 |
| E9 | 09-20 | Page cache 156 -> 82 MiB, used +56 MiB, at storm onset | 1 |
| E10 | 09-20 | Post-boot reads 16-20 MiB/s with writes ~0 (the camera import) | 1 |
| E11 | 09-19 | Container **start** took 3m33s (accel) and 3m54s (camera), `restarts=0`, one clean start | 1 |
| E12 | -- | #316: `workingset_refault_file` ~6,000 pages/s during import vs 6.9/s idle; ~20 MiB/s reads vs ~0.13 MiB/s writes | -- |

## Timeout inventory

Innermost outward. Relevant because expiry at one layer leaves the layers below in
indeterminate states.

| layer | value | set where | on expiry |
|---|---|---|---|
| compose SIGTERM->SIGKILL grace | 10s (90s as of #358) | docker default / `stacks/campod/compose.yaml` | SIGKILL |
| *(observed graceful exit)* | *35.0s, n=1* | | |
| ansible SSH connect | 90s | `ansible.ts:208` | connection error |
| systemd unit stop | 90s | `DefaultTimeoutStopSec` | SIGKILL |
| ansible async, stop task | 180s | `site.yaml:176` | kill child, task fails |
| fleet-control run | none | `ansible.ts:183 job_timeout: 0` | never |

The grace was **below the only measured completion time**, so that path always ended in
SIGKILL; #358 raises it to the ceiling the successful exit ran under. Async expiry
restores nothing -- it kills the child and reports failure while the I/O continues (E4).

## Methodology confounds

Read this before trusting any timeline.

1. **No RTC.** Every boot starts on a fake clock and jumps when NTP syncs, so timestamps
   from two different boots cannot be ordered against each other. A dying boot's last
   entry can appear *later* than the next boot's first. Only intervals within one settled
   boot are trustworthy; use an off-box clock for anything crossing a reboot.
2. **collectd stops before the shutdown completes.** The teardown window has no samples.
   Blank is not quiet.
3. **The off-box probe only measures sshd.** A banner returning means sshd could fork, not
   that docker finished, load dropped, or anything completed. This was misread once as
   "recovered" when the stop had already failed six minutes earlier.
4. **On-box sampling during an episode IS feasible**, contrary to a claim I made while
   investigating. The machine is slow, not dead -- two reads of `/proc/<pid>/io` 20s
   apart succeed, and that pair is what produced the page-fault attribution. Do not
   conclude that a window is unmeasurable because sshd is slow to answer.
5. **Observation costs load.** A handful of `docker ps` calls pushed load to 7.4 and made
   sshd miss its window for 7s. Any on-box sampling perturbs the thing being measured.
6. **Counters reset across a boot**, producing one nonsensical sample (~1000 MiB/s on both
   read and write). Discard rather than interpret.
7. **Almost everything here is n=1.** The ledger says so per row.

## Next experiments

1. Reboot, let the camera import settle, then stop immediately -- tests T3's prediction
   that a young session stops cleanly. With #358 merged, also the first test of whether
   a 90s grace lets the camera exit 0 on the compose path.
2. Repeat at increasing session age to look for a threshold. T3 predicts non-linear:
   fine, then not.
3. A large session with plenty of free space (or the inverse) to separate T3 from T4.
4. An **ungraceful** reset -- power cut, or `echo b > /proc/sysrq-trigger` -- with the
   same before/after data check as E6. The graceful case lost nothing; the case that
   actually happens in flight is untested.
5. Identify the ~56 MiB allocation (T5): resident set of the docker CLI and compose
   plugin on arm64 during a stop.
6. **During the next episode**, take the per-process pair: `/proc/<pid>/io` and the
   fault counts from `/proc/<pid>/stat`, twice ~20s apart, for dockerd, the compose
   process, capture.py and campod-accel. `rchar` against `read_bytes` says whether each
   is reading or faulting; major faults say who is thrashing. This is the measurement
   that would attribute the teardown storm the way the import storm was attributed, and
   nothing collected so far does it.
