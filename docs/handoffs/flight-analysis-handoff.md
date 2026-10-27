# Flight analysis: handoff

For whoever is the flight-analysis agent next. This role extracts findings **from what the
vehicle recorded** -- flight dataflash, campod accel and stills, coordinator tlogs and
captures -- and turns them into numbers someone can act on. Distinct from flight-platform
(software that runs on the vehicle) and ground-platform (the cluster-hosted control surface),
both of which have their own handoffs --
[`docs/handoffs/ground-platform-handoff.md`](ground-platform-handoff.md) here, and
[`docs/flight-platform-handoff.md`](../flight-platform-handoff.md)

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

## Where things stand (2026-10-27)

**Open PRs from this role:** coordinator#626 (spectrogram transfer function never
interpolates across a hole) and #627 (`tlog_read.py` and the three-observer section for
#229). Merged this session: #605 (row sharpness, band RMS), #607 (ledger topic 7), #613
(ledger measured baseline), #619 (notebook move + per-flight set), #620
(`time-sync.ipynb`), #625 (layout doc: provenance per log), tiles#871 (runner runs the
per-flight set, extracts bundles), tiles#874 (runner: DataFlash check, per-log provenance,
timeouts, job deadline). coordinator#211 closed as completed.

**The one flight with everything on it is `2026-10-25-soft-mount-check-flight`:** three
FC boots (`fc-log-20/21/22`), three coordinator sessions, two campods with arm and
camera-mount ADXLs, 5 ms exposure at `infinity`, three-blade set B, motor soft mounts.
Its measured state is `analysis/image-quality-experiments.md`, *Measured baseline,
2026-10-25*; everything below is from it unless said otherwise.

**Stills are not usable (operator):** about a tenth of each frame is sharp and which rows
those are changes frame to frame. Bands are present on the campod (215/230 SE frames fit
one) and the SE band frequency tracks Motor2's rev frame by frame (r 0.80); Motor2 is the
slowest motor (82.7 Hz) and the amplitude peaks when it runs 76-79 Hz. The camera-mount
ADXL reads 0.5-0.8 m/s^2 at the rev lines and up to 4 m/s^2 at blade-pass against a bench
floor of 0.02-0.03. Rev lines sit at 83-115 Hz; `docs/campod.md` was written against
~330 Hz, and its own VCM-resonance paragraph (80-200 Hz) now has forcing inside it.

**Motors:** throttle output 0.18 in hover; front/rear thrust split 1.55 (CG ~35 mm
forward), left/right 0.97, and the two spin directions 0.82 with YOut -0.037 -- the last
is new against 260730/260814 (coordinator#169 has the table).

**The campods now carry Arducam B0310 modules** (IMX708 behind a fixed M12 lens, 120 deg H,
no VCM, no `LensPosition`), from 2026-10-26; factory focus unknown; campod-se's camera did
not probe on its first boot (os-and-driver's finding). Pixel amplitudes are not comparable
across that change. An ADXL is taped to the module housing on these (operator). The
operator's direction, not a plan of this role's: envelope-first -- find one configuration
that gives sharp or sharp-jello frames, then expand from it; fixed lens, 1 ms exposure
(a `campod-exposure-1ms` branch exists, not mine), battery back on top to centre the CG,
cameras on a raft, the Blackfly for global shutter; VIO is off the table.

**Time sync, measured end to end (`time-sync.ipynb`):** every UTC label on the vehicle
(stills by two routes, coordinator, FC RTC) sits +25 to +32 ms ahead of GPS, within a few
ms of each other. FC `TimeUS` against GPS: its second is long by 253-298 ppm (three boots,
ten minutes apart) -- earlier text called the same number "fast"; state the convention.
Against the coordinator's PPS-disciplined clock the FC reads 272-295 ppm by two routes, so
"pulse vs GPS" by difference comes out -19 to +42 ppm across the three logs: not a
measurement of the DS3234 yet. The coordinator locks to the pulse 20-22 s after boot
(chrony slewing at 83 ms/s), then 3-20 us sd; pods stepped 195 days at boot, campod-sw sat
95 ms off for 74 s before locking; during the flight all three are within 5 us sd. Pod vs
pod same-second exposures: median 388 us, sd 287 us (design figure 52 us; coordinator#538
has the method). Zero `CAM` rows in all three logs: the FC recorded no PPS edges that day.
The ground tlog was a 0-byte `.tlog.part`. `vehicle.tlog` holds 11343 FC frames against
11317 the FC counted sending on that channel, zero sequence loss.

**The per-flight set runs nightly:** `flight-analysis` per `.bin`, then per flight the
bundles extracted into `captures/`, `time-sync`, and `vibration-spectrogram` and
`image-sharpness-vs-motion` per pod on the `.bin` `time-sync.json` names for it, with
`derived/polisher.json` provenance. The first nightly after tiles#871 processes every
flight dir on the NAS; whether it completed has not been checked (needs `kubectl`, ask).

**Still open from earlier:** the 152.9 Hz hand-held strike-test mode and the six-pose
calibration stand as written in the 2026-10-15 version of this file (git history); the
twelve-pose set is unflown.

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
