# Post-flight collection, by hand

How to get every artifact of one flight off the vehicle and the cluster and into a flight
directory. Written from the first end-to-end run (2026-09-23); the per-flight diary of that
run is in that flight's own `README.md`, and this file is the repeatable procedure.

**Where things go:** [flight-data-layout.md](flight-data-layout.md).
**What the streams mean, and which clock each is on:** [flight-data-interpretation.md](flight-data-interpretation.md).
Read the clocks section of that file before quoting any timestamp out of coordinator-side data.

This is the manual path. Automating it is [#223](https://github.com/symmatree/coordinator/issues/223),
and the ground-side half of that likely belongs in `fleet-control` rather than in ansible.

## Setup

```sh
FLIGHT_DIR=~/datasets/flights/rekon10/<YYMMDD>-<slug>
HOST=coordinator                       # or a campod hostname
mkdir -p "$FLIGHT_DIR"/{coordinator,captures,ground,cluster,fc}
```

Device access is ssh as `pi`. The capture tree and container inits are root-owned, so every
read of them needs `sudo` -- see [flight-platform-handoff.md](flight-platform-handoff.md).

## Order matters in one place

**Running the fleet-control probe stops capture.** `PROBE_COMMAND` is
`quiesced('coord version')` (`containers/fleet-control/src/probe.ts`), and `QUIESCE` is
`sudo pkill -x -TERM dumb-init` plus a wait (`src/quiesce.ts`). So a probe -- and the offload
and sessions commands, which are quiesced the same way -- SIGTERMs every container on the
device. That is intended, and it is what frees `/dev/ttyAMA0` for the dataflash pull below.

Consequence: if you want the stack still running, collect before you probe. If you want the
FC serial link free, probe first. On 2026-09-23 the probe ran at 12:19:22Z and all four
coordinator containers exited within 0.8 s (143/143/0/0).

## 1. Identify the flight boot and the flight session

Nothing downstream is right if this is wrong, and neither directory names nor `docker`'s
relative times can be trusted on an RTC-less device.

```sh
ssh pi@$HOST 'sudo journalctl --list-boots'
```

Each boot's "first entry" is the `fake-hwclock` restore value, not a real time. Use the LAST
entry of each boot to find the one that covers the flight.

The OAK-D capture session is then identified by **which session contains stills**: stills are
arm-gated (`OAK_ARM_FILE`, `feature_tracker.cpp`), so a session with JPEGs is a session that
was armed, and one without never was. Confirm a session's name against its own recorded start:

```sh
ssh pi@$HOST "sudo cat /var/lib/coordinator/captures/<MXID>/<SESSION>/*.feat.json"
# -> {"start_unix": ...}; convert and compare to the directory name
```

A session can span more than one day. On 2026-09-23 the flight session was named
`20260922T122804Z` because it opened on the previous day and ran a ~24 h boot; that name is
correct, not stale.

## 2. Coordinator host artifacts

```sh
C="$FLIGHT_DIR/coordinator"

ssh pi@$HOST 'sudo cat /var/lib/coordinator/captures/vehicle.tlog'   > "$C/vehicle.tlog"
ssh pi@$HOST 'sudo cat /var/lib/coordinator/captures/timesync.jsonl' > "$C/timesync.jsonl"
ssh pi@$HOST 'coord version'                                         > "$C/coord-version.toml"

# Journals, per boot. -1 is the previous boot; use the index from --list-boots.
ssh pi@$HOST 'sudo journalctl -b -1 -o short-iso-precise --no-pager' > "$C/journal-boot-1-flight.log"
ssh pi@$HOST 'sudo journalctl -b  0 -o short-iso-precise --no-pager' > "$C/journal-boot-0-current.log"

# collectd: 10 s system metrics, 7 d retention, and NOTHING SCRAPES IT --
# it exists only on the device (host/ansible/roles/metrics).
ssh pi@$HOST 'sudo tar czf - -C /var/log collectd' > "$C/collectd.tar.gz"

# Container stdout does NOT reach journald under the json-file log driver,
# so this is a separate source from the journals above.
for c in coordinator_mavlink coordinator_vio_tracker \
         coordinator_vio_estimator coordinator_sh1106_display; do
  ssh pi@$HOST "docker logs --timestamps $c 2>&1" > "$C/docker-$c.log"
done
```

## 3. Capture sessions

Stream the session as tar rather than `scp -r`, so one `sudo` covers the whole tree:

```sh
MXID=<oak-d mxid>; SESSION=<session dir>
mkdir -p "$FLIGHT_DIR/captures/$MXID"
ssh pi@$HOST "sudo tar cf - -C /var/lib/coordinator/captures/$MXID '$SESSION'" \
  | tar xf - -C "$FLIGHT_DIR/captures/$MXID"

# verify both ends agree
ssh pi@$HOST "sudo ls -1 /var/lib/coordinator/captures/$MXID/$SESSION | wc -l"
ls -1 "$FLIGHT_DIR/captures/$MXID/$SESSION" | wc -l
```

Collect only the flight's session. Sessions from other days belong to other flights.

Campod sessions land at `captures/<campod-hostname>/<session>/` and are collected through
`coord sessions package` / the fleet-control offload, which produces a bundle carrying a
per-file sha256 `manifest.json`. **Check the extracted bytes against that manifest**, not just
the transport digest.

## 4. Ground side: backpack link health

The live backpack data is metrics, not a log file. `bin/backpack-link-watch` is a hand-run
tool that nothing installs or starts, so unless a person ran it there is no jsonl and the
window cannot be reconstructed. The always-on path is the Alloy `Probe` CR `backpack-mavlink`
-> `json-exporter` -> Mimir ([#190](https://github.com/symmatree/coordinator/issues/190)); see
[backpack-link-watch.md](backpack-link-watch.md).

```sh
# tenant, from the Alloy chart rather than assumed
kubectl get cm alloy-alloy-metrics -n alloy -o jsonpath='{.data}' | grep -o 'X-Scope-OrgID[^,}]*'

kubectl port-forward -n mimir svc/mimir-gateway 18080:80 &      # read-only
curl -sS -H "X-Scope-OrgID: <tenant>" -G \
  "http://127.0.0.1:18080/prometheus/api/v1/label/__name__/values" \
  --data-urlencode 'match[]={__name__=~"backpack.*"}'
```

Then `query_range` each `backpack_*` series across the window and save the JSON to
`$FLIGHT_DIR/ground/`. Ten series exist. The load-bearing ones:

| series | reads as |
|---|---|
| `backpack_uptime_milliseconds` | a drop to near zero is a reboot |
| `backpack_mavlink_packets_up_total` | counter reset corroborates a reboot; the rising value is uplink including RTCM |
| `backpack_wifi_reconnects_total` | a clean boot lands at 1, never 0 -- only increments ABOVE the post-boot value are link losses |
| `backpack_wifi_link_info` | `bssid` label; a change means it re-associated, since the backpack has no roaming logic |
| `backpack_mavlink_gcs_info` | the `gcs` latch. The direct evidence for [#99](https://github.com/symmatree/coordinator/issues/99) |

Grafana at `borgmon.tiles.symmatree.com` needs credentials; going to Mimir directly avoids
handling them.

## 5. Cluster side: mavproxy and the base station

**The mavproxy tlog is not in the state-basedir.** Read the actual command line:

```sh
POD=$(kubectl get pod -n mavproxy -o name | head -1 | cut -d/ -f2)
kubectl exec -n mavproxy $POD -- tr '\0' ' ' < /proc/1/cmdline    # -> --logfile=<path>

kubectl logs -n mavproxy $POD --since=<window> --timestamps > "$FLIGHT_DIR/cluster/mavproxy-console.log"
kubectl cp -n mavproxy $POD:<logfile path>     "$FLIGHT_DIR/cluster/mavproxy.tlog"
kubectl cp -n mavproxy $POD:<logfile path>.raw "$FLIGHT_DIR/cluster/mavproxy.tlog.raw"
```

The console log is where the arm/disarm/mode timeline is, in cluster time -- which is real
time, unlike anything coordinator-side.

Base station:

```sh
RPOD=$(kubectl get pod -n ntrip -o name | grep rtkbase | head -1 | cut -d/ -f2)
kubectl cp -n ntrip $RPOD:/root/rtkbase/settings.conf "$FLIGHT_DIR/cluster/rtkbase-settings.conf" -c rtkbase
```

`settings.conf` carries `position=` (the base coordinates -- **PPK is impossible without
them**), `antenna_info`, and the per-caster RTCM message sets. `local_ntripc_msg` is the
mount the vehicle actually consumes; `rtcm_msg_a`/`_b` are the external casters and can
differ. Raw observations live under `datadir=` -- on a SEPARATE MOUNT, so a `find / -xdev`
will not see them:

```sh
kubectl cp -n ntrip $RPOD:<datadir>/<YYYY-MM-DD>_..._GNSS-1.ubx     "$FLIGHT_DIR/cluster/" -c rtkbase
kubectl cp -n ntrip $RPOD:<datadir>/<YYYY-MM-DD>_..._GNSS-1.ubx.tag "$FLIGHT_DIR/cluster/" -c rtkbase
```

The current day's file is open and being appended; `tar` warns "file changed as we read it".
That is expected and the already-written prefix covers any past flight.

## 6. FC dataflash, through the coordinator

No second device on the FC and no card pull. The coordinator is already wired to the FC on
`/dev/ttyAMA0`; with the stack quiesced, that port is free.

```sh
ssh pi@$HOST 'sudo coord fc-log list'
ssh pi@$HOST 'sudo coord fc-log pull <id>' > "$FLIGHT_DIR/fc/<name>.bin"
```

**The log never lands on the device.** `pull` streams to stdout, so the caller's shell
writes the file and there is no second full transfer waiting on the first to finish. Do not
reintroduce an on-device path: `/tmp` there is tmpfs, so a 1.8 GB log written to it is
1.8 GB of RAM on a 3.7 GB box, and the card is not a place to leave a copy of something
whose home is the archive.

Progress and the sha256 go to stderr, so they do not land in the middle of the log. A window
that cannot be completed stops the transfer with a non-zero exit and a short stream -- a
partial dataflash must not be filed as a flight record.

`coord-fc-log` re-execs itself into the `coordinator-mavlink` image for `pymavlink`, which
the host does not have and should not: the host is ansible-managed and this is the only
thing that would want it.

### Three FC behaviours that will cost you an hour

**The log state machine wedges after an aborted transfer.** Kill a download mid-stream and the
FC keeps streaming the old log and ignores every `LOG_REQUEST_LIST`. This presents as
`no LOG_ENTRY for id <n>` -- indistinguishable from the log not existing. Send
`log_request_end_send()` and drain before requesting anything.

**Ask for the whole list, not one entry.** `log_request_list_send(sys, comp, id, id)` returned
nothing; `log_request_list_send(sys, comp, 0, 0xffff)` and picking the entry out worked. Not
explained, recorded as observed.

**`LOG_ENTRY.time_utc` is last-modified, not creation.** This decides which log is the flight
and it is easy to get backwards. The check: `LOG_DISARMED` is 1 or 2, so a log MUST have been
created at boot -- if no entry carries a boot-time stamp, the field is not creation time.
Then, with `LOG_FILE_DSRMROT=1`, the flight log is the one whose `time_utc` sits a few seconds
after the disarm, because that rotation closed it.

Request data in bounded windows and re-request gaps inside each window before advancing. A
single streaming request for the whole file does not self-repair: when a packet is lost the
contiguous pointer stops while later bytes keep arriving, and nothing re-requests the hole
until the stream ends.

### Verify before believing

```sh
# the byte count must equal LOG_ENTRY.size from `list`, and pull must have exited 0
ls -l "$FLIGHT_DIR/fc/<log>.bin"
# and the digest pull reported on stderr must match what landed
sha256sum "$FLIGHT_DIR/fc/<log>.bin"
```

Then parse far enough to find the `ARM`/`DISARM` events and check them against the mavproxy
console timeline from step 5. Those are independent observers, so agreement corroborates the
whole chain. `PARM` in the log is the authoritative record of what the vehicle actually flew
-- not `ardupilot/inputs/`, which is dated to the repo and not to the flight.

## Write the README

Each flight directory gets a `README.md` naming every asset, the command that retrieved it,
and anything known to be missing and why. "Not collected, and why" is as load-bearing as the
list of what was: a later reader cannot tell an absent artifact from an artifact nobody tried
for.

---

# Possible optimizations -- NONE OF THESE ARE ESTABLISHED PROBLEMS

Separated deliberately. Everything above is procedure that worked. Everything below is a
candidate, from a single run (2026-09-23) that was the first collection ever done this way.
There is no baseline to compare against, so a number being large is not evidence that it is
wrong.

Each entry states what was actually observed, what would have to be true for the optimization
to apply, how to find out whether it is true now, and what result would show a change worked.
An entry that cannot answer all four is not ready to act on.

## A. Dataflash transfer rate

**Observed.** 147.7 MB (154,894,336 bytes) of log id=3 pulled over `/dev/ttyAMA0` at 1.5 Mbaud
using 256 KiB request windows: **84-85 KiB/s sustained**. An earlier, different attempt
(log id=2, single streaming request, no windows) reported **122 KiB/s** before it stalled on an
unrepaired gap at 2.8%.

**Not established as a problem.** Nobody has said how long this is allowed to take, and the
alternative paths -- Mission Planner over the FC's own USB, or pulling the card -- have not been
timed on this vehicle either. The rate is only a problem if it is slower than those, or slower
than an operator will tolerate between flights.

**Whether the two numbers can be compared: they cannot, yet.** The 122 and the 85 differ in at
least three variables at once (different log, different request strategy, different FC state --
the second ran after an aborted transfer). Treating 122 as "the unwindowed rate" would be
reading a difference out of a comparison that does not isolate anything.

**How to find out.** Pull the same log twice, back to back, changing exactly one thing:
window size (try 64 KiB, 256 KiB, 1 MiB). Record bytes, wall time, and the count of
re-requested windows. Three runs per setting, because a single run cannot show variance.

**What would show an improvement is real.** A rate change that holds across all three runs of a
setting and does not overlap the other setting's range. A single faster run is not a result.

## B. Whether the FC logging concurrently affects the transfer

**Observed.** During the pull, the FC had a fourth log open, reported at 2,248,736,768 bytes
with a `time_utc` of 13:52:02Z against a ~12:12Z creation.

**Not established, and the inference is weaker than it looks.** That the log is large does not
show it was being written *during the transfer*; `time_utc` is last-modified, which was ~20
minutes before the pull started. "The FC was writing 1.35 GB/hour while we downloaded" is an
extrapolation from two numbers, not a measurement.

**Whether it applies at all.** `LOG_DISARMED` is `2` -- per `AP_Logger.cpp`, "Disabled on USB
connection" -- and the FC is on USB power. If the flag is doing what it says, the FC was not
logging at all during the transfer and this entry is moot.

**How to find out.** Two `LOG_REQUEST_LIST` calls sixty seconds apart with the vehicle sitting
as it was, and compare the size of the open log. Growth means the flag is not suppressing
logging on a power-only USB source, which is separately worth knowing -- a dumb charger
asserts VBUS but never enumerates, and which of those `usb_connected()` keys off decides
whether the flag does anything here. That is answerable from `AP_HAL_ChibiOS` without the
vehicle.

**What would show a fix.** The open log's size unchanged across sixty seconds while on USB
power, and a transfer rate measured under A that is outside the range measured while it was
growing.

## C. One request window costs a round trip

**Applies only if A shows window size matters.** The procedure re-requests the first missing
byte of each window and waits for the window to fill before advancing, so throughput includes
one request latency per window. Larger windows amortise that; they also enlarge the unit that
has to be re-fetched when a gap appears.

**How to find out, and what a fix looks like:** same experiment as A. This is a hypothesis
about *why* A might move, and it is only worth testing after A shows there is something to
explain.

## D. Collection pulls whole files for one flight's window

**Observed, and already tracked.** The mavproxy tlog is a single file open since 2026-09-12 --
27,650,316 bytes for a flight that lasted 2m39s. The base station's raw GNSS rotates daily, so
one flight costs a 227 MB `.ubx`. This is what
[#192](https://github.com/symmatree/coordinator/issues/192) describes for the tlog half.

**Established as a problem only for the tlog**, by that issue, not by this run. For the `.ubx`
the daily file is the natural unit of a GNSS archive and slicing it is not obviously desirable;
no one has said the size is a burden.

**How to find out whether it matters:** whether disk on the share, or the time to copy, is
actually a constraint. Neither has been measured.

**What a fix would show:** per-flight rotation landing a file whose window brackets the armed
period, with the same message content as the corresponding slice of today's monolithic file.

## E. Doc corrections found while doing this

Not optimizations -- factual errors, which should simply be fixed.

- `flight-data-interpretation.md` describes `LOG_DISARMED=2` as "(log from boot, needed for
  `LOG_REPLAY`)". Per `AP_Logger.cpp` the value means **"Disabled on USB connection"**; 1 is
  "Enabled" at all times, and `LOG_REPLAY` accepts 1 or 2. The parenthetical drops the USB
  condition, which is the whole point of choosing 2.
- `backpack-link-watch.md` said [#190](https://github.com/symmatree/coordinator/issues/190) was
  still pending and the hand-run script was the stopgap, when the Alloy path had already
  landed. Fixed in [#381](https://github.com/symmatree/coordinator/pull/381).
