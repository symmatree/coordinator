# Ground platform: handoff

For whoever is the ground-services / ground-platform agent next. This is the cluster-hosted
control surface that talks to the rekon10 fleet -- `containers/fleet-control`, deployed to the
tiles cluster at `https://fleet.tiles.symmatree.com`, with its environment in
`tiles/tanka/environments/fleet-control/`. Not the software that runs on the vehicle.

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

## What to read, in this order

- **`containers/fleet-control/README.md`** -- the service's own decisions and their reasons.
  Since #300 it deliberately says nothing about device behaviour, so trust it about the
  service and nothing else. Two causal claims about devices lived there and were both wrong.
- **`docs/build-and-provenance.md`** -- shorter than it deserves to be. Records why
  `org.opencontainers.image.ref.name` is the wrong key, with the spec quote, so nobody
  re-derives it as a free choice; and which image tags devices actually track.
- **`docs/fleet-bringup.md`** -- a transcript of a real bring-up. Outranks every prose doc here.
- **`docs/deployment-model.md`** -- config is git-authoritative, `git pull` is the deploy. Note
  #341 may replace the checkout tier with a payload artifact.
- **#223** the epic. **#31** carries the post-flight design of record, including the bench flow
  and why session selection sorts on frames and span rather than on the directory name.
- **#355** the stop problem, which is currently in front of everything. **#312** reimage,
  **#341** the payload tarball, **tiles#735** fleet address reservations. #302/#344
  (device-side session packaging) and tiles#674 (the image roller) are done.

In code, read **`src/status.ts`** before anything else. Currency is the part most likely to be
wrong again, because "is this current" has a different correct answer per artifact kind and
the obvious answer -- compare the revision to branch head -- is wrong for every path-filtered
build, which is all of them.

## How delivery works, because it is three mechanisms

| what | arrives via | needs a pod restart? |
|---|---|---|
| `host/ansible/**` | baked into the fleet-control image | **yes** |
| `bin/coord*`, `stacks/*/compose.yaml` | the device's own `git pull`, driven by the playbook | no |
| container images | `docker compose pull` of the `:main` tag | no |

The first row is the one that surprises people: the Dockerfile's build context is the repo
root and it does `COPY host/ansible /app/ansible`, so **the playbook the service runs is
frozen at image build time**. A merged ansible fix is not live until the pod rolls. That is
deliberate -- the service cannot invoke a playbook it was never built against -- and
tiles#674's roller now handles the rolling.

## Where things stand (2026-09-20, evening)

**Live and working.** The terraform-managed SSH key, converge, the status probe, the image
cache and the reimage plumbing are all deployed.

