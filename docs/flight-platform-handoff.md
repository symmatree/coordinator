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

## Where things stand (2026-09-27)

**The flight data path works end to end, and one thing collects it.** 260923 was the first
flight collected completely -- coordinator captures, campod session, FC dataflash, journald,
collectd, backpack metrics, mavproxy tlog, base-station raw GNSS -- 1.1 GB in
`datasets/flights/rekon10/260923-new-props/`, with a README saying how each piece was
retrieved. That run is also the argument for everything since: it was a hand-built one-off,
and none of it happened for the calibration runs that followed.

So collection moved onto one rule: **a session is a boot, and anything keyed to a boot is
SELECTED by boot id, never filtered on a wall clock.** That clock is wrong from boot until
NTP lands and in the field is never corrected. Concretely, since #395/#401:

- captures are `captures/<hostname>/<boot-id>/` on both device kinds; the coordinator's used
  to be `captures/<oak-d-mxid>/<ISO>/`, which `coord-sessions` could not see at all
- `timesync.jsonl` and `vehicle.tlog` are written INSIDE the session, where
  `flight-data-layout.md` always said they belonged
- collectd writes `/var/log/collectd/<boot-id>/`, so a session's samples are a directory
- `coord sessions package` carries the boot's `journal.log` and collectd tree in the bundle
- `docker logs` and `coord version` are deliberately NOT in it -- a container outlives a boot,
  and a probe of *now* would be a lie about an old session

**Not migrated, and cannot be:** 26 MxId-named session directories on the coordinator and six
NAS flight directories. Which boot each belonged to was never recorded, so there is no `mv`,
only leave or delete. `analysis/coordinator_captures.py` indexes `captures/<device>/<session>/`
generically and reads both shapes.

