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
- **#312** reimage, **#302**/**#344** device-side session packaging, **#341** the payload
  tarball, **tiles#674** the image roller, **tiles#735** fleet address reservations.

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

## Where things stand (2026-09-20)

**Live and working.** The terraform-managed SSH key, converge, the status probe, the image
cache and the reimage plumbing are all deployed. campod-se converged successfully once --
16m12s, `ok=58 changed=37 failed=0` -- with camera and accelerometer attached for the first
time and both payload containers running.

**Open, mine:** #343 (a probe timeout reports itself as one), #346 (post-flight API plus
stop-capture), #347 (post-flight screen), #350 (per-node probing; fixes a 102-second blank
screen). **tiles#780** is a draft needing one decision: `fleet_subnet`, deliberately without a
default because reserving the devices where DHCP dropped them would freeze an accident.

**Blocking:** #344 is flight-sw's and is the device half of post-flight. Until it merges,
`coord sessions` exists nowhere and #346/#347 cannot be exercised at all.

## What must not be dropped

**Post-flight is written end to end and has never run against a device.** Nothing in that path
is proven, including the parts that look obviously right.

**The reimage path is worse.** The button exists and the playbook is merged, but
`dotfiles-symm#53`'s flasher **has never booted on any hardware**, and tryboot on the Zero 2 W
boot path is unproven. #312 is a design resting on an untested foundation. Its saving grace is
that the worst case is a card pull, which is what a reflash costs today -- there is no failure
budget to design against, and attempts to invent one were correctly rejected.

**The coordinator has not been re-converged since #331** fixed the i2c ordering that broke its
stack start, so that fix is unverified on the live system.

**The `FLEET_SOURCE_REF` fallbacks in `manifest.ts`** exist only for cards flashed before
dotfiles-symm#65. Delete them once every card has been reflashed; they are load-bearing today
(campod-se's card still emits the old spelling).

**#341** replaces the `checkout` unit kind with a `payload` artifact. That is the one place the
ground-platform code should need a real edit rather than a rename.

## How to work here

The failure mode to watch for in yourself is **generating structure instead of checking
something**. Risk sections, preconditions, ordering dependencies, recovery procedures -- all
invented over one session, all correctly knocked down, each costing Seth time to argue against.
The test that works is: **can a person substitute for this?** If yes it is quality of life,
never a gate. Before writing that something is a blocker, name who is blocked.

**This system is usually right, and you are usually reading a stale tree.** `git fetch` and
check `origin/main` before asserting any repo or device fact. In this repo "I verified this"
has a shelf life of about an hour -- a session-identity scheme was described accurately, landed
in two issues, and was wrong by the time anyone read it.

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
