# fleet-control

Converge the rekon10 fleet from a phone instead of a laptop. Runs in the cluster on
linux/amd64. Child of [#223](https://github.com/symmatree/coordinator/issues/223), filed as
[#236](https://github.com/symmatree/coordinator/issues/236).

Live at `https://fleet.{cluster}.symmatree.com`; deployment lives in `tiles`.

## One action

**Converge.** The service runs `host/ansible/site.yaml` against the node, here on the control
node, and reports the exit code. **0 means converged.**

```
ansible-playbook host/ansible/site.yaml -i '<addr>,' -u pi \
  -e device_role=<coordinator|campod> -e manage_checkout=true
```

There used to be two actions. `bootstrap` differed from `update` because a fresh card needed a
`/usr` remount, an apt install, a clone, and an exit-1-means-reboot retry dance that a running
node did not. [#263](https://github.com/symmatree/coordinator/pull/263) deletes all of that:
the same playbook handles a virgin unit and a converged one, so two buttons running identical
commands would misdescribe what the service does.

The playbook owns the whole sequence — it stops capture before converging, reboots and waits,
and starts the stack on the way out. This service adds nothing to it except a button and a log.

**It reboots on every converge, by design.** `/usr` comes back read-only at every boot, so the
remount always fires, and the reboot is what restores that invariant. Gating the reboot on
"did apt install anything" was tried and produced a worse outcome: a no-op converge left `/usr`
writable while the play's own message claimed otherwise. Rebooting a bench operation is
cheaper than an invariant that is only conditionally true.

**Budget twenty minutes or more on a campod**, not the few minutes a Pi 4B takes. Measured on
campod-se: a single apply took over 20 minutes, dominated by `apt-get update`, with the box
not answering SSH for much of it. That is the WiFi link rather than the CPU — wlan0 at -64 dBm
with 239 retry-discarded packets, and `usb0` down so there is no alternative path — so a small
TCP handshake completes while sshd's banner does not get through.

`manage_checkout=true` is passed because every node here is a managed fleet node; it defaults
off in the playbook so an operator's working tree is never reset under them. That flag is what
makes the checkout the playbook's problem rather than this service's: `roles/bootstrap` runs
`ansible.builtin.git` with `update: true` **before** `coord-stack` installs `bin/coord` and the
VIO tools from that same checkout with `remote_src`. So pull-then-converge is ordered inside
the play and cannot be got wrong from out here.

### Reflashed cards

`POST /nodes/<name>/converge?reflashed=true` clears the recorded SSH host key first.

A reflashed card presents a **new key for the same address**, which is a *changed* key rather
than an unknown one — `StrictHostKeyChecking=accept-new` accepts unknown hosts and still
refuses changed ones, and Ansible surfaces the refusal as a bare `UNREACHABLE` with the ssh
error buried. Clearing it is a caller decision, so it lives here and the playbook carries no
trust policy.

## Progress comes from events, not scraped text

`ansible-runner` emits a structured JSON event per task and per host. The service renders those
rather than parsing `-v` output, which is not a stable interface and prints each task's entire
result object — one `docker.service` fact block is several kilobytes.

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
| `GET /runs` / `GET /runs/:id` | run list / one run with its log |
| `GET /runs/:id/stream` | live output, server-sent events |

One action per node at a time; a second `POST` against a busy node is a `409`.

## Configuration

| env | default | |
|---|---|---|
| `FLEET_INVENTORY` | *(required)* | path to the roster |
| `FLEET_SSH_KEY` | `/secrets/ssh/id` | private key |
| `FLEET_SSH_TIMEOUT_SEC` | `90` | Ansible's connect timeout. Its own default is 10s, which a Zero under a converge misses |
| `FLEET_PLAYBOOK_DIR` | `/app/ansible` | where the image keeps `host/ansible/**` |
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

**Dry runs are not available.** The playbook refuses `--check` deliberately: check mode skips
every `command` task, which is the quiesce, the image pull, the start, and every probe that
registers a result and keys off its `rc` — so it cannot validate the half that matters while
still costing a full apt refresh. `--syntax-check` validates structure without connecting, and
the image build already runs it.

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
