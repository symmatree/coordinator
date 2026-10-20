# Flight analysis: handoff

For whoever is the flight-analysis agent next. This role extracts findings **from what the
vehicle recorded** -- flight dataflash, campod accel and stills, coordinator tlogs and
captures -- and turns them into numbers someone can act on. Distinct from flight-platform
(software that runs on the vehicle) and ground-platform (the cluster-hosted control surface),
both of which have their own handoffs --
[`docs/handoffs/ground-platform-handoff.md`](ground-platform-handoff.md) here, and
[`docs/flight-platform-handoff.md`](../flight-platform-handoff.md) one level up, along with
`os-and-driver-handoff.md`. Moving the remaining two in is their owners' call, not something
to do to them.

The boundary is not the directory. When a measurement turns out to be impossible because a
field is miscounted or a control is wrong, fixing that is in scope -- several of this role's
PRs have landed in `containers/`. What is *not* in scope is owning the collection path: the
ground platform files flights, the flight platform converges devices, and this role is their
consumer.

## The charge on this file

Update it at the END of a session, not during. A working period is not a place for small
edits to a handoff.

Two subtractions keep it honest. When something here becomes a durable fact about the system
it graduates to a real doc -- `docs/flight-data-interpretation.md` is where most of this
role's findings have gone. When it becomes work it becomes an issue. What is left is what
would otherwise be re-learned by collision. **"Where things stand" goes stale fastest** --
treat a date older than a week or two as archaeology and check `origin/main`.

## What we are actually trying to do

**Stills sharp enough to feed ODM.** That is the deliverable; everything else is downstream.
Operator's words: *"I would like to get stills that look like I used to get from the DJI,
which I could feed to odm. Everything else is downstream of that."*

Two consequences that are easy to lose:

**Sharpness is the feature-tracking problem, not a separate one.** ODM must reconstruct poses
from matched features across the collection run before densification matters at all. So "are
the stills sharp" and "can they be tracked" are the same question asked twice, and the second
gates a map. An earlier version of the theory ledger treated them as separate and had it
backwards.

**Envelope control bounds the work.** The goal is not to explain every observation or make
every frame good -- it is to find a region of operating conditions that reliably produces
acceptable results, which is usually enough for the mission and which turns every later
experiment into a narrow diff against that region's edges. *"If we can hover to within a few
cm of drift, I can ignore motion blur and just ask why I can't get sharp stills in hover."*
"Why is this blurry" is unbounded; "is there any configuration in which it is sharp, and
where are that island's edges" terminates.

## What to read, in this order

1. **`analysis/image-quality-experiments.md`** -- the live theory ledger for this role. Open
   topics, each with what has been observed and what would decide it. Nothing in it is marked
   confirmed, deliberately.
2. **`docs/flight-data-interpretation.md`** -- what the recorded data *means*. Clocks, what
   each stream is good for, and a long list of things that would be reasonable to assume and
   are wrong. Most of this role's durable findings live here.
3. **`docs/flight-data-layout.md`** -- where things are on the NAS. Note coordinator#431: the
   collection writes `flights/<flight>` while this doc specifies
   `flights/<platform>/<flight>`, so three flights are currently misfiled and one flight
   exists as two directories.
4. **`facts/fables/Datasets/experiments-house-model.md`** -- not in this repo, and the process
   model the ledgers follow. Read it for the form: graded tiers, dead theories kept struck
   through with what killed them, a discriminating probe named per theory.
5. **`docs/rekon10/vibration-testing.md`** -- instruments and method for the accel work. Kept
   deliberately free of interpretation; the arguments live in the theory ledger.
6. **`analysis/README.md`** -- index of what each module and notebook does.

## Where things stand (2026-10-15)

**Open PRs from this role:** #421 (six-pose calibration -- method, `pose_calibration.py`,
tests, results), #503 (clock findings into the interpretation doc), #514 (campod does not
hard-fail on a fixed-lens module). #404 merged, carrying the vibration investigation. #419
merged -- campod focus is `infinity` on main, **not yet converged**.

