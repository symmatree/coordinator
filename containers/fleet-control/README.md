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
  -e device_role=<coordinator|campod>
```

There used to be two actions, `bootstrap` and `update`.
[#263](https://github.com/symmatree/coordinator/pull/263) made the playbook handle a virgin
unit and a converged one the same way, so two buttons issuing identical commands would
misdescribe what the service does.

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
nothing is reconstructed afterwards. The one thing read from the artifact is the `.img` member
name, taken from the zip's central directory rather than assembled from a naming convention --
a value read from the artifact cannot disagree with what ends up on the card.

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
