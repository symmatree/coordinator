# Ground platform: handoff

For whoever is the ground-services / ground-platform agent next. This is the cluster-hosted
control surface that talks to the rekon10 fleet -- `containers/fleet-control`, deployed to the
tiles cluster at `https://fleet.tiles.symmatree.com`, with its environment in
`tiles/tanka/environments/fleet-control/`. Not the software that runs on the vehicle.

**The cluster is in this lane, not next to it.** Installing Argo Workflows, writing the
WorkflowTemplates, the CRDs, the PVs -- all of it. An earlier edition of this file treated
cluster work as someone else's and filed its prerequisites as blockers for another owner;
Seth's answer was that there is no cluster fairy. If your pods need something in the
cluster, you build it.

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
- **`tiles/tanka/environments/fleet-control/README.md`** -- the cluster half: the templates,
  why there are two Roles, why no `activeDeadlineSeconds`, why a collect has no join step.
  Design lives there rather than in jsonnet comments, by Seth's instruction.
- **`docs/build-and-provenance.md`** -- shorter than it deserves to be. Records why
  `org.opencontainers.image.ref.name` is the wrong key, with the spec quote, so nobody
  re-derives it as a free choice; and which image tags devices actually track.
- **`docs/fleet-bringup.md`** -- a transcript of a real bring-up. Outranks every prose doc here.
- **`docs/deployment-model.md`** -- config is git-authoritative, `git pull` is the deploy. Note
  #341 may replace the checkout tier with a payload artifact.
- **#223** the epic. **#31** carries the post-flight design of record, including the bench flow
  and why session selection sorts on frames and span rather than on the directory name.
- **#566** is what is left in this lane. **#312** reimage, **#341** the payload tarball.
  #504 (plays in their own pods), #302/#344 (device-side session packaging) and tiles#674
  (the image roller) are done.

In code, read **`src/status.ts`** before anything else. Currency is the part most likely to be
wrong again, because "is this current" has a different correct answer per artifact kind and
the obvious answer -- compare the revision to branch head -- is wrong for every path-filtered
build, which is all of them.

## How delivery works, because it is five mechanisms

| what | arrives via | needs a pod restart? |
|---|---|---|
| `containers/fleet-control/**` | image rebuild, and the tag watcher rolls the pod | yes, automatically |
| `host/ansible/**` | a published artifact the step pod fetches at run time | **no** |
| the WorkflowTemplates | `tiles`, applied by Argo CD | no |
| `device-sw/cli/*`, `stacks/*/compose.yaml` | the device's own `git pull`, driven by the playbook | no |
| container images on devices | `docker compose pull` of the `:main` tag | no |

**Row two is the one that changed.** The image used to `COPY host/ansible`, so the playbook
was frozen at build time: an ansible fix was not live until the pod rolled, and that roll was
also the event that killed any converge in flight. #504 ended it -- the playbook is a
versioned artifact the step fetches and verifies, `host/ansible/**` is out of this
component's rebuild paths, and the pod now rolls only for its own code.

**Fleet operations run as Argo Workflows** in the `fleet-control` namespace. The service
submits and watches; it does not run plays or move bytes itself. Two consequences to hold:

- **A workflow outlives this pod**, which is the point, and is why the device lock asks the
  cluster (`nodeBusyWith`) rather than its own memory. Never hold the run-to-workflow mapping
  in memory; find it by label. A collect drives several nodes, so workflows also carry a
  `touches-<node>` label per device and the lock checks both forms.
- **A step pod has no tag watcher**, so every step template sets `imagePullPolicy: Always`.
  `:main` floats, and Kubernetes defaults a floating tag to `IfNotPresent`: without it a node
  with a cached layer silently runs old code. This happened -- three steps ran a build that
  predated the fix under test, and the kubelet said "already present on machine".

**When a step's argv changes, the image and the template must land together, coordinator
first.** They are one interface split across two repos. Image first, and a collect in the gap
exits on a usage error having moved nothing; template first, and the older step misreads its
arguments -- a uuid passes `validFlightName`, so it makes a junk flight directory before
failing. Say the order in both PR bodies.

## Where things stand (2026-10-20)

