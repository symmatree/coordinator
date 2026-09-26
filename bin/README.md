# bin/ -- the device-side CLI

What runs **on** a coordinator or a campod. `coord` is the operator entry point and
dispatches to the rest; the others are also callable directly, which is how the platform
drives them over plain ssh.

| | |
|---|---|
| `coord` | stack lifecycle: pull, start, stop, status, logs, shell, exec |
| `coord-version` | what this machine is running, one TOML table per unit (#326) |
| `coord-sessions` | list capture sessions, package one into a verified bundle, delete (#302) |
| `coord-throttle-log` | record the Pi's power/throttle state to the journal |
| `backpack-link-watch` | poll the ELRS backpack's WiFi health. Hand-run; nothing starts it |
| `vio-pose-tap`, `vio-ipc-record` | bench taps on the VIO IPC sockets |

`host/ansible/roles/coord-stack` installs them to `/usr/local/bin`. That is the only thing
ansible does in this path: **collection has to work on a device nobody has converged**, which
is why the platform reaches these over plain ssh rather than through a playbook (#362).

## Things true of all of them, so they are not repeated in each file

**Quiesce first. Nothing here checks it.** `coord-sessions` list, package and delete all
assume capture is stopped, and none verifies it. The platform's probe, offload and sessions
commands are all `quiesced(...)` for this reason (`fleet-control/src/quiesce.ts`), which sends
`sudo pkill -x -TERM dumb-init` and waits for the processes to actually be gone.

It is a contract rather than a check because a check is harder than it looks from here: the
writers are in containers, so their `/proc` fd links resolve in their own mount namespace and
a path comparison would not see them. `pgrep -x dumb-init` would work, and is what the
quiesce itself uses -- but the cost of the contract being broken is a bundle that fails its
own per-file manifest, which is loud.

**Run as root.** The capture tree and the container inits are root-owned, so `pi` can neither
signal them nor read the captures. This bit twice in one session, in adjacent functions,
because it was validated by hand with `sudo` and shipped without (#366, #371).

**A session is a boot, on a campod.** `capture.py` and `campod-accel` each read
`/proc/sys/kernel/random/boot_id` and write to `<captures>/<node>/<boot-id>/`, so they agree
on a directory without either handing it to the other. Nothing semantically closes a session
-- there is no signal that reaches a campod to say we landed -- so a session is bounded by
the machine rebooting, and is per power cycle rather than per flight. A bench day leaves
several per node, most of them junk.

**But "this boot's session" is not "a session being written to."** Those were conflated, and
it cost the workflow: `package` refused the current boot, so a session that had merely been
*stopped* could not be retrieved without a reboot -- and the reboot opened a fresh session
that was equally unretrievable, while restarting collection nobody wanted. The `open` flag in
`coord-sessions list` says which boot a directory belongs to and nothing more.

**These outlive the stack.** `coord version` and `coord sessions` deliberately do not require
a compose file: a machine with no stack installed still has to answer the probe, and one whose
stack was removed still has data worth listing and packaging. Distinguishing "nothing
installed" from "did not answer" is the whole point of the probe (#326).

**The coordinator lists its OAK-D captures, but only the ones written since #386.**
`find_sessions` and `resolve` are anchored on the hostname. The tracker now writes
`captures/<hostname>/<boot-id>/`, so those appear like a campod's. Sessions written before
that change are under the camera's MxId with an ISO-stamped name and are invisible here --
they cannot be renamed, because which boot each belonged to was never recorded, so there is
no migration. Collect those by hand; `docs/post-flight-collection.md` has the procedure.

`coord fc-log` is the third kind and has its own verbs. It lists and pulls the FC's own
dataflash logs over `ttyAMA0`, which are not on this device's disk at all.

## Related

- [docs/post-flight-collection.md](../docs/post-flight-collection.md) -- the whole collection procedure, of which these are the device half
- [docs/deployment-model.md](../docs/deployment-model.md) -- how these get onto a device
- [docs/flight-data-layout.md](../docs/flight-data-layout.md) -- where a collected session lands
