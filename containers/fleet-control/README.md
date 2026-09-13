# fleet-control -- ground-station control surface for the rekon10 fleet

Set up and update the coordinator and the campods over SSH, from the cluster, without a
laptop. The first buildable piece of
[#223](https://github.com/symmatree/coordinator/issues/223), filed as
[#236](https://github.com/symmatree/coordinator/issues/236).

**This is the only image in this repo that does not run on the vehicle.** It is `linux/amd64`
and runs on the tiles cluster; everything else here is `linux/arm64` on a Pi. It lives in this
repo because what it automates is *this repo's* operator workflow -- `host/one_time.sh`,
`bin/coord`, the `/opt/stacks` symlink deploy -- and those change together. The Kubernetes
deployment (a tanka environment) lives in `tiles`.

## The two actions

| | what runs on the node | when |
|---|---|---|
| **bootstrap** | remount `/usr` rw, install git, clone, `./host/one_time.sh <role>`, reboot, `coord pull && coord start` | **once per card**, after flashing and first boot |
| **update** | `git pull --ff-only && coord pull && coord start` | **every time** a merged change needs to reach a node that is already set up |

The operator knows which they want -- they know whether they just flashed a card -- so the
service does not guess. It probes at the confirmation step and says if the answer looks wrong
for the action chosen ("this node is already set up; you probably want Update").

### The bootstrap sequence is transcribed, not reconstructed

It follows [`docs/fleet-bringup.md`](../../docs/fleet-bringup.md) stage 2, which records a
bring-up that actually happened on 2026-09-12. Two steps in it are not obvious:

- **`/usr` ships read-only**, so installing anything needs `mount -o remount,rw /usr` first.
  Not git-specific, and not a defect -- it is the ordering consequence of a device
  bootstrapping itself, and a driver coming in from outside just performs it as a step.
- **The run ends with a reboot.** `remount,ro` is refused on a live system (`mount point is
  busy`, exit 32), so a reboot is the only thing that returns `/usr` to the invariant the
  image shipped with, and it doubles as the test that the stack comes back on its own.

`one_time.sh` exits **1** to mean *"I opened the `/usr` hatch, reboot me and run me again."*
That is handled, but it is **not** the expected path: in the recorded run nothing ever set
`/var/run/reboot-required`, and the one observed exit-1 was a deliberate test artifact
([#260](https://github.com/symmatree/coordinator/pull/260)).

Timings from that run, on a Zero 2 W: clone to converged **~13 min** (mostly the Docker
install), then `coord pull` **4m22s** for `campod-camera` on an uncontended channel.

## Nothing happens by accident, and nothing happens uninvited

**Every action goes through a confirmation.** The guard being built is against an accidental
bump on a phone screen, so there is always a deliberate second tap. This is not flight
detection and does not pretend to be -- in flight there is no WiFi to the coordinator by
design, and the coordinator has better things to do than answer arbitrary SSH.

**Nothing is polled.** The page renders the roster from config and touches no machine until
you ask for an action; the probe then runs once, at the confirmation step, and once more when
a run finishes. A Zero 2 W is not here to answer a dashboard.

## `stage`

`probe` answers one question -- does this node need bootstrapping or updating -- plus whether
it can be reached at all.

| stage | meaning |
|---|---|
| `unreachable` | no SSH. **Three causes**: still booting, wrong WiFi, or a failed first boot that powered the board off. The third is silent and terminal, so the message names them rather than reporting a timeout |
| `blank` | flashed and booted, nothing installed -- **bootstrap** |
| `partial` | a checkout without docker, or docker without a checkout: an interrupted bootstrap -- **bootstrap** again |
| `ready` | checkout, docker and a stack are present -- **update** |

**`ready` means deployed, not working.** Nothing downstream of a camera being present has run
on real hardware -- capture, the exposure cap, the focus control and the accel path are all
untested. Running containers are reported as information, never as health.

## Addresses are static

Configured in [`inventory.json`](inventory.json), not resolved at runtime. There is known DNS
instability on this network; what was observed directly on 2026-09-13 is that the coordinator
resolves consistently by name and no campod resolved at all in the same window. No cause is
established here, and the service should not depend on one being found. A fixed mapping also
makes a moved node a visible config change rather than a resolver behaviour.

## Host keys

Trust-on-first-use with an explicit forget. Reflashing a card generates new host keys and
reflash is a *normal* step here, so strict `known_hosts` would fail on every reflash while
ignoring keys discards the protection permanently. Clearing a key is what you do when you
reflash that card:

```sh
curl -sXDELETE localhost:8080/nodes/campod-se/hostkey
```

Trust is only as durable as its store: mount a writable volume at `FLEET_HOSTKEYS`, or
`/status` reports `hostKeys.ephemeral: true` and the service warns at startup, because every
restart would otherwise be a silent fresh first-contact.

## curl, not just the browser

[#223](https://github.com/symmatree/coordinator/issues/223) names three trigger surfaces (the
phone UI, hardwired buttons, the pocketterm), so this is a route surface and the UI is one
client of it with no private endpoints.

```sh
curl -s      localhost:8080/nodes/campod-se/probe | jq
curl -sXPOST localhost:8080/nodes/campod-se/bootstrap     # -> 202 {"id": ...}
curl -sN     localhost:8080/runs/<id>/stream              # live output (SSE)
```

| route | |
|---|---|
| `GET /` | the web UI |
| `GET /healthz` | liveness |
| `GET /status` | inventory summary + host-key store state |
| `GET /nodes` | the roster, from config; touches nothing |
| `GET /probe` | probe every enabled node, in parallel |
| `GET /nodes/:name/probe` | probe one |
| `POST /nodes/:name/update` | start a run -> `202 {id}` |
| `POST /nodes/:name/bootstrap` | start a run -> `202 {id}` |
| `DELETE /nodes/:name/hostkey` | forget a recorded key (after a reflash) |
| `GET /runs` / `GET /runs/:id` | run list / one run with its log |
| `GET /runs/:id/stream` | live output, server-sent events |

One action per node at a time: a second `POST` against a busy node is a `409`, because two
concurrent `coord pull`s on one device fight over the docker daemon and fail confusingly.

## Configuration

| env | default | |
|---|---|---|
| `FLEET_INVENTORY` | `/config/inventory.json` | node name -> address/role mapping |
| `FLEET_SSH_KEY` | `/secrets/ssh/id` | private key. `pi` has passwordless sudo, so **this is root on the whole fleet** -- see [#261](https://github.com/symmatree/coordinator/issues/261) |
| `FLEET_HOSTKEYS` | `/state/hostkeys.json` | recorded host keys; needs a persistent volume |
| `FLEET_SSH_TIMEOUT_MS` | `15000` | connect timeout |
| `PORT` / `HOST` | `8080` / `0.0.0.0` | |

## Develop

```sh
npm install
npm run typecheck
npm test                       # no hardware needed
npm run dev                    # FLEET_INVENTORY=./inventory.json FLEET_SSH_KEY=~/.ssh/OnePKey
npm run probe -- campod-se     # CLI, read-only
```

`test/fixtures.json` holds host-key fingerprints generated by **`ssh-keygen`**, so the
fingerprint tests compare this implementation against OpenSSH's rather than against itself.
Regenerate with `bash test/gen-fixtures.sh`.

## Known direction, deliberately not built yet

**Run the one-time tasks as Ansible from here, rather than self-targeted Ansible on the
device.** That would shrink the install footprint on a 512 MB Zero, remove the split between
"bootstrap installs" and "things Ansible does", and give one clearly defined toolchain.
`docs/fleet-bringup.md` records the same question ("whether to move the Ansible control node
off the device is the open question there"). Not a blocker for this service, and the current
shape does not obstruct it: `bootstrap` is the only caller of `one_time.sh`.

**Traditional ground-station features** (current flight log on a map, offline views) are a
real but undefined future want. Nothing here is built for them, and nothing here is built to
exclude them.

## Related

- [#223](https://github.com/symmatree/coordinator/issues/223) the epic
- [#236](https://github.com/symmatree/coordinator/issues/236) this service
- [#261](https://github.com/symmatree/coordinator/issues/261) a dedicated fleet key
- [`docs/fleet-bringup.md`](../../docs/fleet-bringup.md) -- the cross-device order, and the transcript the bootstrap sequence comes from
- [`docs/deployment-model.md`](../../docs/deployment-model.md) -- config is git-authoritative, no on-box override