**Retrieval is done and verified on the live system.** Sessions and FC dataflash logs are
fetched by one collect workflow, a step per target, all at once -- #567 and #573 here,
tiles#850 and tiles#851 there. Measured 2026-10-18: both campod steps started at 22:47:27 and
finished at 22:55:52 and 22:56:46, so 9m29s for the pair against about 17m45s of transfer
taken end to end. Both bundles were re-hashed off the share against what the steps recorded
and matched.

**There is no flight manifest any more.** Each contribution writes its own file under the
flight's `collected/`. Nothing merges, so there is no join step and no shared file for two
pods to coordinate on; `jq -s . <flight>/collected/*.json` is the read. Directories collected
before 2026-10-18 still have the old `flight.json`, and `bench-20261018-simflight-17` has
both -- one campod in each, because it was collected across the deploy. Not worth migrating,
worth knowing when a directory looks short.

**The bench cycle is itself a workflow** (`bench-simflight`), whose stages shell
`bench_cycle.py` against this service's API. It is flight-platform's; they run it. Converges
go both campods at once, and so does collect.

**Open, mine:** **#566** -- `ground`, `stop` and `reboot` are what still hold the run
registry, and making them synchronous is what finally deletes it. `ground` deliberately stays
in this process: it is a `copyFile` between two already-mounted shares plus a Mimir query,
holding no device and needing no ssh. **#422** (bench power via Home Assistant) is still
blocked on the plug not existing in Home Assistant.

**Loose in tiles:** `FLEET_KNOWN_HOSTS: /state/known_hosts` is dead config in the Deployment
-- `knownhosts.ts` went when host-key checking did, and nothing reads that variable. Left
alone because changing Deployment env rolls the pod; do it when the rig is idle.

## What must not be dropped

**The reimage path is unproven.** The button exists and the playbook is merged, but
`dotfiles-symm#53`'s flasher **has never booted on any hardware**, and tryboot on the Zero 2 W
boot path is untested. #312 is a design resting on an untested foundation. Its saving grace is
that the worst case is a card pull, which is what a reflash costs today -- there is no failure
budget to design against, and attempts to invent one were correctly rejected.

