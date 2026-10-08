# The pod bus: how a device is told what to be, and how it says what it is

The MQTT contract between the coordinator and the campods -- and, running the same
binary, the coordinator itself. Written so something can be built against it without
reading Go: the ground platform's side of this is publishing to these topics instead
of opening an ssh session.

Implements the control-surface half of
[#25](https://github.com/symmatree/coordinator/issues/25), whose transport was left
open between MAVLink and an HTTP/gRPC pod API. **It is MQTT**, decided by
[#439](https://github.com/symmatree/coordinator/pull/439) shipping capture intent on
it, and extended here. The broker is `mochi-mqtt` on the coordinator -- the same
implementation the cluster runs (`tales/tanka/environments/mochi-mqtt`), so
federating this bus upward later is two instances of one thing rather than a
translation.

## What it is for

A small resident foothold with a **limited vocabulary**, in place of an ssh session
that can do anything. It stays in RAM, answers predictably, and the set of things it
can be asked is short enough to read in one table.

The cost it avoids is specific: every ssh forks an sshd on a board that cannot spare
it, and the cost is page cache rather than CPU -- the docker CLI alone faults in
~71 MiB of mapped text on a 417 MiB Zero with no swap, which is why `docker ps`
measures 2.1-22.0 s with capture running against 136-223 ms without.

**This is a bench and maintenance surface, not a flight one.** In flight the lifecycle
is the power plug: it is pulled before switching to battery -- sometimes repeatedly,
chasing a radio link or RTCM -- and again at the end. Capture is actuated by the arm
flag from the operator's controller, through the router. Nothing on this bus is in
that path except the intent that carries arm state.

So the bus earns its place on the bench, where the operations are: stop a job so
something else can have the serial port, bring it back, open a radio, get a device
back to paused-but-ready after a converge.

## What it costs, measured

There is no evidence that anything fits in a campod's margin, and that includes this. So
the process reports its own cost and the numbers below are measurements, not assurances.

**campod-se, boot `93af8de7`, 2026-10-08, n=1, read off its own status topic with nothing
logged in:**

| | |
|---|---|
| RSS, idle-paused | 5.7 MiB |
| RSS, capturing | 6.5 MiB |
| RSS, the coordinator's instance | 7.6 MiB |
| major faults | **31 rising to 36 over ~17 min** -- see below |
| stripped arm64 binary | 6.0 MB |

Against the campod's 414.8 MiB `MemTotal`, of which about 300 MiB cannot be reclaimed under
any pressure, leaving ~162 MiB of page cache and ~29 MiB free as the only slack there is.

**There was an x86-64 column here and it has been removed, because it could not answer
either question.** RSS is a function of memory pressure, and on a notebook with gigabytes
free nothing was reclaiming, so the figure it produced was not a prediction of the figure
on a 414 MiB board -- the two landing close together is not the x86 number having held.
Worse, it reported **0 major faults** as evidence of the property this section says it cares
about most, from a measurement where nothing was evicting anything and a non-zero result was
structurally impossible. Hardware returned 31. What the x86 run was legitimately good for
was checking that `selfCost()` reads the right `/proc` fields and that the status document
carries them, which is a test of the code and belongs with the tests.

`self_major_faults` is the load-bearing one. It counts pages this process had to read back
off the card. A count that is non-zero and growing is pod-link being paged out between
wake-ups and faulting back in -- which is the mechanism by which *delivering* "start
capturing" could cost the camera its residency, rather than this process merely sitting
there.

**It is not zero on hardware.** campod-se read **31 rising to 36 across about seventeen
minutes**, and the coordinator 7. Five faults in seventeen minutes is slow growth rather
than a startup cost that has settled -- an earlier reading of the same window was reported
as stable and that was wrong. What this does NOT yet have is an hour of a box doing real
work, which is the window that would say whether the rate holds, decays, or climbs under
capture load. Until then: non-zero, slowly growing, unexplained.

What is done to keep it there. **The order is reasoning, not measurement** -- nothing here
was measured with and without, so read it as the list of what was done rather than as a
ranking that has been tested:

1. **Few wake-ups.** The status period defaults to **60 s** and is itself a desired state
   (`desired/status_period`, in seconds) -- fast while somebody is watching at a bench,
   slow or unasked-for in flight. The MQTT keepalive is 60 s rather than 10 s for the same
   reason: a keepalive is a wake-up that says nothing.
2. **Nothing walks `/proc` periodically.** The container-init count is taken only when a
   `desired/stack` exists.
3. `GOMAXPROCS(1)` and a 32 MiB soft memory limit, so the Go runtime holds no more
   scheduler structures, thread stacks or heap than this work needs.
4. **No docker on a campod.** `POD_LINK_DOCKER_SOCKET` is empty there; per-service control
   lives on the coordinator, which can afford the daemon.

A periodic pass is therefore: two small tmpfs reads, a `statfs`, a `json.Marshal` and a
QoS 0 publish. No card reads, no forks, no `/proc` walk.

## Everything is a desired state, except reboot

Controls are desired states, reconciled every pass, with one exception. Reboot is not
a state -- the device comes back in the state it left, so a *retained* reboot request
would re-apply on every boot. It is therefore the one message published unretained,
and a retained one is refused.

## Topics

| topic | retained | payload | direction |
|---|---|---|---|
| `rekon/capture/intent` | yes | JSON with a boolean `capture`, or a bare `true`/`false` | coordinator -> all pods |
| `rekon/pod/<node>/desired/stack` | yes | `running` \| `stopped` | anyone -> one pod |
| `rekon/pod/<node>/desired/radio` | yes | `open` \| `closed` | anyone -> one pod |
| `rekon/pod/<node>/desired/status_period` | yes | seconds, e.g. `5` | anyone -> one device |
| `rekon/pod/<node>/desired/service/<container>` | yes | `running` \| `stopped` | anyone -> one device |
| `rekon/pod/<node>/reboot` | **no** | free text (a reason, logged) | anyone -> one pod |
| `rekon/pod/<node>/status` | yes | the document below | pod -> anyone |

`<node>` is the pod's own hostname (`campod-ne`, `-se`, `-sw`, `-nw`), read from the
host's `/etc/hostname` rather than the container's, which is an ephemeral docker id.

Desired-state payloads are **bare words, not JSON**. One enum value does not need a
wrapper, and `mosquitto_sub -t 'rekon/#' -v` at a bench stays readable.

**Capture intent is the exception and takes either form.** Our publisher emits
`{"capture":true,"reason":"armed"}`; a hand publish of `true` works too, and so does any
JSON carrying a boolean `capture` whatever its whitespace or key order. It is parsed, not
pattern-matched -- it used to be matched as the substring `"capture":true`, so a payload
written with the ordinary space after the colon read as **false**, closed the gate, and
said nothing. Diagnosed on campod-se 2026-10-07 only by comparing it against a topic that
worked.

A payload that cannot be parsed means **paused**, and says so: in the log, and as
`last_intent_error` in the status. The gate failing closed is deliberate
([#439](https://github.com/symmatree/coordinator/pull/439)); the gate failing *silently*
is what that bug cost.

**Capture intent is fleet-wide; everything else is per device.** Capture intent is a
property of the *vehicle* -- it armed, so every pod should be collecting -- while
stopping a job or opening a radio is a bench operation on one box. Four publishes is
what stopping four pods costs, which is nothing.

A device ignores another device's desired topics; there is no fleet-wide form of them.

`<node>` includes the **coordinator**, which runs the same binary in the publisher
role and reconciles the same way. See [On the coordinator](#on-the-coordinator).

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
  "services": {"campod_camera": "running"},
  "desired": {"capture": "true", "radio": "closed", "service/campod_camera": "running"},
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

**`stack` is `unknown` unless something asked about it.** Counting container inits
means walking /proc, and in the normal case it buys nothing: this document existing
proves pod-link is up, because pod-link is in the stack. Whether the *other* containers
are alive is better answered by the `camera` and `accel` sections, which carry an
advancing `as_of` plus a frame and a sample count and cost two small tmpfs reads. So the
walk happens only when there is a `desired/stack` to reconcile against, and `stack_inits`
is absent the rest of the time -- absent because nobody looked, not because anything
failed.

**`desired` sits beside the observed fields on purpose.** That is what makes "did it
take" a single read: no cross-referencing two topics, no inferring success from the
absence of an error. If they disagree the pod is either mid-transition or `errors`
says why it cannot get there.

**`last_intent` is the payload AS RECEIVED, verbatim**, with `last_intent_at_boot_s` and
`last_intent_error`. Verbatim because the failure worth catching is a payload the pod read
differently than its publisher meant, and a pod reporting only its own interpretation
cannot show you that. It is also the answer to "did my publish arrive", off the bus,
without reading a container log over ssh.

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

## What a device does with each desire

| desired | reconciled by | notes |
|---|---|---|
| `capture` | writing `/tmp/campod_capture`, which `capture.py` reads once per tick | written in the message handler rather than on the tick: arm comes from the operator's controller and the first frame should not wait a status period on top of the publisher's poll. Re-asserted on the tick if a write failed |
| `service/<unit>` | `StartUnit`/`StopUnit` on systemd over the system bus | the granularity that matters on the coordinator. The unit name is the quadlet file's (`coordinator-mavlink`), and a bare name, `.service` or `.container` all work. systemd owns the stop timeout -- from `TimeoutStopSec` in the unit, next to the measurement that justifies it -- so there is nothing to reimplement and nothing to wait for |
| `stack: stopped` | `SIGTERM` to every container init but its own | the whole-stack hammer, independent of the runtime: it signals `dumb-init` by name and needs nothing else running. `coord stop` and the ansible quiesce now both use `systemctl stop` on the stack target instead, which is synchronous; this stays as the path that works when systemd cannot be reached |
| `stack: running` | **nothing** | see below |
| `radio` | NetworkManager's `WirelessEnabled` over the system bus | the same property `coord radio` sets, persisted by NM across boots. Read every pass whether or not anything asked; a failed *read* is reported as `unknown` rather than as an error, because a device with no bus socket is not a fault |

The same system bus carries the radio and the reboot, so per-service control needs no
container socket mounted anywhere -- which is what it used to need, as four HTTP calls
over the Docker API (#449). A device that cannot reach the bus loses all three verbs
and says so, and keeps carrying capture intent.

A unit systemd does not know reports as `not-found` rather than as an error, so
"stopped" and "no such unit" are different answers. There is no allowlist of names.

### Why `stack: running` is not reconciled

Starting the whole stack means `docker compose up`, which is the page-fault cost this
process exists to avoid, and it is not the operation anybody actually wants: for normal
operations the thing you express is that the stack *is* up, which is the default a
power-up produces.

The boot unit's `ExecStart` is unconditional
([#256](https://github.com/symmatree/coordinator/issues/256)), so coming up **is**
starting the stack -- and on this vehicle the power plug is how that happens, several
times a session. `reboot` is the same edge for a box you are not standing next to.

For one job rather than the whole stack, `service/<container>` starts as well as
stops, which is what makes the log-pull workflow a round trip instead of a one-way one.

## On the coordinator

The coordinator runs the same binary and reconciles the same desires. The one it exists
for:

```bash
# Give up /dev/ttyAMA0 so `coord fc-log` can have it.
mosquitto_pub -t rekon/pod/coordinator/desired/service/coordinator_mavlink -r -m stopped
# ... pull the log ...
mosquitto_pub -t rekon/pod/coordinator/desired/service/coordinator_mavlink -r -m running
```

Per-service rather than whole-stack **because the broker is in that stack**: stopping
everything would cut the path the instruction arrived on, and the whole point is to
stop one job and leave the rest answering. Nothing prevents setting the coordinator's
`desired/stack` to `stopped` -- it will do it, the broker goes with it, and a power
cycle is the way back.

Its `capture` field reports the arm file it publishes from, which is what this box
knows about capture. It has no `camera` or `accel` section: the tracker and the router
write into the session rather than publishing status documents.

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
  after a flight ssh is affordable. A device pushes what it knows; nothing asks.
- **No lockout against using these at the wrong moment.** Nothing sends a reboot or a
  stack stop mid-flight, and the radio is off. If a gate is ever wanted the shape is a
  flag held true while armed that each device checks, but there is no evidence it is
  needed.
- **No bench-capture override.** Capturing while disarmed is wanted, and it does not
  belong here: the route is MAVLink -- from the radio, or injected by the router to
  itself -- so that the one actuator for capture stays the arm state the router already
  owns. This bus carries that state; it does not get a second opinion about it.
  (Mechanically it could not work anyway: the publisher recomputes intent from the arm
  file every 2 s, so a hand-published intent is overwritten within one tick.)
- **No authentication.** `allow_all`, matching the cluster's config. The radio is off in
  flight and the only route to the broker is the gadget segment, so the network layer
  already answers the question an auth hook would ask.
- **No persistence on the broker, and that is the right amount of durability.** There is
  no storage hook, so retained state lives in the broker's memory on the coordinator: it
  survives a *pod* reconnect or reboot, and dies with the coordinator's power. Adding a
  hook would mean card writes on a box whose power is pulled without warning.

  Nothing is lost, because **nothing on this bus is a source of truth.** Every desire is
  either re-derived or already held by whatever owns the real state: capture intent comes
  back from the router's arm file on the publisher's next connect, the radio state is
  persisted by NetworkManager itself, and a stopped container or stack stays stopped
  because the only thing that starts either is a boot. So losing the retained set reverts
  nothing -- it just means nobody is currently asserting anything, which is the correct
  state for a box that was just power-cycled.

## Driving it by hand

```bash
# On the coordinator, where the broker is. -r so the value survives a pod reconnect.
mosquitto_sub -t 'rekon/#' -v                                     # including retained
mosquitto_pub -t rekon/pod/campod-sw/desired/radio -r -m open
mosquitto_pub -t rekon/pod/campod-sw/desired/stack -r -m stopped
mosquitto_pub -t rekon/pod/coordinator/desired/service/coordinator_mavlink -r -m stopped
mosquitto_pub -t rekon/pod/campod-sw/reboot -m 'back to idle-ready'   # NOT -r
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
delivery to a late subscriber and per-device addressing, both of which are broker
behaviour and could not be asserted against a mock.

`service_test.go` serves the Docker API over a real unix socket, so the start/stop
round trip and the 304-is-success case are exercised rather than stubbed.

Both run at image build time under `-race`, so a wire-level regression fails the build.

## Related

- [#25](https://github.com/symmatree/coordinator/issues/25) -- the control API this implements
- [#434](https://github.com/symmatree/coordinator/issues/434) -- warm up, hold ready, start on command
- [deployment-model.md](deployment-model.md) -- "easy to change is two channels"; this is the first
- [coordinator-network.md](coordinator-network.md) -- the gadget link this rides on, and closed-network as the operating state
- [flight-data-interpretation.md](flight-data-interpretation.md) -- why no document here carries a wall clock
