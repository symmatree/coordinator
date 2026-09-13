# fleet-control -- ground-station control surface for the rekon10 fleet

Probe, bootstrap and update the coordinator and the campods over SSH, from the cluster,
without a laptop. The first buildable piece of
[#223](https://github.com/symmatree/coordinator/issues/223) (pre-flight / post-flight from a
phone), filed as [#236](https://github.com/symmatree/coordinator/issues/236).

**This is the only image in this repo that does not run on the vehicle.** It is `linux/amd64`
and runs on the tiles cluster; everything else here is `linux/arm64` on a Pi. It lives in this
repo anyway because what it automates is *this repo's* operator workflow -- `host/one_time.sh`,
`bin/coord`, the `/opt/stacks` symlink deploy -- and those change together. The Kubernetes
deployment (a tanka environment) lives in `tiles`.

## The actions are the thing; the buttons are a skin

There are already three named trigger surfaces -- the phone web UI, the hardwired buttons under
*Eventually* in #223, and the pocketterm. So the logic is a route surface and the UI is one
client of it. **The test is whether `curl` can run pre-flight**, and it can:

```sh
curl -s   localhost:8080/probe | jq                    # every enabled node, structured
curl -sXPOST localhost:8080/nodes/campod-sw/update      # -> 202 {"id": ...}
curl -sN  localhost:8080/runs/<id>/stream               # live output (SSE)
```

## What it does

| Action | What runs on the node | Needs |
|---|---|---|
| **probe** | a read-only script; writes nothing | reachable |
| **update** | `git pull --ff-only && coord pull && coord start` | a checkout |
| **bootstrap** | `apt-get install git`, `git clone`, `./host/one_time.sh <role>` until it completes, then `coord pull && coord start` | a blank, flashed, booted card |

`update` is the repeatable path and is most of the day-to-day value: no sudo, no apt, no
Ansible. It is the thing that currently needs a keyboard, SSH keys and a remembered command.

### `stage`, not "reachable"

"Reachable" is not the useful question -- a node can answer SSH while being blank,
half-bootstrapped, bootstrapped-but-stopped, or running, and those need four different next
actions. `probe` derives one of:

| stage | meaning |
|---|---|
| `unreachable` | no SSH. **Three causes**: still booting, wrong WiFi, or a failed first boot that powered the board off. The third is silent and terminal; the error message says so rather than reporting a timeout |
| `blank` | flashed and booted, nothing installed -- what a fresh card looks like |
| `partial` | a checkout without docker, or docker without a checkout: bootstrap was interrupted |
| `bootstrapped` | ready, nothing running |
| `running` | containers are up |

**`running` means deployed, not working.** Nothing in the campod capture path has ever run on
real hardware, so a running container is not evidence that frames are landing. The probe says
so and does not conflate the two.

## Things that are deliberate

**Addresses are static, not resolved.** The house DHCP hands out a rotating DNS list rather
than an ordered fallback, so only some of its servers know the internal
`*.local.symmatree.com` names -- a runtime lookup therefore succeeds or fails by luck of the
draw, and the failure presents as an unreachable node rather than as a DNS problem. Observed
directly on 2026-09-13: `campod-sw.local.symmatree.com` resolved and then, minutes later, did
not. The mapping lives in [`inventory.json`](inventory.json).

**Exit codes are the interface.** `host/one_time.sh` exits **1** to mean *"I installed a
kernel/firmware change, reboot me and run me again"* -- a normal path, not a failure. Treating
it as an error makes a working bootstrap look broken, so `bootstrap` loops on it, and uses the
node's `boot_id` to confirm a reboot actually happened rather than reconnecting to the
still-up old system.

**A fresh connection after `one_time.sh`, on purpose.** Ansible adds `pi` to the `docker`
group, and group membership only lands in a *new* login session -- the non-interactive
equivalent of the `newgrp docker` step in [`docs/host-setup.md`](../../docs/host-setup.md).
Reusing the session makes every following `coord` call fail on docker permissions.

**`resize2fs_once.service` is expected to be failed.** It is a vendor leftover that feeds
btrfs subvolume notation to an ext-only tool, so it fails on every card this fleet flashes
(observed on both `coordinator` and `campod-sw`). It is in `EXPECTED_FAILED_UNITS`, so a health
check does not report the whole fleet broken on day one. `unexpectedFailedUnits` is the list
worth reacting to.

**Host keys are trust-on-first-use.** Reflashing a card generates new host keys, and reflash is
a *normal* step here -- so strict `known_hosts` would turn every reflash into a hard failure,
while ignoring keys throws the protection away permanently. The service records a key on first
contact, verifies it after, and reports a change rather than accepting it. Forgetting a key is
explicit, and is what you do when you reflash:

```sh
curl -sXDELETE localhost:8080/nodes/campod-sw/hostkey
```

Host-key trust is only as durable as its store. Mount a writable volume at `FLEET_HOSTKEYS`;
if it is not persistent, `/status` reports `hostKeys.ephemeral: true` and the service logs a
warning at startup, because every restart would otherwise be a silent fresh first-contact.

## Configuration

| env | default | |
|---|---|---|
| `FLEET_INVENTORY` | `/config/inventory.json` | node name -> address/role mapping |
| `FLEET_SSH_KEY` | `/secrets/ssh/id` | private key. `pi` has passwordless sudo, so **this is root on the whole fleet** -- see [#261](https://github.com/symmatree/coordinator/issues/261) |
| `FLEET_HOSTKEYS` | `/state/hostkeys.json` | recorded host keys; needs a persistent volume |
| `FLEET_SSH_TIMEOUT_MS` | `15000` | connect timeout |
| `PORT` / `HOST` | `8080` / `0.0.0.0` | |

## Routes

| | |
|---|---|
| `GET /` | the web UI (one client of the routes below; it has no private endpoints) |
| `GET /healthz` | liveness |
| `GET /status` | inventory summary + host-key store state |
| `GET /nodes` | the roster |
| `GET /probe` | probe every enabled node, in parallel |
| `GET /nodes/:name/probe` | probe one |
| `POST /nodes/:name/update` | start a run -> `202 {id}` |
| `POST /nodes/:name/bootstrap` | start a run -> `202 {id}` |
| `DELETE /nodes/:name/hostkey` | forget a recorded key (after a reflash) |
| `GET /runs` / `GET /runs/:id` | run list / one run with its log |
| `GET /runs/:id/stream` | live output, server-sent events |

One action per node at a time: a second `POST` against a busy node is a `409`, because two
concurrent `coord pull`s on one device fight over the docker daemon and fail confusingly.

## Develop

```sh
npm install
npm run typecheck
npm test                       # no hardware needed
npm run dev                    # FLEET_INVENTORY=./inventory.json FLEET_SSH_KEY=~/.ssh/OnePKey
npm run probe -- campod-sw     # CLI against real hardware, read-only
```

`test/fixtures.json` holds host-key fingerprints generated by **`ssh-keygen`**, so the
fingerprint tests compare this implementation against OpenSSH's rather than against itself.
Regenerate with `bash test/gen-fixtures.sh`.

## Status

Built and tested; **not yet run against a node end to end.** The probe's structure and the
`blank` stage were verified read-only against `coordinator` and `campod-sw` on 2026-09-13,
before either had been bootstrapped. `update` and `bootstrap` have **not** been exercised on
hardware -- by agreement, the first bringup is a hand-run so the happy path is established
without this service as a variable, and a card gets reflashed afterwards for this to be proved
against.

When that hand-run produces a happy-path script, `bootstrap` should **call** it rather than
restate it -- the sequence in `src/actions.ts` is currently written from
[`docs/host-setup.md`](../../docs/host-setup.md) and [`docs/campod.md`](../../docs/campod.md),
and two copies of it can drift.

## Related

- [#223](https://github.com/symmatree/coordinator/issues/223) the epic -- pre-flight/post-flight, one button each
- [#236](https://github.com/symmatree/coordinator/issues/236) this service
- [#261](https://github.com/symmatree/coordinator/issues/261) a dedicated fleet key, rather than reusing the operator's
- [#48](https://github.com/symmatree/coordinator/issues/48) the symlink deploy -- why the checkout is load-bearing
- [`docs/deployment-model.md`](../../docs/deployment-model.md) -- config is git-authoritative, no on-box override
