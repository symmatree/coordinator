# fleet-control

Converge the rekon10 fleet from a phone instead of a laptop. Runs in the cluster on
linux/amd64. Child of [#223](https://github.com/symmatree/coordinator/issues/223), filed as
[#236](https://github.com/symmatree/coordinator/issues/236).

Live at `https://fleet.{cluster}.symmatree.com`; deployment lives in `tiles`.

## Converge

The service runs `host/ansible/site.yaml` against the node, here on the control
node, and reports the exit code. **0 means converged.**

```
ansible-playbook host/ansible/site.yaml -i '<addr>,' -u pi \
  -e device_role=<coordinator|campod>
```

There used to be two of these, `bootstrap` and `update`.
[#263](https://github.com/symmatree/coordinator/pull/263) made the playbook handle a virgin
unit and a converged one the same way, so two buttons issuing identical commands would
misdescribe what the service does. The other actions below are not smaller converges -- they
are things a converge does on its way past, offered alone because the operator wants them
alone.

The playbook owns the whole sequence. This service adds nothing to it except a button and a log.

> **Scope of this file.** It documents the *service*. What the playbook does, and what happens
> on a device while it runs, is documented with the playbook (`host/ansible/`) and the device
> (`docs/campod.md`, `docs/host-setup.md`). Causal claims about device behaviour do not belong
> here: nobody debugging a device reaches for the ground station's README, and a copy this far
> from the thing it describes goes stale without anyone noticing.

**Every converge reboots the device**, whether or not anything changed. Worth knowing before
you press the button; the reason is the playbook's and is recorded there.

**Budget twenty minutes or more on a campod**, against a few minutes for a Pi 4B. That is
measured from this side -- wall time for a single converge driven from here -- and it is an
expectation to set, not an explanation of anything.

**This service carries no git logic and needs none:** the playbook updates the on-device
checkout before installing from it. It used to be gated on a `manage_checkout` flag this
service passed as `true`; [#295](https://github.com/symmatree/coordinator/pull/295) deleted
the flag.

### Reflashed cards

`POST /nodes/<name>/converge?reflashed=true` clears the recorded SSH host key first.

A reflashed card presents a **new key for the same address**, which is a *changed* key rather
than an unknown one — `StrictHostKeyChecking=accept-new` accepts unknown hosts and still
refuses changed ones, and Ansible surfaces the refusal as a bare `UNREACHABLE` with the ssh
error buried.

**Clearing it is a caller decision when only the caller knows.** Here, nothing on the device
says the card was swapped -- the operator does, out of band, by passing `reflashed=true`. So
it lives in this service.

That is a rule about *who knows*, not a rule that playbooks never touch `known_hosts`:
`host/ansible/reimage.yaml` clears the key itself, because the play replaced the rootfs and so
is recording the consequence of its own action rather than deciding to trust a stranger. It
takes the `known_hosts` path as a variable and skips the step when it is not given.

Host keys really are checked: `accept-new`, not disabled. Turning the check off while also
clearing keys on reflash would be theatre — the clear only means anything if the check is real.
Both ssh and the clear are pointed at one explicit `known_hosts` file rather than the account
default, which in a container is neither predictable nor persistent.

## Stop and reboot

```sh
curl -sXPOST "https://fleet.tiles.symmatree.com/nodes/campod-se/stop"
curl -sXPOST "https://fleet.tiles.symmatree.com/nodes/campod-se/reboot"
```

**Stop** signals the container set and waits for it to exit. One signal over plain ssh, no
playbook: what sits between ssh and the kill is the whole question on a box that cannot `stat`
a file inside its own timeout ([#362](https://github.com/symmatree/coordinator/pull/362)), and
a signal needs nothing of ours installed -- so it works on a card that has never converged. It
is **not a sticky off**: the boot unit's `ExecStart` is unconditional
([#97](https://github.com/symmatree/coordinator/issues/97)), so the stack returns on the next
power cycle and nothing has to remember to undo this. Keeping a device down across a reboot
means disabling that unit, which is a deliberate act and not a button.

**Reboot** is one command over ssh, `sudo systemctl --no-block reboot`, **gated on nothing**.
It does not stop the stack first: a reboot is the way out of a stuck box -- the thing that was
otherwise done by hand over ssh or by pulling power -- so it must not depend on anything else
working. The containers get systemd's shutdown signal on the way down; press **Stop** first if
you want them to go on our timeout instead.

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

**`time_utc` is LAST-MODIFIED, not creation.** With `LOG_FILE_DSRMROT=1` the flight's log is the
one stamped a few seconds *after* the disarm, because that rotation is what closed it. The screen
labels the column "last written" for that reason: called anything else it leads straight to
picking the wrong log.

It is its own run. The pull holds the serial port with the stack quiesced for as long as it takes
-- 147.7 MB at 84-85 KiB/s measured 2026-09-23, so about half an hour -- which is a different
operational state from the minutes everything else here takes. Nothing is deleted afterwards: the
log stays on the FC's own card, rotated at disarm with `LOG_MAX_FILES=500`, so unlike a device
card it is not the thing that fills.

## The ground station's own record of a flight

`captures/` is the vehicle's view; `ground/` is the ground station's. Different observers, neither
substituting for the other -- a ground-side link dropout is invisible to the vehicle, and the FC
log cannot say where the base station was.

`POST /flights/<name>/ground` collects into `<flight>/ground/`:

| | |
|---|---|
| `mavproxy-console.log` | the arm/disarm timeline, **in cluster time** |
| `rtkbase-settings.conf` | carries `position=`, without which PPK is not possible, and `local_ntripc_msg` -- the mount the vehicle actually consumed |
| `<date>_*.ubx` | the base station's raw observations for the flight's day, from the `datadir=` in those settings |
| `backpack-metrics-<date>.json` | every `backpack_*` series over the window, from Mimir |
| `<start>-armed-<armed>-disarmed-<end>.tlog` | the per-flight tlog `tlog-split` already wrote (tiles#794), selected by the armed stamp |

**It establishes the armed window itself**, from the console log, and cuts the rest to it. That is
the only clock in a flight that is trustworthy without qualification -- neither device has an RTC,
so nothing on the vehicle can supply it. The Mimir range is widened by 20 minutes each side on
purpose: on 2026-09-23 the backpack rebooted and re-associated *before* the armed window, which is
the event that explained the flight.

Driven with `kubectl`, for the same reason the device side is driven with `ssh`: one credential,
one trust path, and the automated steps are the documented manual ones
([docs/post-flight-collection.md](../../docs/post-flight-collection.md)) rather than a second
implementation of them. In-cluster it uses the pod's ServiceAccount, which can read pods and exec
in `mavproxy` and `ntrip` and nothing else (tiles#793).

**Each artifact is attempted independently.** A failure is recorded and reported, not thrown: a
flight missing its base position is still worth the console log, and "not collected, and why" is as
load-bearing as the list of what was.

Files land in `ground/` because
[docs/flight-data-layout.md](../../docs/flight-data-layout.md) puts ground-side records there and
calls itself canonical; `docs/post-flight-collection.md` used `cluster/` on the day and the two
disagree.

## Progress comes from events, not scraped text

`ansible-runner` emits a structured JSON event per task and per host. The service renders those
rather than parsing `-v` output, which is not a stable interface and prints each task's entire
result object — one `docker.service` fact block is several kilobytes.

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

## curl, not just the browser

The UI is one client of the routes; it has no private endpoints.

```sh
curl -sXPOST "https://fleet.tiles.symmatree.com/nodes/campod-se/converge"
curl -sXPOST "https://fleet.tiles.symmatree.com/nodes/campod-se/converge?reflashed=true"
curl -sN     "https://fleet.tiles.symmatree.com/runs/<id>/stream"
```

| route | |
|---|---|
| `GET /` | the web UI |
| `GET /healthz` | liveness |
| `GET /nodes` | the roster |
| `POST /nodes/:name/converge[?reflashed=true]` | start a run -> `202 {id}` |
| `POST /nodes/:name/stop` | signal the container set and wait for it to exit |
| `POST /nodes/:name/reboot` | reboot it; does not wait, does not stop first |
| `GET /nodes/:name/fc-logs` | what the FC holds -- `time_utc` is LAST-MODIFIED, not creation |
| `POST /nodes/:name/fc-log?id=&flight=` | stream one dataflash log into a flight dir -> `202 {id}` |
| `POST /flights/:flight/ground` | collect the ground station's own record of a flight -> `202 {id}` |
| `GET /runs` / `GET /runs/:id` | run list / one run with its log |
| `GET /runs/:id/stream` | live output, server-sent events |
| `GET /runs/:id/log` | a failed play as ansible printed it |
| `GET /runs/:id/events` | its ansible events, one JSON object per line |
| `GET /images/builds` | recent successful builds on the tracked ref, newest first |
| `GET /images/cached` | what the image cache holds |
| `POST /images/:role/fetch[?sha=]` | put a build in the cache -- the only route needing a token |
| `GET /images/:role/:sha/zip` | serve a cached image, for a device to `get_url` |
| `GET /images/:sha/current` | is that sha head of the tracked ref, and what PR was it |

One action per node at a time; a second `POST` against a busy node is a `409`.

## Configuration

| env | default | |
|---|---|---|
| `FLEET_INVENTORY` | *(required)* | path to the roster |
| `FLEET_SSH_KEY` | `/secrets/ssh/id` | private key |
| `FLEET_SSH_TIMEOUT_SEC` | `90` | Ansible's connect timeout, raised from its 10s default |
| `FLEET_KNOWN_HOSTS` | `/state/known_hosts` | recorded host keys, shared by ssh and the clear-on-reflash path. On the `/state` volume so they survive a pod restart |
| `FLEET_PLAYBOOK_DIR` | `/app/ansible` | where the image keeps `host/ansible/**` |
| `FLEET_IMAGE_REPO` | `symmatree/dotfiles-symm` | where disk images are built |
| `FLEET_IMAGE_WORKFLOW` | `build-pi-image.yaml` | the workflow that builds them |
| `FLEET_IMAGE_REF` | `main` | the ref the fleet tracks |
| `FLEET_GITHUB_TOKEN` | *(unset)* | needed **only** to download an artifact; see below |
| `FLEET_IMAGE_CACHE` | `/images` | where fetched images are kept |
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
the local-connection property `site.yaml` documents. A broken playbook fails CI rather than a
provisioning run.

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