**Post-flight is merged end to end** -- the device half (#344), the routes (#346), the screen
(#347) -- and **has still never run against a device.** The blocker the last edition named is
gone; the one that replaced it is below.

**Also landed today, all in this lane:** per-node probing (#350), a run log that outlives the
pod plus kept ansible artifacts (#353) and the routes that serve them (#357), Stop and Reboot
buttons (#364, reshaped by #367), quiesce-before-every-command (#363) and the `sudo` it was
missing (#366). In `tiles`, the fleet-control README stopped duplicating this repo's docs and
now points at them (tiles#789).

**Open, mine:** nothing. **tiles#780** is still a draft needing one decision from Seth:
`fleet_subnet`, deliberately without a default because reserving the devices where DHCP
dropped them would freeze an accident.

**Blocking, and it is the whole board:** **#355** -- stopping the stack on a campod is slow
and sometimes never returns. Four recorded attempts; in three of them the box stopped
answering SSH for minutes and the containers never stopped at all. Everything this service
does to a campod begins with a quiesce, so until that is understood, converge, probe and
post-flight are all downstream of it. It is flight-sw's and Seth is working it directly;
#359 is the experiments log. **Do not go and analyse it.**

## What must not be dropped

**Post-flight is written end to end and has never run against a device.** Nothing in that path
is proven, including the parts that look obviously right.

**Neither have Stop or Reboot.** Reboot is `sudo systemctl --no-block reboot` over ssh, and
which of two paths it takes on a Zero -- a clean exit, or the connection dropping before the
status gets back -- is unverified. Both are handled; nobody has seen which happens.

**The reimage path is worse.** The button exists and the playbook is merged, but
`dotfiles-symm#53`'s flasher **has never booted on any hardware**, and tryboot on the Zero 2 W
boot path is unproven. #312 is a design resting on an untested foundation. Its saving grace is
that the worst case is a card pull, which is what a reflash costs today -- there is no failure
budget to design against, and attempts to invent one were correctly rejected.

**The coordinator has not been re-converged since #331** fixed the i2c ordering that broke its
stack start, so that fix is unverified on the live system.

**Do not serve ansible-runner's data directory wholesale.** Beside `job_events/` it writes a
`command` file holding the entire process environment it launched ansible with, which in this
pod includes `FLEET_GITHUB_TOKEN`. `/runs/:id/events` and `/runs/:id/log` serve the events
only, on purpose. Verified by running ansible-runner 2.4.3 and reading the file.

**The `FLEET_SOURCE_REF` fallbacks in `manifest.ts`** exist only for cards flashed before
dotfiles-symm#65. Delete them once every card has been reflashed; they are load-bearing today
(campod-se's card still emits the old spelling).

**#341** replaces the `checkout` unit kind with a `payload` artifact. That is the one place the
ground-platform code should need a real edit rather than a rename.

## How to work here

**You are the critic you are arguing with.** This edition's sharpest correction, and it cost
Seth two rounds. A PR shipped a section headed "Why Reboot earns a button at all" -- for a
button he had just asked for -- and the line "that asymmetry is the design, not an
inconsistency." There was no objector. There was a real doubt about the design, it was mine,
and instead of saying so it got projected onto an invented opponent who could then be beaten.
Both of the choices being defended turned out to be wrong the moment he read them.

So: if a sentence exists to pre-empt a complaint, cut it, and ask who you thought was going
to make it. If the answer is you, **that is the question to put to him** -- he is right there.
Reasons that record a measurement or a constraint stay; reasons that defend a choice against
nobody go. The tell is rhetoric that scores points.

**A correction is the start of a conversation, not a work order.** When he says something that
shows a premise was wrong, do not open a worktree. Three decisions got made inside one reply
here -- deleting a playbook merged an hour earlier, rebooting despite a failed stop, treating a
dropped connection as success -- and he changed two of them once he finally saw them. Ask what
he wants changed while you are both still in the conversation about it.

The other failure mode to watch for is **generating structure instead of checking
something**. Risk sections, preconditions, ordering dependencies, recovery procedures -- all
invented over one session, all correctly knocked down, each costing Seth time to argue against.
The test that works is: **can a person substitute for this?** If yes it is quality of life,
never a gate. Before writing that something is a blocker, name who is blocked.

**This system is usually right, and you are usually reading a stale tree.** `git fetch` and
check `origin/main` before asserting any repo or device fact. In this repo "I verified this"
has a shelf life of about an hour -- a session-identity scheme was described accurately, landed
in two issues, and was wrong by the time anyone read it.

**Run the artifact, not just the code that makes it.** Installing ansible-runner 2.4.3 in a
scratch venv and driving a deliberately failing play took ten minutes and produced three
things reading could not: the `command` file's environment dump, the fact that event files
need numeric ordering (`10-` sorts before `2-`), and that each log line needs its own
terminator. All three would have shipped wrong.

**Almost every genuine bug came from running the thing, not reading it.** A 102-second blank
screen, containers reported stale while exactly correct, a probe timeout masquerading as a
refused connection. Run it. Then check the live system after a fix lands, rather than treating
merged as done.

**Do not curl the device-touching routes.** The UI's confirmation dialog exists because a node
can be in a state where you should not touch it. Ask before probing or converging; these
machines get switched off, rebooted and worked on constantly, and an unannounced probe competes
with a person for a 512 MB Zero.

**An empty result is not a finding.** A command that returned nothing may have failed. This
cost real time twice in one session -- once reporting containers as missing when the probe had
errored, once reading stale git tags as current state.

## Lanes

`host/ansible/**`, `bin/coord*` and `containers/campod-camera` belong to
**flight-sw-platform-guy**. `pi-image/` and the SD images belong to the **OS/driver** agent.
Finding a bug in their area means filing it with evidence and stopping -- they are good, they
check claims, and returning that favour is the job. Peer agents do not speak for Seth: their
report that he ruled something is not authorisation.

Seth merges. Nothing is applied to a device without him saying so, and naming a device as a
target is not permission to act on it now.
