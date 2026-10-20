# Flight platform: handoff

For whoever is the flight-software / flight-platform agent next. This is the software that
runs **on the vehicle** -- `host/ansible` (how a device is converged and reimaged), the
campod and coordinator payload containers in `containers/`, the stack definitions in
`stacks/`, and the device-side CLIs in `device-sw/cli/`. Not the cluster-hosted control surface; that
is [handoffs/ground-platform-handoff.md](handoffs/ground-platform-handoff.md).

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
- **[docs/time-base.md](time-base.md)** -- how the pods and the coordinator agree on when
  something happened. Read it before touching anything in that path: the arrangement is
  counter-intuitive on purpose (chrony's reference is the RTC, never UTC) and the failure mode
  of getting it wrong is silent.
- **[docs/build-and-provenance.md](build-and-provenance.md)** -- which labels and tags are
  load-bearing, and why `org.opencontainers.image.ref.name` is not one of them.

- **[analysis/image-quality-experiments.md](../analysis/image-quality-experiments.md)** -- why
  the stills are not usable, as open topics rather than conclusions. Newer than this file's
  other entries, and the imagery side is the deliverable.

In code: **`device-sw/cli/coord`** is the device CLI and is short; **`containers/campod-camera/capture.py`**
and **`accel/main.go`** are the two things that actually collect; **`device-sw/cli/coord-version`** is
the probe the ground platform reads. Read **`containers/fleet-control/src/quiesce.ts`** and
**`probe.ts`** too, short as they are -- they decide what every remote operation costs, and one
of them stops the whole fleet.

## Where things stand (2026-10-12)

**The capture lifecycle thread is done.** #434 is closed and #439 merged, so a pod warms up,
holds idle-ready, and starts when the coordinator tells it the vehicle armed -- `capture.py`
gated on a flag file, `pod-link` publishing from the arm file the router writes. #442 added the
MQTT control surface and status document on top. What is left of #25 is the topics and the
readiness half, not the mechanism.

**The deployment tier changed shape underneath that.** `bin/` is now `device-sw/cli/`, holding
only what a device installs, and `packaging/build-device-sw` packs it with `stacks/` into a
`device-sw-<sha>.tar.gz` per commit -- 29.6 KB against the whole repo. Nothing on a device
fetches it yet; `roles/checkout` still clones, and that is #341's surface. The install set is a
directory rather than a list, deliberately: `roles/host-cli` copies every file in `cli/` and the
packer packs the same directory, so neither can disagree with the other or go stale.

**Quadlet units and the CLIs are copies, not symlinks into the checkout.** A symlink under
`/etc/containers/systemd` points into `/home`, which podman's generator reads before
`local-fs.target` -- it dangles and the generator emits *nothing*, exit 0, no output. That broke
first boot twice before it was understood.

**`deploy.yaml` neither reboots nor starts the stack.** The quiesce at the top leaves it
stopped and that is the intended end state -- `coord start`, fleet-control's Reboot, or a power
cycle is the operator's call. `provision.yaml` still reboots, for udev and the gadget link on a
fresh card. Do not add a start task: that argument has been had twice and the second time it
was me asserting my own requirement against Seth's design.

**PPS is built on the coordinator and brought up.** First tier only: a DS3234 and one
SN74AHC125N feeding GPIO24, measured 2026-10-11. [time-base.md](time-base.md) has the numbers
and, more usefully, what they are *not*. The fan-out for the pods is not built and no Zero has a
leg. The software -- chrony with the chip as its source, the FC correcting the chip while
disarmed, the edge recorded at both ends -- is merged and has never run on hardware.

**The FC parameter store reset itself to defaults on 2026-09-27** and took `SERIAL4_PROTOCOL`
with it, so the coordinator's UART went quiet and every log pull failed at `wait_heartbeat`.
Unexplained. `ardupilot/verify.py` compares the fragments against the export and is
deliberately non-blocking, because a fragment legitimately leads the export between merge and
apply.

**A freshly flashed card cannot be reached by fleet-control.** dotfiles-symm#77: the cloud-init
seed carries one key and it is the operator's, so the service gets `Permission denied
(publickey)` until someone appends its key by hand over their own. Twice now, two different
cards. Expect to do it on every flash until that lands.

## What must not be dropped

**These are embedded devices that happen to run Linux. Do not interrogate a running one.**
Configure, power, collect. On a device that is capturing, the only safe operation is to stop it
with signals and then WAIT -- not `docker ps`, not a probe, not a polling loop however cheap,
not "just confirming it came up". Every ssh forks an sshd on a box that cannot spare it, and
observation IS the load. The docker CLI alone faults in ~71 MiB of mapped text on a 417 MiB
board with no swap; `docker ps` measures 2.1-22.0 s with capture running against 136-223 ms
without. **A command returning is not evidence it was harmless** -- the cost is page cache, not
exit status, and it outlives the call (E4: I/O continuing ~6 min after the command was killed).

**`GET /status` quiesces EVERY node.** `probe.ts:64` is `PROBE_COMMAND = quiesced('coord version')`
and `probeAll` maps it over the whole roster, so looking at the status page stops the fleet
capturing -- and with no `restart:` policy anywhere, the containers stay stopped until a reboot.
Use `GET /nodes/<name>/status` for one device. This also collides with #434: asking whether pods
are ready is what makes them not ready.

**The card fills in about 4.2 hours and nothing caps it.** A full card does not present as a
disk error -- it presents as a pod that cannot capture, stops answering, and invites several
wrong explanations. **Run `df` before theorising.** `coord sessions delete` does work on a full
btrfs card, measured twice: 26.5 GB in 20 s and in 26 s.

**collectd answers most of this retroactively.** `/var/log/collectd/<boot-id>/<node>/`, 10 s raw
counters, ~7 boots retained, written by the device with nobody touching it. Settle behaviour,
the import storm, when a card filled, when a stack died -- all recoverable afterwards. It is the
first place to look and it costs the device nothing.


**chrony's reference is the RTC chip, never UTC, and getting that wrong fails silently.** One
chip generates both the seconds label and the 1 Hz edge, so they cannot disagree; a UTC
reference is a *different* clock from the pulse, and their difference drifts at the chip's
2 ppm until it crosses chrony's 0.4 s `PPS_LOCK_LIMIT` and every pulse is discarded with
nothing reporting a problem. It would work for two days. This was built the wrong way round
once and corrected -- [time-base.md](time-base.md) has the reasoning, and
`should_set_chip` in `device-sw/cli/coord-rtc` is where the rest of the rule lives.

**Do not write the chip's time registers while anything is using the tick.** The write resets
the countdown chain, which re-phases the 1 Hz and makes one second the wrong length -- and the
campods govern capture timing off that signal. The gate is the arm file, which is also what
pod-link publishes as capture intent, so it is the same value the pods act on. Whatever
implements disarmed bench capture (#42) has to flow through that file or the gate reads the
wrong one.

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

**A found sample is not data.** The single most repeated mistake of this session: taking a
device that happened to be in some state, measuring it, and reporting the result as a finding.
A controlled run states its provenance before it starts -- same code, known starting condition,
one variable, nothing touched in between -- and the difference is not cosmetic. Seven *found*
boots gave a 60-260 s settle spread; three controlled ones gave 70-130, and most of that range
had been image and condition differences rather than the thing being measured. If you cannot
say what was fixed, you have an anecdote.

**Measure by reading what the device wrote, not by watching it.** The window worth measuring is
the window you must not observe. Sidecar `monotonic_ns`, the accel stream's `boot_ns`, and the
collectd tree are all time-since-boot and all free. Protocol: power cycle, hands off, stop with
signals, collect, compute.

**Look at the artifact.** I reported "90 seconds of successful capture" from a file count
without opening a single frame. It happened to be true; had it not been, the report would have
been a spontaneous hardware failure that was really someone disturbing a cable. Two images and
four minutes would have settled it either way.

**Reach before declaring something unreachable.** Twice in one session I declared a capability
absent after one failed check -- no ssh key (it was in `~/.ssh/OnePKey`) and no Go toolchain (a
download away). Both became load-bearing excuses in artifacts before anyone corrected them.

**Do not propose in-flight detection.** The reflex to add a watcher, a health check, a failsafe
is almost always wrong here: the platform cannot yet fly a sunny-day mission, nothing can safely
watch a capturing pod anyway, and the operator is standing right there. Fix the thing.

**Do not treat the operator as unreliable, in either direction.** Twice in one session, as
mirror images. First: I told him PRs were ready, asked him to review and merge, then an hour
later said "the only thing we are waiting on is you to merge" -- assuming he had not done the
thing I asked. Then, told plainly that he had merged them: I asked GitHub, which agreed, and
then went further and diffed the trees to prove it -- producing a *wrong* intermediate
conclusion I needed a third check to undo. His read: "you are equally determined to treat me as
unreliable when I tell you things, as you are to treat me as unreliable when you need things."
If you need state in order to say something, look it up before speaking. If he has told you,
it is true.

**A check with no consumer is a display.** The tell that catches the above: if the result
cannot change your next action, you are performing diligence rather than doing it. I was going
to clean up the worktrees whichever way that diff came out.

**Document in one place and point at it.** The `/usr` hatch mechanics were written out in
seven files; moving the hatch into its own role made three of them false and one had been wrong
for weeks. Restating a fact is not redundancy with a safety margin -- it is a guarantee that
the copies diverge, and the one nobody edits is the one that goes stale. When two docs describe
one thing from different sides, trim the seam rather than add a link: a link makes the
duplication survivable.

**Organise by component, not by the subsystem you are inside.** The questions people ask are
"what is on this pin" and "what can we move". I put an FC pad survey in a doc about *time*
because time was what I was working on, which is grouping by my own task. `docs/rekon10/README.md`
has the map, keyed on the question. And when a fact belongs to another agent's layer, hand them
the text and let them place it -- they know what exists and what is being rewritten; you do
not, and a plausible-looking placement from outside is a guess nobody will check.

**Do not promote a property of the system into an invariant the tooling enforces on him.**
`/usr` is read-only and only a boot restores it became "a play that opens the hatch MUST
reboot", written into a doc as a rule. The converge stops the stack became "so the converge
must start it again". Each time the invariant was mine and I built it into the playbook, so the
playbook decided instead of the operator. He can deal with a writable `/usr` and a stopped
stack; both are states he may want. State the property, never a MUST.

**Do not write "today it is a person" or anything like it.** If a mechanism does not exist, say
it does not exist. A placeholder phrase that makes an unfinished design sound finished is worse
than an admitted hole, and it will be read as a plan.

**When he corrects a framing, check whether it also invalidates what you built on it.** Several
times I accepted a correction and then kept quoting a number that the correction had just
retired -- stop timings from a run whose camera had failed, first-frame as a readiness measure
after being told it was not one.


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
