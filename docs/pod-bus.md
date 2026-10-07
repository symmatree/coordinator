# The pod bus: how a campod is told what to be, and how it says what it is

The MQTT contract between the coordinator and the campods. Written so something can
be built against it without reading Go -- the ground platform's side of this is
publishing to these topics instead of opening an ssh session.

Implements the control-surface half of
[#25](https://github.com/symmatree/coordinator/issues/25), whose transport was left
open between MAVLink and an HTTP/gRPC pod API. **It is MQTT**, decided by
[#439](https://github.com/symmatree/coordinator/pull/439) shipping capture intent on
it, and extended here. The broker is `mochi-mqtt` on the coordinator -- the same
implementation the cluster runs (`tales/tanka/environments/mochi-mqtt`), so
federating this bus upward later is two instances of one thing rather than a
translation.

## Why a bus and not ssh

Not tidiness. **Every ssh forks an sshd on a board that cannot spare it**, and the
cost is page cache rather than CPU: the docker CLI alone faults in ~71 MiB of mapped
text on a 417 MiB Zero with no swap, which is why `docker ps` measures 2.1-22.0 s
with capture running against 136-223 ms without. A packet into a process already
resident costs none of that.

So the bus's job is narrower and more useful than "replace ssh": **it is how you get
a pod quiet, after which everything else is cheap again.** Stopping the capture stack
is the precondition for every other remote operation -- a converge, an FC log pull,
packaging a session -- and it is the operation you least want to pay an ssh for,
because the box is at its least responsive exactly then. Once stopped, ssh is back to
milliseconds and ansible can do what ansible is for.

## Everything is a desired state, except one thing

"Stack running" is a node; "stop the stack" is the edge into it. Publishing the node
and letting the pod walk the edge buys three properties that an RPC would have to
implement:

- **Idempotence.** The same retained message applied twice is the same state.
- **Recovery with no retry policy.** The broker holds the last desired value, so a
  pod whose link blipped reconciles on reconnect rather than having missed a
  one-shot.
- **No waiting.** `pkill` returns when the signal is sent, not when the process is
  gone -- measured at 1.1 s for the accel reader and 19.0 s for the camera, both
  *after* the command returned. Every imperative caller therefore needs its own wait
  loop, and two of ours shipped without one. A reconciler has none to forget:
  `stack` reads `running` until the inits are actually gone.

**The test for whether something belongs in this model is not state-versus-action.
It is whether the desired value stays true once it has been reached.** "Stopped"
does. "Rebooted" does not -- the box comes back in the node it left, so a retained
reboot request means reboot, come up, read it again, reboot. Reboot is therefore the
one message published **not retained**, and a retained one is refused rather than
honoured. ("Delete all sessions" fails the same test for a worse reason: it stays
true and keeps consuming every future session.)

## Topics

| topic | retained | payload | direction |
|---|---|---|---|
| `rekon/capture/intent` | yes | `{"capture":true,"reason":"armed"}` | coordinator -> all pods |
| `rekon/pod/<node>/desired/stack` | yes | `running` \| `stopped` | anyone -> one pod |
| `rekon/pod/<node>/desired/radio` | yes | `open` \| `closed` | anyone -> one pod |
| `rekon/pod/<node>/reboot` | **no** | free text (a reason, logged) | anyone -> one pod |
| `rekon/pod/<node>/status` | yes | the document below | pod -> anyone |

`<node>` is the pod's own hostname (`campod-ne`, `-se`, `-sw`, `-nw`), read from the
host's `/etc/hostname` rather than the container's, which is an ephemeral docker id.

Desired-state payloads are **bare words, not JSON**. One enum value does not need a
wrapper, and `mosquitto_sub -t 'rekon/#' -v` at a bench stays readable.

**Capture intent is fleet-wide; stack and radio are per pod.** The asymmetry has a
reason: capture intent is a property of the *vehicle* -- it armed, so every pod should
be collecting -- while stopping a stack or opening a radio is a bench operation on one
device. Four publishes is what stopping four pods costs, which is nothing.

A pod ignores another pod's desired topics; there is no fleet-wide form of them.

## The status document

Retained, republished every `POD_LINK_STATUS_PERIOD_S` (default 5 s), so a late
subscriber -- a front panel that just booted, a ground screen on reconnect -- gets the
whole current picture in one subscribe with nothing to assemble.

```json
{
  "state": "ok",
  "node": "campod-sw",
  "build": "c6dc61e",
  "as_of_boot_s": 1832.4,
  "capture": true,
  "stack": "running",
  "stack_inits": 2,
  "radio": "closed",
  "desired": {"capture": "true", "radio": "closed"},
  "data_free_bytes": 12802234368,
  "camera": {
    "node": "campod-sw", "session": "<boot-id>", "camera": "present",
    "phase": "capture", "ready": true, "ready_at_mono_s": 95.2,
    "capturing": true, "frames": 412, "last_frame_mono_s": 1831.9,
    "as_of_mono_s": 1832.1
  },
  "accel": {
    "session": "<boot-id>", "reader": "campod-accel (go) c6dc61e",
    "as_of_boot_ns": 1832100000000,
    "devices": [
      {"label": "camera", "present": true, "self_test": "pass",
       "samples": 5862400, "batches": 2051840, "drops": 0, "errs": 0,
       "last_sample_boot_ns": 1832098000000},
      {"label": "arm", "present": false, "detail": "arm (/dev/spidev0.1): DEVID 0x00, expected 0xE5"}
    ]
  }
}
```

**`desired` sits beside the observed fields on purpose.** That is what makes "did it
take" a single read: no cross-referencing two topics, no inferring success from the
absence of an error. If they disagree the pod is either mid-transition or `errors`
says why it cannot get there.

**`state` is liveness.** `"gone"` is published by the broker as the pod's last will
when its connection drops, because otherwise absence and silence look identical.

**`camera` and `accel` are passed through verbatim** from the files the two capture
processes write (`/tmp/campod_camera_status`, `/tmp/campod_accel_status`, both tmpfs).
pod-link does not parse them beyond checking they are valid JSON, so adding a field on
the writing side needs no change on the publishing side and there is no schema kept in
two languages.

**Every clock in here is seconds since boot**, and that is not an oversight. These
boards have no RTC, so the wall clock is wrong by an unknown amount until time service
arrives and then steps -- and nothing in a document can say which side of the step it
was written on. The camera's `monotonic`, the accel's `boot_ns` and pod-link's
`as_of_boot_s` are all the same family and can be differenced against each other. A
consumer that needs absolute time gets it from the coordinator, which has an upstream.
See [flight-data-interpretation.md](flight-data-interpretation.md).

**`frames` with `last_frame_mono_s`**, not a rate: a count alone cannot distinguish
"capturing" from "stopped at 412", and the consumer must not have to poll the pod to
find out. Polling a capturing campod is the one thing that reliably breaks it.

**`data_free_bytes`** is `f_bavail * f_frsize` on the captures mount. The card fills in
about 4.2 hours and nothing caps it, and a full card does not present as a disk error:
it presents as a pod that cannot capture and stops answering. Worth reading before a
flight rather than diagnosing after one.

## What the pod does with each desire

| desired | reconciled by | notes |
|---|---|---|
| `capture` | writing `/tmp/campod_capture`, which `capture.py` reads once per tick | written in the message handler, not on the tick, because this latency is inside the arm-to-takeoff window; re-asserted on the tick if a write failed |
| `stack: stopped` | `SIGTERM` to every container init but its own | the same selection `coord stop` and the ansible quiesce make (`dumb-init` by name). No escalation to `SIGKILL`: a container that will not exit is a result worth seeing |
| `stack: running` | **nothing** | see below |
| `radio` | NetworkManager's `WirelessEnabled` over the system bus | the same property `coord radio` sets, persisted by NM across boots |

### Why `stack: running` is not reconciled

Starting the stack means `docker compose up`, and invoking the docker CLI is the
~71 MiB page-cache cost this whole process exists to avoid. The way back to `running`
is a **reboot**: the boot unit's `ExecStart` is unconditional
([#256](https://github.com/symmatree/coordinator/issues/256)), so coming up *is*
starting the stack.

So the graph is connected -- `stopped --reboot--> running` -- the edge just is not
labelled "start". There is also a power cycle between bench and flight by
construction, which is the same edge taken by hand.

### Going closed needs no gate here

`coord radio closed` refuses unless it can first ping the coordinator over the gadget
link, because that link is the way back in. A message that arrived over this bus came
*through* that link, to a broker on the coordinator -- so the check is satisfied by
construction rather than reimplemented. The bus route to closed-network is better
evidenced than the ssh one.

## What is deliberately not here

- **No vehicle state.** The pods make no decision from GPS, battery or attitude, so
  none of it is published to them. Putting FC telemetry on this bus is a real thing to
  want, but its consumer is a display, and the set of messages should be chosen by
  whoever is building that -- not guessed at here. The coordinator's `vehicle.tlog`
  records everything the FC sends meanwhile, so nothing is being lost while that waits.
- **No queries.** Interrogating a capturing pod is the documented way to break it, and
  after a flight ssh is affordable. The pod pushes what it knows; nothing asks.
- **No coordinator management.** Only the subscriber role reconciles. The broker runs
  on the coordinator, so a bus instruction to stop its stack would take the bus down
  with it -- and ssh to a Pi 4B is cheap in the way ssh to a Zero is not, which is the
  problem this exists to solve.
- **No authentication.** `allow_all`, matching the cluster's config. The radio is off in
  flight and the only route to the broker is the gadget segment, so the network layer
  already answers the question an auth hook would ask.
- **No persistence on the broker.** Retained state is held in memory. On the coordinator
  a storage hook would mean card writes on a box whose power is pulled without warning;
  nothing is lost, because the publisher re-establishes intent on every connect.

## Driving it by hand

```bash
# On the coordinator, where the broker is.
mosquitto_sub -t 'rekon/#' -v                        # everything, including retained
mosquitto_pub -t rekon/pod/campod-sw/desired/radio -m open
mosquitto_pub -t rekon/pod/campod-sw/desired/stack -m stopped
mosquitto_pub -t rekon/pod/campod-sw/reboot -m 'card swap'     # NOT -r
```

Clearing a desire is an empty retained payload on its topic, which is MQTT's way of
deleting a retained message:

```bash
mosquitto_pub -t rekon/pod/campod-sw/desired/stack -r -n
```

## Testing it without hardware

`containers/pod-link/link_test.go` runs a real `mochi-mqtt` in-process -- the same
implementation the coordinator deploys, imported as a library -- and drives the whole
path: arm file, publisher, broker, subscriber, capture flag. It covers retained
delivery to a late subscriber and per-pod addressing, both of which are broker
behaviour and could not be asserted against a mock. It runs at image build time, so a
wire-level regression fails the build.

## Related

- [#25](https://github.com/symmatree/coordinator/issues/25) -- the control API this implements
- [#434](https://github.com/symmatree/coordinator/issues/434) -- warm up, hold ready, start on command
- [deployment-model.md](deployment-model.md) -- "easy to change is two channels"; this is the first
- [coordinator-network.md](coordinator-network.md) -- the gadget link this rides on, and closed-network as the operating state
- [flight-data-interpretation.md](flight-data-interpretation.md) -- why no document here carries a wall clock