**The 152.9 Hz arm mode is the one new structural number**, from the 260926 strike test:
152.92 Hz on the camera-colocated sensor and 152.94 on the arm-end one, 160 ppm apart, on 3
and 5 of 16 strikes. Nothing drives it at current hover RPM -- nearest line is motor2's 2nd
harmonic 21.4 Hz above. **The base was held by hand and moved under every strike**, so it is
the mode of a hand-restrained assembly, and the damping is unusable: Q 14 against Q 40 at one
frequency, unexplained.

**The biggest single finding was a bug, not a measurement.** The spectrogram computed
`2 * rev` and called it blade-pass on a three-blade aircraft. Computing order 3 turned up the
largest line anywhere on the airframe -- motor2's blade-pass at 261.80 Hz, PSD 7.187 on the
arm-end sensor against 4.286 for its own rev line -- on the arm both sensors are bolted to.

**Focus was the live defect and it is fixed but unflown.** The campod was focused at 1.25 m
while photographing ground at flight altitude: a **2 px defocus floor on every survey frame
to date**, which no exposure cap or mount change could lift. #419 sets `infinity`. So every
still we have was taken through a known defect, and nothing about what remains is measured.

**Six-pose calibration is done and bounded.** Scale and bias per axis for both ADXL345s,
camera-to-arm 94.26 deg about +z, and a three-instrument closure with the FC: 144.85 - 50.82
= 94.03 deg against 94.26 measured directly, agreeing to 0.23 deg. The ~1 deg residual is
**the part's own +/-1% cross-axis spec** (0.57 deg), so nothing is left to explain there. Not
settled: the fit is exactly determined at six orientations, so its own residual is not
evidence. Twelve would fix that, and `pose_set_identifiability` /
`incremental_identifiability` / `ellipsoid_identifiability` score candidate sets.

**Clocks, measured:** FC `TimeUS` runs **+242 to +313 ppm** fast against GPS and moved 71 ppm
between two flights three days apart, so fit it per flight. `SYSTEM_TIME` is **not**
GPS-authoritative -- on 260923 it was a day behind its own GPS, almost certainly sourced from
the coordinator's stepped clock. `vehicle.tlog` concatenates FC boot sessions with no marker.
The campod-to-FC tie was +/-0.4 s before PPS, which is the loose link the PPS work fixes.

**In flight, unresolved:** whether soft mounts plus correct focus produce acceptable stills.
That is the question everything else waits on. Hardware acquired toward it: a FLIR Blackfly
BFS-U3-244S8M-C (24.5 MP IMX540 **global shutter** mono) with a Kowa LM16XC 16 mm f/2.0, four
Arducam IMX219 **mechanically fixed-focus** modules, and IMX708 + M12 modules -- the last
being the single-variable test against the current Camera Module 3, same sensor and readout,
differing only in that the lens cannot move.

## What must not be dropped

**`gap_ns` is not a loss detector on its own.** It finds concentrated holes -- the 500 ms
events that corrupt a decay fit -- and is **structurally blind** to loss spread one sample at
a time. On a seven-hour session a drop counter reached 144k, 44 s of dark time and 0.18% of
the session, while `gap_ns` found six gaps over 10 ms all night. Both correct: a discard
extends the next `gap_ns` by ~0.31 ms against a *median* gap of 0.50 ms. "No stalls" is not
"no loss".

**The fitted ADXL rate does not bound loss.** Rate is `(true ODR) x (1 - loss)`, so a constant
loss fraction scales it and leaves it flat. Flatness bounds the *variation*. With no ODR
tolerance in the datasheet there is nothing to compare the absolute against. Corollary:
absolute ADXL rates are emitted-sample rates, so a reported -1.21% is really -1.08%.