**FC dataflash comes off through the coordinator now** (#77, `bin/coord-fc-log`): no second
device on the FC, no card pull. `list` emits JSON; `pull` streams the log to stdout with JSONL
events on stderr, and fleet-control drives both (#400). It took four attempts to get right and
every failure was us mishandling ArduPilot's log state machine -- see the next section.

Open work:

| | |
|---|---|
| retry log 50 into `260926-sixpose-and-bump` | #408 is merged but NOT yet converged onto the coordinator. Converge, then let flight-analysis run it |
| #11, #370 | time and state distribution. The next task, and mostly doable with no hardware -- see below |
| #355 | campod unresponsiveness, still a record rather than a theory |
| #341, #302, #313, #315 | unchanged |

**The next task, and why it is cheap.** Analysis currently recovers campod timing by fitting
observed accelerations against a tap, which is noisy and needs someone to tap. It does not have
to: `timesync.jsonl` already bridges FC clock to coordinator monotonic, and campod sidecars
already carry a stable monotonic. **The only missing link is campod monotonic to coordinator
monotonic** -- two counters from unrelated origins with nothing between them. One round trip
over the gadget net bounds that offset by RTT/2, measured at 0.35 ms. Two crystals drift up to
~100 ppm relative, so a single exchange leaves ~18 ms across a 3-minute armed window;
exchanging every 10 s lets you fit the slope and puts the whole flight under a millisecond.
Forwarding ARM/DISARM is the other half and nearly free -- the coordinator already derives it
for the tracker's arm gate -- and it gives analysis the shared marker it is manufacturing with
a tap. Both records belong in the campod's capture session, which now collects automatically.
No RTC, no PPS, no new hardware.

The DS3234 breakouts are in hand (SparkFun BOB-10160, SPI, SQW broken out, ±2 ppm). #11 has
the conceptual layer written as five edges of who feeds time to whom, with the electrical
detail deliberately left open. Keep those levels apart; mixing them is how that issue
previously filled with over-specified detail that had to be thrown away.

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

**Never touch the hardware without asking for that exact action.** Coordinator, campods, FC.
Including reads -- an ssh login, an `ls`, a probe, an scp of a scratch script. Permission for
one task expires with it; it is not standing access. It is the only test article, and it is the
data-collection vehicle whose repeatability Seth protects by never running anything on it
himself, because ad-hoc runs pollute the history. Concurrent access also destroys work in both
directions: probing the FC while he downloads a log corrupts his download, and him pulling
power during an unannounced test of yours destroys the test. He cannot coordinate around what
he does not know is happening. When granted, say in the same message what you are putting where,
remove it afterwards, and name which binary produced any output you show -- the installed one or
a scratch copy. I ran dev copies out of `/tmp` for days without once saying so.

**Read ArduPilot's state machine before theorising about the FC.** Every FC failure this session
was us violating its protocol, and it stayed responsive through all of them. It is not the
fragile part. `AP_Logger_MAVLinkLogTransfer.cpp` is 250 lines and answers the questions:
`_log_sending_link` is registered for a transfer's duration, `handle_log_request_data` returns
UNCONDITIONALLY while it is set, and the "Log download in progress" STATUSTEXT that would tell
you is suppressed for a same-channel requester. A listing releases the link only on its final
entry, which `LOG_ENTRY.last_log_num` makes observable.

**Prefer the shape of a tool that already works.** `MAVProxy/modules/mavproxy_log.py` requests a
whole log in one go, tracks receipt by 90-byte block, and coalesces gaps -- and pymavlink is
already our dependency, so it is portable rather than merely instructive. I invented a windowed
protocol instead, shipped it twice, and it failed on the vehicle both times; 256 KB is not even
a multiple of 90, so it could only ever have worked on short logs. Cloning the reference
implementation would have been cheaper than any of my reasoning about it.

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

**Do not pre-verify what the action will tell you.** Asked to do something through a system,
do it -- do not first audit whether the system can. A 404 or a non-zero exit answers "is this
possible" in one step you were taking anyway. Checking in advance costs more, and what it makes
you inspect is almost always somebody else's work: their merge, their deploy, their pipeline.
That reads as assuming they failed, and then assuming the systems under them failed too. I did
this to a pod that had restarted ten minutes after a merge -- which was the pipeline working.
The tell is converting the second half of a request into a precondition for the first.

**Never assert a PR or issue's state from memory**, least of all one you asked him to act on --
that is precisely what makes your memory of it stale. `gh pr view` first. And report it flat: no
"still open", no "already merged", no surprise either way. Surprise is not information, it shows
you were treating your expectation as the baseline instead of his decision. Say the TYPE and the
repo too: `coordinator#405, a PR, merged`. A bare number with "open" makes him ask what kind of
thing it even is before he can decide anything.

**`--author @me` is every agent's work, not yours.** All sessions push as the same GitHub user.
I handed him five PRs as my backlog and none were mine. If you cannot summarise it, you did not
write it -- so do not raise it. Attribution is the `Claude-Session:` trailer on the commits.

**One PR per conversational topic.** Not per subdirectory, per language, or per "concern" as you
have decided to narrow it. He can read two languages in one diff, and fine-grained PRs make him
reconcile agreement across several reviews for one decision. The tell that you split wrong: a PR
that keeps absorbing later work, or two PRs from the same afternoon citing the same evidence.

**Say what you measured; he decides what it is worth.** Not just in filed artifacts -- in
conversation too. I told him re-running a calibration was cheaper than reconstructing its data.
That was his call about his own morning, and not mine to price.

**"I am not going to theorise, but..." is worse than either option.** Gesturing at a cause while
disclaiming it is still naming a cause, and it reads as passive aggression. Ask, or drop it.
Words like "fragile" and "risky" in place of a mechanism are the same move: if you cannot say
what breaks and how, you have a feeling, not a finding.

**Unshipped code being unverified is not a caveat.** "One thing I have not done is run this live,
because I just wrote it" is a statement about causality. Flag what you COULD have tested and did
not.

**When a direct quote and your own model disagree, the quote is the evidence.** A peer talked
itself out of a quote I had relayed and did nothing for a day as a result.

**Get the conceptual layer right before the electrical detail**, and do not lock both at once.
Asked how time should flow, I produced pin numbers, pull-up questions and a parameter table in
the same breath -- and the over-specific half had to be thrown away. One level, with focus, and
say plainly what is left open.
