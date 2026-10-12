# The shared time base

How the campods and the coordinator agree on when something happened, and what that
agreement is and is not. The hardware that distributes the signal is
[rekon10/central-hub.md](rekon10/central-hub.md) under "PPS"; this is what the software
does with it. Open work is [#11](https://github.com/symmatree/coordinator/issues/11).

## What it is for

Three things, in the operator's order of priority:

1. **Images start at the same time** across the eight horizontal-ring cameras.
2. **Know where the vehicle was** when they did -- ideally at that instant, at minimum a
   pose either side of it.
3. **Place an image in time** well enough to use SfM refined against the FC trajectory
   as a prior.

Absolute UTC is **not** on that list. It is wanted loosely -- "I flew that this morning",
sun angle for a date and time of day -- and nothing requires it to be precise. This
matters because it is what makes the design simple: the alignment lives in *differences*,
and absolute time is a label applied to them.

## The base is the chip, not a clock

A DS3234 on the hub board emits 1 Hz. It is buffered and fanned out, one leg per
consumer: the coordinator, the FC, and one per pod. Every consumer sees the same physical
edge within nanoseconds of wire delay.

**That is why the absolute phase does not matter.** Whatever is wrong about when the edge
happened is identically wrong for everyone, and inter-camera alignment is a difference, so
it cancels. With NTP instead, each device's clock error is independent and the errors add
-- and over a USB gadget link with asymmetric delay the error is not small. This is the
reason the circuit exists; it is not an optimisation of something NTP could do.

The campods have no SPI connection to the chip and get no RTC from it. Wire for phase,
network for the second.

## chrony's reference is the chip, never UTC

One chip generates both the seconds label and the edge, so the two cannot disagree.
`coord-rtc feed` samples the chip at its seconds rollover -- the same instant as the
falling edge -- and reports *the chip's* time over a SOCK refclock. chrony's reference and
the pulse it is locked to are therefore the same clock and agree by construction.

```
refclock SOCK /run/chrony.ds3234.sock refid DS32 precision 2e-3 poll 4
refclock PPS  /dev/pps0 refid PPS lock=DS32 precision 1e-7 poll 2
```

**Reporting UTC there instead breaks it, and silently.** The reference would be a
different clock from the pulse, and their difference -- the chip's arbitrary phase against
the UTC second -- drifts at the chip's +/-2 ppm. From chrony's `refclock.c`, a locked PPS
whose offset disagrees with its reference by more than `PPS_LOCK_LIMIT` (0.4 s) has every
pulse discarded, and 0.4 s at 2 ppm is about 55 hours. It would work for two days and then
stop, with chrony reporting nothing wrong.

A PPS refclock also cannot stand alone: with no lock it is discarded unless chrony is
already synchronised with root distance under `0.5/rate`, and the offset is folded into
`[-0.5/rate, 0.5/rate)`, so it structurally cannot supply *which* second.

## The FC sets the chip, while disarmed

The FC's GPS-derived time is the only thing on the vehicle that knows the time of day, so
it sets the chip; the chip then sets chrony. `should_set_chip(no_epoch, offset, is_armed)`
holds the whole rule:

| | |
|---|---|
| armed | never, whatever the error |
| no FC sample | never -- nothing else knows the time of day |
| chip has no epoch | always; there is no phase worth protecting yet |
| otherwise | only past a 100 ms debounce |

100 ms is ~14 hours of 2 ppm drift, so about one correction per flying day, during
pre-arm. The cadence is a policy choice and nothing forces it: `PPS_LOCK_LIMIT` compares
the pulse against the reference, and the reference is the same chip, so the chip's error
against UTC never enters that comparison.

**Why `fc_armed` is the right gate rather than merely an available one.** pod-link's
publisher "watches the arm file the MAVLink router already writes and publishes it as
capture intent", so gating on that file gates on the same value the pods act on. There is
no second signal. The constraint that follows, for #42: whatever implements disarmed bench
capture has to flow through the same file, or two things will both mean "capture" and can
disagree.

**Correcting the chip is coherent, not disruptive.** The write moves the label and the
edge together, and every consumer takes a buffered copy of that edge, so they all step at
the same instant by the same amount -- common-mode, not a divergence. An unreadable arm
flag counts as armed: the only thing it gates is a write that would disturb capture.

## What a re-phase looks like

Writing the seconds register resets the countdown chain. The datasheet says the output
"transitions high 500ms after the seconds data transfer" and says nothing about its state
in between, so depending on where in its cycle the write lands, consumers see one of:

- a **runt pulse**, a high or low period much shorter than 500 ms
- a **missing edge**, if the output was already high and stays high until the transition
- nothing unusual, if the write landed near a boundary

All three are electrically ordinary and the buffer passes any of them. `pps-gpio`
timestamps a runt as an ordinary assert, so chrony sees an interval that is neither 1 s
nor a clean step; the FC's ISR counts edges, so a runt is an extra `CAM` record and a
missing edge is a gap. Benign, but they look like faults -- written down so a 0.3 s
interval next to a correction is recognised rather than chased. Not worth optimising the
landing instant: the disturbance is the size of the correction.

## UTC is a label, and where it comes from

`coordinator-mavlink` logs the FC-derived offset to `fc_time.jsonl` per session. Nothing
on the device reads it; it is for putting a date on a flight afterwards.

Composing two FC messages is what makes it worth anything:

- **`SYSTEM_TIME`** carries `AP::rtc().get_utc_usec()` alongside `AP_HAL::millis()`, both
  read inside the FC. Note what that means: `rtc_shift = time_utc_usec - micros64()`, so
  the UTC field is the boot clock plus a stored constant. One clock and a constant, not
  two clocks -- it cannot measure the FC's rate, and it is only as good as whatever last
  set the shift.
- **`TIMESYNC`** is a ping-pong in the FC's monotonic clock, where
  `timesync_receive_timestamp_ns()` uses `receive_time_constraint_us()` -- corrected for
  serial transmission at the UART -- and that monotonic clock is the one the dataflash
  stamps `TimeUS` with. The router sends its own requests, so the round trip cancels the
  one-way delay.

Comparing `SYSTEM_TIME`'s UTC against our own receive time instead eats the whole one-way
delay, which is buffering and scheduling rather than the ~100 us of wire time at 1.5
Mbaud.

`BRD_RTC_TYPES` is `1` on this vehicle -- GPS only -- so a MAVLink peer cannot set the
FC's RTC. Worth keeping that way: the FC takes `SYSTEM_TIME` inbound as a *write*
(`handle_system_time_message` calls `set_utc_usec`), and "can't allow time to go
backwards, ever" latches any forward error for that FC boot. If our clock were ever fed in
and read back, the loop would be self-sustaining and would look like a well-synchronised
source. The damage would be a wrong *label*, since nothing in the alignment goes through
UTC.

## Recording the edges, at both ends

The FC's clock is free-running, so its edges must be logged -- `CAM1_FEEDBAK_PIN` on pad
S11, with a hardware-captured timestamp (#471). The coordinator and the pods record theirs
too, with `ppstest` into a dedicated journal namespace.

Recording arrivals that chrony's discipline already implies looks redundant, and is not:
the implication assumes the discipline worked, which is the thing worth checking. What the
pair of records buys:

- **A cross-check on TIMESYNC**, from a route with no link in the measurement path.
- **The FC's clock rate per flight**, fitted over a whole flight's 1 Hz baseline.
  flight-analysis measured that this has to be per-flight: **+313.1 ppm** on 260923 and
  **+241.8 ppm** on 260926, both from dataflash GPS. At 313 ppm a one-shot offset costs
  94 ms over a 300 s flight.
- **A capture's position in FC time by interpolation**, after the fact, even if the live
  discipline misbehaved.
- **The step is visible.** The fractional part of the assert timestamps is constant while
  nothing disturbs the tick, so a re-phase shows as a shift in it. Corrections document
  themselves, and an *unannounced* phase disturbance looks the same.

`LogNamespace=pps` because one line per second is 20-25 MB/day against a journal capped at
200 MB: sharing would crowd out the post-bounce forensic trail the journal is persistent
for (#100). The namespace has its own 64 MB cap, the same 10 s sync bound, and
`Storage=persistent` restated because a drop-in in `journald.conf.d/` reaches the main
instance only. `coord-sessions package` asks for the namespace by name and the bundle
carries `pps.log`.

Only where a leg is soldered (`coord_pps_wired`). That is not detectable -- the `pps-gpio`
overlay presents `/dev/pps0` on an unconnected pin -- so it is deployment state in
ansible, while the hardware doc owns which outputs exist.

## Which clock pairings are strong

flight-analysis, measured on real flights:

| pairing | strength |
|---|---|
| FC `TimeUS` <-> GPS `GWk`/`GMS` (dataflash) | **strong**: 8.7-11.3 ms rms over ~1900 records, slope fittable per flight. The only external anchor. |
| FC `TimeUS` <-> `get_utc_usec()` | tight and tautological -- measures nothing outside the FC |
| campod `wall_ns - boot_ns` | 0.1 ms over 315 s, and uninformative: wall is boot plus a constant |
| campod <-> FC | **+/-0.4 s**, and the campod wall clock was 6.0 s fast against GPS on 260926 |

That last row is the loose link by four orders of magnitude, and it is what this subsystem
exists to fix.

A healthy FC RTC agrees with its own GPS to **median +18.7 ms, rms 18.5 ms** over 678
samples -- so even when that route is working it has a ~19 ms floor, which is the accuracy
of the UTC label and not of the alignment.

## What has been measured on this hardware

First tier brought up on the coordinator, 2026-10-11. Card from
`coordinator-pi-20261011.img`; `/dev/spidev0.0`, `0.1` and `/dev/pps0` all present.

Chip as it arrives, nothing ever having written it:

```
control  0x1c  pin=alarm-output rate=8.192kHz osc=running bbsqw=off alarms=none
status   0xc8  osf=set
time     2000-01-01T03:39:38
```

After `enable-pps`, `control 0x00`, and five consecutive edges with no gaps:

```
assert 1791742065.939897924  sequence: 2
assert 1791742066.939905061  sequence: 3
assert 1791742067.939909975  sequence: 4
assert 1791742068.939917168  sequence: 5
assert 1791742069.939929398  sequence: 6
```

Intervals 1.000007137, 1.000004914, 1.000007193, 1.000012230 s. After a reboot the boot
unit configured the chip unattended and edges continued at sequence 24-27.

**What those numbers are not.** The ~7 us/s is the DS3234 against the **Pi's** crystal with
nothing synchronised, and the Pi's is the loose one -- it is a difference between two
oscillators, not a figure for the part. And they were taken on the **rising** edge, which
`pps-gpio` captures by default: on this part that is the half second and the undriven RC
curve. Nothing measured before a reflash carrying `assert_falling_edge=1` can become a
calibration constant.

## Not measured

- **The duty cycle.** That the falling edge is *on* the second follows from the 500 ms
  figure plus an assumption of 50% duty, which the datasheet does not state. The 1 Hz is a
  tap off the same binary divider as the seconds counter, so it is symmetric by
  construction -- an argument from how the part must work, not a citation. Settled on the
  bench by polling the seconds register against the PPS timestamps, or by
  `capture_clear=1` and reading assert-to-clear spacing.
- **Edge slope.** Every figure in the hardware doc is computed from worst-case datasheet
  capacitance. The falling edge is the one worth characterising, since it is the one
  timestamped.
- **Whether the shutters actually fire together.** libcamera's `rpi.SyncMode` is wired but
  its control surface is unverified and the default is `off`. The sharpest available
  instrument is a bright LED on a short, sharp edge: the scan-line position of the flash
  gives simultaneity, and its band width against a known illumination time gives the
  sensor's per-row readout -- which would also settle the "~26 ms, needs confirmation"
  figure in [campod.md](campod.md).

## Related

- [#11](https://github.com/symmatree/coordinator/issues/11) -- what is still to do
- [rekon10/central-hub.md](rekon10/central-hub.md) -- the board, the fan-out, which edge
- [campod-software.md](campod-software.md), [flight-data-layout.md](flight-data-layout.md)
- `device-sw/cli/coord-rtc`, `host/ansible/roles/chrony`
