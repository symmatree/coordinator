# fleet-control

Converge the rekon10 fleet from a phone instead of a laptop. Runs in the cluster on
linux/amd64. Child of [#223](https://github.com/symmatree/coordinator/issues/223), filed as
[#236](https://github.com/symmatree/coordinator/issues/236).

Live at `https://fleet.{cluster}.symmatree.com`; deployment lives in `tiles`.

## Converge, and full provision

```sh
curl -sXPOST .../nodes/campod-se/converge                    # config, stacks, images
curl -sXPOST .../nodes/campod-se/provision                   # a fresh card, end to end
```

| | playbook |
|---|---|
| **Converge** | `deploy.yaml` |
| **Full provision** | `site.yaml` |

**Converge is the one pressed between flights**, so it is the default and the primary button. Full
provision is the first-run path; on a card that is already provisioned it is a slow way to get a
converge.

What each playbook installs, reboots or leaves behind is documented with it and changes without
this file knowing -- the "reboots partway" that was here was true for about a day.

They are two operations rather than one at two sizes, which is why they are two routes and not a
size flag. After the playbook split in two
([#453](https://github.com/symmatree/coordinator/pull/453)) this service ran `site.yaml` for
everything, so a routine converge paid the full apt set, the `/usr` hatch and a reboot.

Exit 0 means converged either way. What each playbook does is documented with it:
[`host/ansible/`](../../host/ansible/) and
[docs/deployment-model.md](../../docs/deployment-model.md).

> **Scope of this file.** It documents the *service*. **What the playbook does, which playbook
> runs when, and what state a device is left in are documented with the playbook** --
> [`host/ansible/`](../../host/ansible/) and
> [docs/deployment-model.md](../../docs/deployment-model.md). This file used to paraphrase them
> and the paraphrase went stale: it claimed every converge reboots the device and that the play
> starts the stack on its way out, both of which stopped being true at
> [#453](https://github.com/symmatree/coordinator/pull/453) with nothing here to notice.
>
> A device's actual state comes off the pod bus (see below), not from anything this service
> believes about the play.

**Budget twenty minutes or more on a campod**, against a few minutes for a Pi 4B. Measured from
this side -- wall time for one converge driven from here -- so it is this service's to state.

### The key a device trusts is the key we present

`roles/ssh-access` writes `authorized_keys` **whole**, so the play has to be told every key it
should contain. The humans are a committed list in `host/ansible/group_vars/all.yml`, which is
the point of that arrangement -- operator access as a reviewable diff.

**The fleet key is not committed anywhere.** This service derives it from the private key it is
about to authenticate with (`ssh-keygen -y`) and passes it as the `fleet_ssh_public_key` extra
var. So what a device is told to trust is definitionally what reached it, and there is no second
copy to drift.

**The play refuses to write the file if it was not told.** An `assert` ahead of the template, so a
run that cannot name the driving key costs a re-run instead of a card.

Worth knowing if you ever drive the play by hand: a public key contains a space, and ansible's
`-e key=value` form stops at the first one -- it silently yields the string `ssh-ed25519`. Pass it
as JSON. This service writes `env/extravars` as JSON and is unaffected.

### Host keys are not checked

`StrictHostKeyChecking=no`, with both `known_hosts` files at `/dev/null`, for ssh and for
Ansible alike (`src/ssh.ts`).

These are physical devices on a private network that get **reflashed routinely**, and a
reflashed card presents a new key for the same address — a *changed* key, which no amount of
`accept-new` accepts. So checking produced exactly one outcome in practice: a bare
`UNREACHABLE` from a card that had just been written.

What grew around that was a whole mechanism to avoid the refusal: a module to forget a
recorded key, a `?reflashed=true` query parameter and a second button to trigger it, a
`reimage_known_hosts` variable so the reimage play could do the same from the other end, and a
volume to keep the record on. All of it existed to stop ssh refusing to connect.

**Recording keys only to clear them on the one occasion they would ever differ is a check with
no failure mode left.** The threat it would have caught is someone impersonating a campod to
feed us camera frames and accelerometer samples, which is not on this fleet's radar. Removed
rather than kept as a costume.

## Stop and reboot

```sh
curl -sXPOST "https://fleet.tiles.symmatree.com/nodes/campod-se/stop"
curl -sXPOST "https://fleet.tiles.symmatree.com/nodes/campod-se/reboot"
```

**Stop** asks the device over the pod bus to stop collecting, and returns when the device
reports the units are actually inactive. One message, no playbook and no ssh: what sits between
us and the stop is the whole question on a box that cannot `stat` a file inside its own timeout
([#362](https://github.com/symmatree/coordinator/pull/362)), and pod-link is already resident
and already connected. It is **not a sticky off**: the units are enabled at boot, so the stack
returns on the next power cycle and nothing has to remember to undo this. Keeping a device down
across a reboot means disabling that unit, which is a deliberate act and not a button.

**Every command this service sends a device is preceded by a quiesce**, including the read-only
ones -- `docker ps` measured 2.1-22.0 s with capture running against 136-223 ms without. That
quiesce was `sudo pkill -x -TERM dumb-init` over ssh with a 40 s poll loop, which is two
problems. It forked an sshd and then polled from the far end of it, on the box least able to
afford either -- the paging cost arriving at exactly the moment pod-link exists to avoid. And
`pkill -x dumb-init` matches **every** container init, so it stopped pod-link and the broker as
well: it killed the thing carrying the instruction to undo it. The bus quiesce stops only the
units pod-link is configured to stop, and pod-link is never among them.

**What that gives up:** the ssh signal needed nothing of ours installed, so it reached a card
that had never converged. This needs pod-link running. A device that has not been provisioned,
or whose pod-link is down, cannot be stopped from here and **the command fails rather than
falling back** -- ssh by hand is the way through. The fallback was the thing being removed, and
it would have fired exactly when the device could least afford it.

**The wait is the device's now.** `StopUnit` returns when the job is accepted, not when the unit
is gone; measured on campod-se, the camera exited 19.0 s after the old command had already
returned. pod-link polls systemd until each unit is inactive and then publishes, so a caller
that reads the device straight afterwards is no longer racing a shutdown in flight.

**Reboot** is one command over ssh, `sudo systemctl --no-block reboot`, **gated on nothing**.
It does not stop the stack first: a reboot is the way out of a stuck box -- the thing that was
otherwise done by hand over ssh or by pulling power -- so it must not depend on anything else
working. The containers get systemd's shutdown signal on the way down; press **Stop** first if
you want them to go on our timeout instead.

**Including not gated on the node being busy, and it abandons what was running.** One action per
node is the rule everywhere else, and for a while it applied here too -- so a converge that wedged
held the slot and Reboot, the way out, was the one thing refused. Both here and on the screen,
which disabled the button ([#424](https://github.com/symmatree/coordinator/issues/424)). Now the
node's in-flight runs are marked abandoned first, which is what frees the slot, and that is a fact
rather than a licence: **rebooting the box ends those runs**, so a run still reading `running`
afterwards is wrong and holds the slot against a converge or stop that would now work. The
confirmation names what it is about to end.

Converge and Stop keep the lock. Two converges against one device fight over the docker daemon.

**An abandoned run stays abandoned.** Ansible does not stop because this stopped believing in it,
so the play settles later -- and without a guard that would flip the status back and send a second
notification for a run the operator already watched end. First writer wins.

**Only reboots this service performs.** A device that went down to a power cut, a hand-run
`reboot`, or the playbook's own reboot still leaves a run that only a pod restart clears. Nothing
tells this service a box went away, so that gap is not closable from here.

**Nothing waits for it to come back.** Same as everything else here: the device answers again
or it does not, and the status screen is the check
([#326](https://github.com/symmatree/coordinator/issues/326)).

`--no-block` queues the job and returns rather than blocking on the transition, so ssh gets a
real exit status instead of racing sshd's own shutdown. That race is not fully closable from
here, so a connection that drops (`closed by remote host`, `Broken pipe`, `Connection reset`)
is read as the reboot starting, while `Connection timed out`, `refused` and a rejected key
still fail -- those mean no command got in at all.

## The FC's dataflash logs

The third thing a flight is assembled from, beside a campod session and the coordinator's.
Device side is [#384](https://github.com/symmatree/coordinator/pull/384) /
[#395](https://github.com/symmatree/coordinator/pull/395); only the coordinator has an FC,
because it is the only thing wired to `/dev/ttyAMA0`.

`coord fc-log list` prints JSON and `pull <id>` streams the log to **stdout**, reporting progress
and its own sha256 on **stderr as JSON Lines**. So nothing lands on the device, this side invents
no path there, and the transfer is one pass rather than a serial download followed by a copy. The
received bytes are checked against the digest the device computed while sending; a `pull` that
reports `error` leaves a `.part` and files nothing.

**`time_utc` is LAST-MODIFIED, not creation.** So it marks the power-off, not the disarm: a log's
boundaries are power cycles, because `LOG_FILE_DSRMROT` rotates only
`if (file_disarm_rot && !log_replay)` and this vehicle flies `LOG_REPLAY=1`. The screen labels the
column "last written" for that reason: called anything else it leads straight to picking the wrong
log.

It is its own run. The pull holds the serial port with the stack quiesced for as long as it takes
-- 147.7 MB at 84-85 KiB/s measured 2026-09-23, so about half an hour -- which is a different
operational state from the minutes everything else here takes. Nothing is deleted afterwards: the
log stays on the FC's own card, in a ring of `LOG_MAX_FILES=500`, so unlike a device card it is
not the thing that fills.

## The ground station's own record of a flight

`captures/` is the vehicle's view; `ground/` is the ground station's. Different observers, neither
substituting for the other. **The tlog is the ground record:** what actually crossed the radio link
and the backpack, which the vehicle cannot see, and -- on the cluster's clock, the only one in a
flight that needs no qualification -- the timing reconciliation for everything device-side.

```sh
curl -s  https://fleet.tiles.symmatree.com/ground/tlogs           # what the share holds
curl -sXPOST "https://fleet.tiles.symmatree.com/flights/260927-sixpose/ground\
?tlog=20260927T154500Z-armed-20260927T160200Z-disarmed-20260927T160730Z.tlog"
```

**It does not work out which flight you meant.** `tlog` names what to collect and repeats; `start`
and `end` state the interval for the backpack series and override what the tlogs imply. Before
[#414](https://github.com/symmatree/coordinator/issues/414) it derived an armed window from
mavproxy's console log and rebuilt a tlog filename from it, which filed **the wrong flight's tlog**
as soon as that pod had seen more than one -- and armed is the wrong interval regardless, because it
starts after the pre-arm window where the RTK problems are.

| | |
|---|---|
| `<start>-armed-<a>-disarmed-<d>.tlog` | each tlog named, copied off the share tlog-split writes to (tiles#794) |
| `backpack-metrics-<date>.json` | every `backpack_*` series over the range, from Mimir |

**The listing is a listing.** It stats the files and reads the stamps out of their names; it never
opens a tlog. And it reports every file it finds, including one still being written and one whose
name nothing here produced -- a list that hides what it cannot classify makes those files
unretrievable, which is worse than a row with empty columns.

**What you named is not best-effort.** A tlog that was asked for and did not arrive **fails the
run**, because the screen unticks on success and an unticked file reads as collected -- which is
then what "Delete the rest" spares. The metrics are the other way round: a flight missing its
backpack series is still worth its tlog, so that is recorded as "not collected, and why".

**No range and no tlog means no backpack series**, which is right -- if the radio was never
connected there is nothing on the ground worth having.

An open `.tlog.part` can be collected and **keeps its name**, because that name is the true thing to
say about it: the copy is a prefix of a file still being written, and a fourth naming scheme
invented here would be worse than the one the share already uses.

The Mimir step widens for a long range rather than sitting at 5 s, and the resolution used is
reported, so a coarse answer is visible as one.

### Every source is a directory on the share

**This service runs no `kubectl` at all**, and holds no Role anywhere in the cluster. It used to
have pods get/list, `pods/log` and `pods/exec` in `mavproxy` and `ntrip` for four reads. One moved
and two were dropped:

- **`<date>_*.ubx`**, the base station's raw observations. **Nothing logs them any more.** In two
  months the only use was one 24 h session for the base's own survey-in, pulled by hand, and
  nothing in `analysis/` parses a `.ubx` at all -- so a one-off capture for a PPP solve is a
  `str2str` against the receiver when it is wanted, not something collected every flight. The
  curated session and its `rinex/` package stay where they are on the share.

  It also never collected anything here: the original path was `kubectl exec ... cat` into a 64 MB
  buffer against a few hundred MB of file
  ([#416](https://github.com/symmatree/coordinator/issues/416)), and the share mount meant to
  replace it was refused by the NAS export.

Also dropped:

- **`mavproxy-console.log`** -- cluster debugging output, not flight data. Everything it said about
  the vehicle is derived from heartbeats that are in the tlog; what is only there is mavproxy's own
  link and NTRIP state, and Alloy already ships pod logs to Loki.
- **`rtkbase-settings.conf`** -- `tiles/tanka/environments/ntrip/settings.conf` is in git and seeded
  into the pod, so the base position already has a history mechanism.

## The pod bus: what the devices are doing, for free

```sh
curl -s  https://fleet.tiles.symmatree.com/pods
curl -sXPOST "https://fleet.tiles.symmatree.com/nodes/campod-se/stack?desired=stopped"
```

Every campod and the coordinator publish a **retained** status document to
`rekon/pod/<node>/status` on the broker the coordinator runs
([docs/pod-bus.md](../../docs/pod-bus.md)). Readiness, camera phase, frame count, capture state,
free bytes on the card, the sensors and their self-tests. Reading it is a subscribe, so **it costs
the devices nothing** -- they were publishing anyway.

**That is why readiness is here and not on the probe.** `GET /status` quiesces every node and
then asks it `coord version`, so loading it stops collection on the whole fleet and nothing
restarts it until a power cycle -- asking whether the pods were ready was what made them not
ready ([#434](https://github.com/symmatree/coordinator/issues/434)). The two questions are
genuinely different and only one of them is expensive:

| question | answered by | costs |
|---|---|---|
| what is it **doing** -- ready, capturing, frames, card space | `GET /pods`, off the bus | nothing |
| what is **installed** -- image digests, revisions, currency | `GET /status`, `coord version` over ssh | a quiesce |

So the probe stays what it always was: a deliberate, expensive, definitive question you asked for
on purpose. It is just no longer the price of looking at the screen.

**`POST /nodes/:name/stack?desired=running|stopped`** publishes a desired state, retained, as the
bare word the contract specifies -- the same units a quiesce stops, held as a desire rather than
sent as an action.

`running` is **not reconciled device-side**, by the contract's design: it clears the desire to be
stopped, and a power cycle is what actually brings the stack back. So this is the honest verb for
"stop collecting" rather than a start button pretending to be one.

**Stop capture on all** does not use this; it uses `/stop-capture`, the quiesce. Two reasons: the
quiesce returns when the units are actually out rather than when the desire was recorded, and it
is unretained, so it does not outlive the bench session it was pressed in. This route had briefly
been the bus half of a per-node bus-or-ssh choice in that button, which stopped meaning anything
once `/stop-capture` became the bus itself.

**A broker that cannot be reached is a 502, not an empty fleet.** "Nothing is publishing" and "we
could not look" are different answers and an empty list must not be able to mean either. A document
that will not parse is reported against its node rather than skipped: this bus has already been
bitten once by a payload read differently than its publisher meant, and a reader that silently
drops what it cannot read hides that same class of bug.

| env | default | |
|---|---|---|
| `FLEET_MQTT_URL` | *(empty)* | the coordinator's broker, e.g. `mqtt://10.0.99.75:1883`. **Required.** Empty disables the bus, and with it every command that touches a device: each one quiesces first, and the quiesce goes over the bus |
| `FLEET_MQTT_SETTLE_MS` | `1500` | how long to listen after subscribing. Retained messages arrive unprompted with no count to expect, so the read ends by stopping rather than by being satisfied |

No persistent subscription and no poll loop: connect, subscribe, read, disconnect. Retained means
one subscribe gets the whole current picture, which keeps the rule that nothing here touches the
fleet unless somebody asked.

## Converge and full provision run in a pod of their own

[#504](https://github.com/symmatree/coordinator/issues/504). The service submits an Argo
`Workflow` against `fleet-control-playbook`, declared in
[tiles](https://github.com/symmatree/tiles/tree/main/tanka/environments/fleet-control), and then
watches it. Submitting and watching are different things: once the cluster has accepted the
workflow the work is happening whether this process is still here or not, which is the point --
a pod roll used to kill a converge, and a playbook-only merge *is* a pod roll.

**The run is findable without this process.** Each workflow carries
`fleet-control.symmatree.com/run-id`, `/node` and `/action` as labels, so a restarted service can
ask the cluster what is running on its behalf rather than having lost the mapping with its
memory. `findByRun` is that lookup.

**Which playbook it runs** is the newest published archive on `main`, resolved from the
`ansible-payload.yaml` workflow's artifacts — not this service's own revision, which would go
stale the moment `host/ansible/**` leaves this image's rebuild paths. The step then reads the
archive's own `VERSION` and refuses a mismatch, so the run records what it unpacked rather than
what it asked for.

Walking back through recent builds rather than taking only the newest is deliberate: artifacts
expire on a 90-day clock, so the newest *successful build* is not always a runnable one.

**Reimage has not moved** and still spawns `ansible-runner` here, which is why this image still
carries `host/ansible` and still rebuilds on a playbook merge. That is the remaining step.

### Progress comes from events, not scraped text

For the runs that still use `ansible-runner`. It emits a structured JSON event per task and per
host, and the service renders those rather than parsing `-v` output, which is not a stable
interface and prints each task's entire result object — one `docker.service` fact block is
several kilobytes. A workflow's log is not rendered at all: it is what `ansible-playbook` prints,
which is what the operator would see running it by hand.

### The log says when

Every line carries the time of day, and the top of an adopted log says what run it is, how it
ended and how long ago:

```
[fleet-control] converge failed in 3s -- started 2026-10-13 12:10:53Z, 10m03s ago
12:10:55  TASK Stop data collection for the duration of the deploy
12:10:56  UNREACHABLE: 10.0.2.49 -- Failed to connect to the host via ssh
```

The stamp was always on the wire -- `/runs/:id` returns a `t` per line and the event stream sends
the whole object -- and the page dropped it. Without it a run that finished two days ago and one
that finished in three seconds read identically, so "is this card stale or was it just fast" could
only be answered from the pod's own log.

**A button press clears the log and forgets the adopted run**, so the next render cannot put the
old one back underneath the new one.

### The screen picks up what the service is already doing

On load, the page asks `/runs` and shows the newest run per node -- in flight or just finished.
A still-running one is reattached and followed to its end.

Before this, the page's memory of a run lived entirely in one in-memory Map, so switching away on
a phone or reloading threw away information the service still had. `/runs` keeps the last 50 with
their logs and their final status, so a converge that finished in the background is recoverable
rather than lost.

### The run log outlives the run

Every line a run produces also goes to **the pod's own stdout/stderr**, tagged
`[<action> <node> <run-id8>]`, as well as to `/runs/:id` and the event stream. The registry is
in memory, so a pod restart takes its whole history with it -- and that is exactly the run
someone comes asking about afterwards. Log collection reads the pod's streams and outlives it.

And **ansible-runner's private data directory is kept unless the play exited 0**, named after
the run rather than `mkdtemp`'d, so two routes can serve it with no mapping to keep:

```sh
curl -s https://fleet.tiles.symmatree.com/runs/<id>/log    > converge.log     # as ansible printed it
curl -s https://fleet.tiles.symmatree.com/runs/<id>/events > events.ndjson    # every task's result object
```

`/events` is the one that says *how* a play ended -- an async timeout is distinguishable from
an `UNREACHABLE` from a lost connection -- and `GET /runs` reports an `events` count per run so
you can see which ones kept anything.

**Only `job_events/` is served, and that is a boundary rather than a convenience.** Beside it
the runner writes a `command` file recording the **entire process environment** it launched
ansible with, which in this pod includes `FLEET_GITHUB_TOKEN`. So there is no route that hands
out the directory, and there should not be one.

It is `/tmp` inside the container, so a kept directory still goes when the pod does -- the
stdout echo is the half that survives that. Nothing evicts these, and a successful run leaves
none.

## The flight's description

```sh
curl -XPUT https://fleet.tiles.symmatree.com/flights/260927-sixpose/notes \
  -H 'content-type: application/json' -d '{"text": "Six-pose calibration plus a bump test."}'

curl -XPUT ... -H 'content-type: text/markdown' --data-binary @notes.md   # or a file
curl     ... /flights/260927-sixpose/notes                               # read it back
```

**API-first, because the usual author is an agent** that was told the intent of the flight and is
writing that down. The text box on the post-flight screen is one caller of the same route, not a
second mechanism.

It writes `NOTES.md`, which
[docs/flight-data-layout.md](../../docs/flight-data-layout.md) already names as "optional human
narrative for this flight" -- so this is not a new artifact, it is the one already specified,
finally written by something.

**PUT replaces.** A description is the current answer to "what was this flight", and an
append-only file turns that into an archaeology problem. A caller that wants to add reads it first.

## Notifications

A campod converge is twenty minutes or more. The screen can say what happened when you come back
to it; a notification means you do not have to come back.

| event | why |
|---|---|
| a run finished | the thing you started is over, and how it ended |
| the service started | **a restart ends any run in flight.** The registry is in memory and the playbook is a child of that process, so this is not a hiccup. Worth knowing before starting something long, and after one vanishes |

The start notification carries the build, because the reason to care is "did the roller replace me,
and with what".

Announced **server-side**, from the run registry rather than the page -- the point is that it
reaches you when no browser is attached.

| env | default | |
|---|---|---|
| `FLEET_NOTIFY_URL` | *(empty)* | Apprise's notify endpoint. Empty disables notification |
| `FLEET_NOTIFY_TAG` | `tiles` | Apprise routes by tag, and **an untagged notify reaches nobody** |

**A failed notification never fails the thing it reported on.** A converge that worked and could not
be announced is still a converge that worked, so failures go to the pod's stdout and nowhere else.

## Which build is answering

The pod is replaced whenever its image digest moves (argo-tag-watcher in `tiles`), and a
replacement kills whatever run was in flight. So "which build am I talking to, and has it just
restarted" has to be answerable -- on the page and over the API.

`GET /build` reports the revision, the ref, the PR title, whether that revision is still head, and
how long this process has been up. The page shows it in the topbar, on demand rather than polled.

**A process cannot read its own image labels**, so the Dockerfile writes `/etc/container-image` in
the [#326](https://github.com/symmatree/coordinator/issues/326) manifest format -- the same flat
quoted table the devices carry, read with the same parser. `GIT_SHA` and `GIT_REF` come from
`.github/actions/build-container`; both are empty for a local build, which is reported as
`no build manifest` rather than invented.

## curl, not just the browser

The UI is one client of the routes; it has no private endpoints.

```sh
curl -sXPOST "https://fleet.tiles.symmatree.com/nodes/campod-se/converge"
curl -sN     "https://fleet.tiles.symmatree.com/runs/<id>/stream"
```

| route | |
|---|---|
| `GET /` | the web UI |
| `GET /healthz` | liveness |
| `GET /build` | which build is answering, how long it has been up, and the PR it came from |
| `GET /nodes` | the roster |
| `GET /pods` | what the devices say they are doing, off the bus. Costs them nothing |
| `POST /nodes/:name/stack?desired=` | `running` or `stopped`, published retained to the bus |
| `POST /nodes/:name/converge` | config, stacks and images -> `202 {id}` |
| `POST /nodes/:name/provision` | a fresh card, end to end -> `202 {id}` |
| `POST /nodes/:name/stop` | signal the container set and wait for it to exit |
| `POST /nodes/:name/reboot` | reboot it; does not wait, does not stop first, and abandons this node's in-flight runs |
| `GET /nodes/:name/fc-logs` | what the FC holds -- `time_utc` is LAST-MODIFIED, not creation |
| `POST /nodes/:name/fc-log?id=&flight=` | stream one dataflash log into a flight dir -> `202 {id}` |
| `GET /ground/tlogs` | what the ground-tlog share holds |
| `POST /flights/:flight/ground[?tlog=&tlog=&start=&end=]` | collect the tlogs named, over the range given -> `202 {id}` |
| `GET /runs` / `GET /runs/:id` | run list / one run with its log |
| `GET /runs/:id/stream` | live output, server-sent events |
| `GET /runs/:id/log` | a failed play as ansible printed it |
| `GET /runs/:id/events` | its ansible events, one JSON object per line |
| `GET /images/builds` | recent successful builds on the tracked ref, newest first |
| `GET /images/cached` | what the image cache holds |
| `POST /images/:role/fetch[?sha=]` | put a build in the cache -- the only route needing a token |
| `GET /images/:role/:sha/zip` | serve a cached image, for a device to `get_url` |
| `GET /images/:sha/current` | is that sha head of the tracked ref, and what PR was it |

One action per node at a time; a second `POST` against a busy node is a `409`. **Except reboot**,
which is the way out of a stuck box and so cannot be the thing a stuck box refuses.

**And one playbook run at a time, fleet-wide.** The cap dates from when every run was an
`ansible-runner` inside this pod's cgroup -- about 60 MB apiece against a 256 MB limit, so one
fitted and two did not. Three were started at once on 2026-10-13 and the pod was OOM-killed,
taking all three half-finished applies with it.

Converge and full provision no longer cost that: they run in a pod of their own (below), so the
memory argument no longer applies to them. The cap is still here because lifting it is a
separate decision and reimage has not moved yet. The cheap actions -- stop, reboot, offload, the
FC pull -- cost a connection rather than an interpreter and are not capped.

This is a floor under that failure, not a fix for its shape: the apply is a child of this process
either way, so a pod roll still kills it, and a roll is how every release arrives.
[#280](https://github.com/symmatree/coordinator/issues/280) is where that is being weighed.

## Configuration

| env | default | |
|---|---|---|
| `FLEET_INVENTORY` | *(required)* | path to the roster |
| `FLEET_SSH_KEY` | `/secrets/ssh/id` | private key |
| `FLEET_SSH_TIMEOUT_SEC` | `90` | Ansible's connect timeout, raised from its 10s default |
| `FLEET_PLAYBOOK_DIR` | `/app/ansible` | where the image keeps `host/ansible/**` |
| `FLEET_IMAGE_REPO` | `symmatree/dotfiles-symm` | where disk images are built |
| `FLEET_IMAGE_WORKFLOW` | `build-pi-image.yaml` | the workflow that builds them |
| `FLEET_IMAGE_REF` | `main` | the ref the fleet tracks |
| `FLEET_GITHUB_TOKEN` | *(unset)* | needed **only** to download an artifact; see below |
| `FLEET_IMAGE_CACHE` | `/images` | where fetched images are kept |
| `FLEET_MQTT_URL` / `FLEET_MQTT_SETTLE_MS` | *(empty)* / `1500` | the pod bus; see above. Required -- empty disables every device command, not just the bus reads |
| `FLEET_FLIGHTS_DIR` | `/mnt/flights` | where flight directories are assembled |
| `FLEET_GROUND_TLOGS` | `/mnt/ground-tlogs` | tlog-split's output, read-only (tiles#794) |
| `FLEET_MIMIR_URL` / `FLEET_MIMIR_TENANT` | `http://mimir-gateway.mimir.svc` / `tiles` | for the backpack series. The tenant is the cluster name, and there is more than one cluster |
| `PORT` / `HOST` | `8080` / `0.0.0.0` | |

Roster: `host` is optional and defaults to `name`.

```json
{
  "user": "pi",
  "nodes": [
    { "name": "coordinator", "role": "coordinator", "host": "10.0.99.75" },
    { "name": "campod-se", "role": "campod" }
  ]
}
```

## Images: discovery is public, download is not

Listing builds and their artifacts needs no credential -- the repos are public and both API
calls answer unauthenticated. **Downloading an artifact zip does**: that endpoint returns
`401 Requires authentication` even for a public repo. So the status routes and the
is-this-sha-current check work with `FLEET_GITHUB_TOKEN` unset, and only `fetch` needs it.
The minimal useful grant is a fine-grained token with *Actions: read-only*.

Identity comes from the build, not from the bytes: a run carries its own head sha and ref, so
nothing is reconstructed afterwards.

**Nothing evicts.** Every image fetched stays until the volume is wiped. Images are built on
every PR and almost none matter; the ones actually pushed are exactly the ones worth keeping,
and one image to five machines is one fetch and five local reads.

## The image carries the playbook

The build context is the **repo root**, and `host/ansible/**` is copied in — the playbook ships
with the service that runs it, so the service cannot invoke a playbook it was never built
against. That coupling is the one that bit us when this shelled `one_time.sh` and the script
changed underneath it.

The build then runs `ansible-playbook --syntax-check -i localhost, --connection=local`, using
the local-connection property `site.yaml` documents, over **every playbook this service can
invoke** -- `site.yaml`, `provision.yaml`, `deploy.yaml`, `reimage.yaml`. A broken playbook fails
CI rather than a provisioning run.

All four are named rather than relying on `site.yaml` importing two of them: `import_playbook` is
static, so parsing `site.yaml` does parse them today and stops doing so the moment anything
changes what it imports.

**It does not resolve handlers**, which is the limit worth knowing: six orphaned `notify`s
survived the [#453](https://github.com/symmatree/coordinator/pull/453) split and failed a real
first-time converge. `host/ansible/test_notifies.py` is that check, and it runs in `tests` --
gating the merge rather than this rebuild, which is the better end of the chain.

## Known limitations

**The run lives in this process.** If the pod dies mid-converge, the playbook dies with it and
the node is left part-converged. Moving Ansible to the control node did not fix that — it was
equally true when this drove SSH directly. A converge is re-runnable, so recovery is to run it
again, but nothing resumes on its own.

**There is no overall ceiling on a converge.** Ansible's own timeouts bound it, and that is
deliberate: the previous version had a 60-minute ceiling, hit it on a slow node, and reported
a *successful* converge as a failure while the play was still running. Killing a running play
is worse than waiting — it leaves a half-configured box. The cost is that a genuinely wedged
run holds that node's slot until the pod restarts.

**Nothing reaches a device without pod-link.** Every command here quiesces first and the
quiesce goes over the bus, so a device whose pod-link is down or that has never been
provisioned cannot be probed, stopped, enumerated or pulled from. There is no ssh fallback, on
purpose; see **Stop and reboot** above.

**Dry runs are not available.** The playbook refuses `--check`, for reasons recorded with the
playbook. `--syntax-check` validates structure without connecting, and the image build already
runs it.

## Develop

```sh
npm install && npm run typecheck && npm test   # no hardware, no ansible needed
FLEET_INVENTORY=./my-inventory.json npm run dev
```

## Related

- [#223](https://github.com/symmatree/coordinator/issues/223) the epic
- [#236](https://github.com/symmatree/coordinator/issues/236) this service
- [#263](https://github.com/symmatree/coordinator/pull/263) the playbook this drives
- [#280](https://github.com/symmatree/coordinator/issues/280) whether to keep building this or adopt a platform