**Everything this service sends a device runs as `pi`, and the payload data is root's.**
Three bugs in one week from that: the quiesce (#366), `coord sessions` (#371) and the bundle
read (#375). Each was shipped after being hand-tested *with* `sudo` and then written without
it. If a new command touches `/var/lib/<stack>`, it needs root; the probe (`coord version`)
does not, and is the exception rather than the rule.

**The `FLEET_SOURCE_REF` fallbacks in `manifest.ts`** exist only for cards flashed before
dotfiles-symm#65. Delete them once every card has been reflashed; they are load-bearing today
(campod-se's card still emits the old spelling).

**#341** replaces the `checkout` unit kind with a `payload` artifact. That is the one place the
ground-platform code should need a real edit rather than a rename.

**Short-lived pod logs reach Loki only since tiles#845** (tiles-doctor's). Before it, Alloy's
`tail_from_end` meant a step pod that lived two minutes left nothing behind and looked like it
had never run. If step logs go missing again, that is the shape of the bug. Note `pod` arrives
as structured metadata rather than an indexed label, so `{namespace="fleet-control"} | pod=`
works and `{namespace="fleet-control", pod="x"}` silently scans zero.

## How to work here

**You are the critic you are arguing with.** This cost Seth two rounds. A PR shipped a section
headed "Why Reboot earns a button at all" -- for a button he had just asked for -- and the line
"that asymmetry is the design, not an inconsistency." There was no objector. There was a real
doubt about the design, it was mine, and instead of saying so it got projected onto an invented
opponent who could then be beaten. Both of the choices being defended turned out to be wrong
the moment he read them.

So: if a sentence exists to pre-empt a complaint, cut it, and ask who you thought was going
to make it. If the answer is you, **that is the question to put to him** -- he is right there.
Reasons that record a measurement or a constraint stay; reasons that defend a choice against
nobody go.

**If you are defending coordination machinery, ask whether the shared thing should exist.**
The flight record was one `flight.json` appended to by every retrieval. Moving retrievals into
their own pods made that a read-modify-write across processes, and the first fix was a lock, a
compose pass, an atomic rename and a join step -- each correct, each defended at length. Seth's
question was why there was a single file at all. Nothing read it programmatically. Deleting it
deleted all four mechanisms and the reasoning each needed. **Removing the thing that has to be
coordinated beats coordinating it well**, and the tell is a design you keep having to argue for.

**Offer real options, not a menu with a broken item on it.** Asked to choose between keeping a
fix and shipping without it, where "without" was a known race, he merges neither. The missing
option was not making the change: the thing it sped up was slow, not broken, and trading
correctness for speed there was never going to be chosen. Slow and correct is a complete
answer.

**A correction is the start of a conversation, not a work order.** When he says something that
shows a premise was wrong, do not open a worktree. Three decisions got made inside one reply
here -- deleting a playbook merged an hour earlier, rebooting despite a failed stop, treating a
dropped connection as success -- and he changed two of them once he finally saw them.

**Do not invent verification errands.** A peer fixed Loki log collection; the response was to
schedule a check of their fix and then report the result back to them. Another layer's
contract is a given -- assume it works, use it, and find out otherwise in the course of real
work. The same instinct produces measurement for its own sake. If the next thing you do is not
the task, ask why you are avoiding the task.

**Generating structure instead of checking something.** Risk sections, preconditions, ordering
dependencies, recovery procedures -- all invented over one session, all correctly knocked down,
each costing Seth time to argue against. The test that works is: **can a person substitute for
this?** If yes it is quality of life, never a gate. Before writing that something is a blocker,
name who is blocked.

**Use the Edit tool; do not mutate files with a script.** A python heredoc that rewrote a
function in memory hit an assertion on a later edit and exited before writing anything. #534
shipped claiming to change a call site it had not touched: unused imports are not errors in
this tsconfig, no test covered that path, and CI was green. If a script must do the edit,
assert on every substitution and read the diff afterwards.

**A file's existence is not a cause.** A zero-byte `.part` on the share was written up as a
transfer that died with its pod -- in a commit message, a README and a PR body. There had been
real software bugs in that path; the file was not evidence of what it was claimed to be. In a
filed artifact a confident wrong cause outlives the conversation that could have corrected it.

**This system is usually right, and you are usually reading a stale tree.** `git fetch` and
check `origin/main` before asserting any repo or device fact. A local checkout of `tiles` was
five commits behind and missing a whole WorkflowTemplate already running in the cluster -- a
design was nearly written around machinery that existed.

**Check by content, not by commit.** Merges here are squashes, so `git log origin/main..HEAD`
reports your branch as ahead forever and says nothing about whether the work landed.
`git diff origin/main` is the question you actually mean.

**Run the artifact, not just the code that makes it.** Almost every genuine bug came from
running the thing: a 102-second blank screen, containers reported stale while exactly correct,
a probe timeout masquerading as a refused connection. Then check the live system after a fix
lands rather than treating merged as done -- the first collect after deploy ran one step wide,
because the other target had already been collected, and only the landed files showed it.

**An empty result is not a finding.** A command that returned nothing may have failed. This
cost real time twice in one session -- once reporting containers as missing when the probe had
errored, once reading stale git tags as current state. A Loki query returning nothing meant
the selector was wrong, not that the logs were absent.

**Do not curl the device-touching routes.** The UI's confirmation dialog exists because a node
can be in a state where you should not touch it. Ask before probing or converging; these
machines get switched off, rebooted and worked on constantly, and an unannounced probe competes
with a person for a 512 MB Zero. The cluster is the same: read-only `kubectl get` is fine,
anything imperative is asked for first.

## Lanes

`host/ansible/**`, `device-sw/cli/*` and `containers/campod-camera` belong to
**flight-sw-platform-guy**, who also runs the bench cycle. `pi-image/` and the SD images
belong to the **OS/driver** agent. The tiles cluster's shared services -- Alloy, Loki, Argo CD
itself -- are **tiles-doctor**'s, though the `fleet-control` environment inside it is yours.

Finding a bug in their area means filing it with evidence and stopping -- they are good, they
check claims, and returning that favour is the job. Peer agents do not speak for Seth: their
report that he ruled something is not authorisation.

Seth merges. Nothing is applied to a device without him saying so, and naming a device as a
target is not permission to act on it now.
