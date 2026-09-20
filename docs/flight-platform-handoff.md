# Flight platform: handoff

For whoever is the flight-software / flight-platform agent next. This is the software that
runs **on the vehicle** -- `host/ansible` (how a device is converged and reimaged), the
campod and coordinator payload containers in `containers/`, the stack definitions in
`stacks/`, and the device-side CLI in `bin/`. Not the cluster-hosted control surface; that
is [ground-platform-handoff.md](ground-platform-handoff.md).

The boundary is not the directory. `fleet-control` reaches devices over ssh, so when the
thing being fixed is *what happens on the device*, the fix belongs here even if the line of
code is in TypeScript. Two of this session's bugs were exactly that.

## The charge on this file

**Seth's, not optional:** the agent holding this role updates this file at the END of its
session. **Do not thrash on it mid-session** -- a working period is not a place for constant
small edits to a handoff.

Everything else here is discretion.

Two subtractions keep it honest. When something here becomes a durable fact about the system
it graduates to a real doc; when it becomes work it becomes an issue. What is left is what
would otherwise be re-learned by collision. **The "Where things stand" section is the part
that goes stale fastest** -- treat a date older than a week or two as archaeology, and check
`origin/main` rather than believing it.

## What we are actually trying to do

The deliverable is **sharp aerial images and a usable vibration record**, recovered off the
vehicle after a flight. Everything else is in service of that. VIO is optional and currently
a liability (#313). A taxonomy of the airframe is not the goal; a proven fix that lets the
next flight happen is.

Two properties rank above features:

1. **Collection must land on disk.** Graceful shutdown of a capture session is *not* a
   requirement -- the vehicle dies by power pull in the real case. At most we flush on
   disarm. So "did we lose data" always outranks "was the stop tidy".
2. **The fleet must be updatable.** If a device cannot be converged, nothing else about it
   matters, because no fix can reach it. Most of this session went here.

## What to read, in this order

- **[analysis/pi-zero-unresponsiveness-experiments.md](../analysis/pi-zero-unresponsiveness-experiments.md)**
  -- the current investigation, in the house experiments format: theories with statuses, an
  evidence ledger with sample counts, and a methodology-confounds section that will save you
  a day. Read the confounds first.
- **[docs/campod.md](campod.md)** -- what a campod is, the vibration and rolling-shutter
  reasoning, and the mount.
- **[docs/deployment-model.md](deployment-model.md)** -- the three-tier appliance model:
  immutable image / persisted data / convergence. `git pull` is the deploy (#48), though
  #341 may replace that tier with a payload artifact.
- **`host/ansible/site.yaml`** -- read the comments, not just the tasks. Most of the hard-won
  reasoning in this repo is in that file, and several comments record measurements you would
  otherwise repeat.
- **[analysis/vio-quality-experiments.md](../analysis/vio-quality-experiments.md)** -- not
  because VIO is live, but because it is the best example in the repo of how to hold a
  theory. Note the DISPROVEN labels: prior work is marked superseded, not wrong.
- **[docs/build-and-provenance.md](build-and-provenance.md)** -- which labels and tags are
  load-bearing, and why `org.opencontainers.image.ref.name` is not one of them.

In code: **`bin/coord`** is the device CLI and is short; **`containers/campod-camera/capture.py`**
and **`accel/main.go`** are the two things that actually collect; **`bin/coord-version`** is
the probe the ground platform reads.

## Where things stand (2026-09-20)

**The fleet was un-updatable for two days and now is not.** `coord stop` was
`docker compose stop`, which on a capturing campod did not return -- four measured attempts,
one returned (51s), three did not, two took the machine down with it. Converge died at that
task, so nothing could be deployed.

What that turned out to be, measured:

- **dockerd is the layer that degrades**, not containerd and not the kernel. Same box, same
  moment: `cat /proc/loadavg` 48-59ms, `ctr containers list` 126-213ms, `docker ps -q`
  2.1-22.0s, `docker compose ps -q` 40.3s.
- **The storm is reads, not writes.** ~20 MiB/s of reads with writes collapsing to near zero,
  in every stop with samples. A "flush everything on stop" theory predicts the opposite.
- **It was compose paging itself in.** ~56 MiB allocated at the instant of the stop, ~70 MiB
  of page cache evicted; the docker CLI is 43.5 MiB and the compose plugin 47.2 MiB. When we
  stopped signalling through compose, the read counter stayed *flat*.

So: everything that kills a container set now sends `sudo pkill -x -TERM dumb-init` and waits
for the processes to actually be gone. dumb-init is PID 1 in every device image and proxies
to the child's whole process group (`dumb-init.c:66`), so one signal per container is enough.

Open work:

| | |
|---|---|
| #355 | the unresponsiveness bug, now a record rather than a theory dump |
| #359 | the experiments doc (PR open) |
| #368, #371 | merged this session -- probe round-trips, sessions root+layout |
| #341 | replace the on-device git checkout with a payload artifact |
| #302 | device-side session packaging -- `coord sessions` exists and works on hardware |
| #313 | remove VIO for now; blocks nothing but keeps costing |
| #315 | one shared capture program for campod and OAK-D |

Untested and worth knowing: the 40s quiesce bound is a guess from one 19s observation, and
`stop_grace_period: 90s` rests on a single 35s measurement.

## What must not be dropped

**Every command to a device must quiesce first, as root.** The capture tree and the container
inits are root-owned; `pi` cannot signal or delete them. This bit twice in one session, in
adjacent functions, because it was validated by hand with `sudo` and shipped without.
`site.yaml` and `reimage.yaml` are `become: true` so they were never affected -- which is
exactly why the bug survived.

**`pkill` returns when the signal is sent, not when the process is gone.** 1.1s for the accel
and 19.0s for the camera, both *after* the command returned. Anything that does not wait is
racing the shutdown it just asked for.

**A converge must not restart capture in the middle of itself.** It used to, and then ran
eighteen more tasks against a box writing full-resolution JPEGs. The reboot at the end is what
restarts collection.

**The card fills at roughly 5 GB/hour** and there is no cap. `coord sessions delete` exists
and is proven on hardware. A full card is how this started.

**The Zero has no RTC.** Timestamps from two different boots cannot be ordered against each
other -- a dying boot's last line can appear *later* than the next boot's first. Use an
off-box clock for anything crossing a reboot.

## How to work here

**Fresh worktree off `origin/main` for every change, and remove it when merged.** Never stack
PRs; ordering goes in the body, not the base branch.

**Read the code before theorising, and clone rather than fetching file-by-file.** Reading
`docker/compose`, `moby/moby` and `Yelp/dumb-init` locally answered in minutes what an
afternoon of inference got wrong -- including that dumb-init signals the process *group*,
which was strictly better than what I had written by hand.

**Check the issue comments, not just the body.** The measurement that reframed this whole
investigation -- ~20 MiB/s of reads against 0.13 MiB/s of writes -- was in a comment on #316,
and I spent hours asserting the opposite without having read it.

**Prefer the instrumentation that already exists.** collectd runs on every device at 10s with
7d retention, raw counters, disk/cpu/memory/thermal/interface. Last night's failures were
answerable retroactively from `/var/log/collectd/<node>/` with no new tooling. Per-process
attribution comes from `/proc/<pid>/io` sampled twice ~20s apart: `rchar` near zero with large
`read_bytes` means page faults, not file reads. The box is slow, not dead -- that sampling
works during an episode.

**Do not invent a proxy and then believe it.** I built a TCP/banner poller and reported
"recovered" when all it measured was sshd forking; the stop had failed six minutes earlier and
Seth acted on the wrong statement. Worse, the polling spawned an `sshd-session` per probe on a
box that could not afford forks -- I then reported those processes as a symptom without
noticing I had created them.

**Measure the thing, not a thing near it.** Comparing read *rate* when the rate is pinned at
the card's ceiling cannot show a difference either way. Durations were the discriminator.

**Say n=1 when it is n=1.** Most numbers here are single observations. "35s is the fastest
graceful exit observed" is honest; "it takes 35s" is not.

**Do not call prior work wrong.** It is a record of causes and actions. Writing "that claim
was false" retroactively removes the justification for a change that was made and implies it
could be reverted -- a bold claim that today's measurements of today's system cannot support.
Superseded, with the reason, is almost always the accurate word.

**Ask about cost decisions instead of deciding them.** The cost to weigh is not elegance; it
is that a grounded vehicle cannot be fixed. A list of six strings is a marginal price. Being
clever about avoiding it is not.

**Do the thing that was asked.** The largest single waste this session was substituting my own
variant -- cgroup enumeration instead of "signal the binary", an escalation loop instead of
letting docker own escalation -- and then debugging problems that only existed because of the
substitution. When the improvement hits a problem the original did not have, that is the
signal to go back to the original.