**`ovr` cannot classify a short read.** `entries()` returns early when `n < fifoDepth` without
reading `INT_SOURCE`, so `ovr` is false by construction on every short batch -- and a short
read is the normal case at mean n of about 2 into a 32-deep FIFO.

**Blade count is a per-flight fact, and it is three.** Blade-pass is blades x rev, so calling
order 2 blade-pass misplaces a named line by a third of its frequency.
`docs/rekon10/props.md` is the ledger. A two-blade set is on hand and unflown.

**`campod-sw` is on the rear-left arm**, motor2's. Nothing else in the repo recorded it, and
every per-motor amplitude is read against it -- the loudest lines turn out to be the sensors'
own motor, which is only interpretable once the mounting is known.

**The magnitude constraint determines an ellipsoid, not a full gain matrix.** `|a| = |g|`
gives 9 parameters -- symmetric 3x3 plus bias -- and can never see the package rotation.
Fitting a full 12-parameter model from magnitude data alone is *unidentifiable*, not badly
conditioned: it ran away to 4000 g biases with the matrix shrinking to compensate. Counting
degrees of freedom is necessary and not sufficient.

**Notebooks must not use `matplotlib.use("Agg")`.** It renders into a buffer nobody collects,
so the executed notebook has zero embedded images and a reader gets the prose and none of the
figures. This cost a PR review.

**Report a symptom, not a cause, on someone else's subsystem.** An FC log pull failed with
"nothing answered on `/dev/ttyAMA0`"; two causes were named and both were wrong -- the FC was
answering but still listing, and a registered link discards data requests silently. Identical
symptoms from different causes is the trap.

## How to work here

**He decides what to fly. Never phrase analysis as a requirement.** No "what I'd want from
that flight", no wishlists, no next steps aimed at him. State what a measurement would show
and stop. This was the most repeated correction in the session that produced this file, and
the phrasing made the conversation unpleasant for him to come back to.

**Start with a proposal of a first step, get agreement, test it numerically, then go on.** Not
a finished conclusion with the derivation attached. *"You shouldn't be presenting conclusions
and jumping to other ones."*

**Deliver notebooks in accessible folders, not tables in chat or figures in `/tmp`.** And run
them -- a test plan is not testing.

**Nothing is confirmed without his agreement.** "Confirmed" and "evidence" both mean *stop
interrogating this*, and they are his to grant. A thing that happened once in one flight is an
observation. Grade findings, and keep dead theories struck through in the doc rather than
retracting them in chat where the next reader never sees it.

**One PR per conversation topic.** Not per directory, not per the order things were built. A
measurement and the doc that interprets it are one topic. Five PRs in that session should have
been two, and he said so.

**Announce long device operations as they start.** A 1 GB offload ran while he was being told
the data could not be fetched; he merged a PR on the strength of that, and the merge rolled
the pod and killed the transfer. He cannot protect work he does not know exists.

**His statements about his own system are given, not claims to verify.** When he says the FC
is powered, it is powered. Two wrong causes were named for one failure after being told that.

**Ask "to what precision?" before calling something insufficient.** A +/-25 ppm scope was
called inadequate for absolute timing when it is 80-2000x better than every vibration purpose
here. The only thing it cannot do is verify a TCXO datasheet number that nothing needs.

**Peers will be wrong, and so will you -- check before building on it.** One peer's figure was
186M where the truth was 144k, and a careful analysis got built on it. Another's framing was
adopted and then had to be withdrawn. Verify the input when the conclusion depends on it, and
say which part is established versus inferred.

**Do not argue in docs or code comments.** He only finds it in review, days later, and it
reads as trying to have the last word. Say it in conversation.

**Work in a fresh worktree off `origin/main`, with a distinctive name.** Writing this file, a
generic worktree path collided with another agent's tree and a file got dropped into their
working directory. Check `git worktree list` and stop on anyone else's uncommitted work.
