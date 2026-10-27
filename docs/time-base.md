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

**The sample is the pulse plus a label.** The rollover does not have to be hunted for: the
falling edge *is* it, `pps-gpio` is configured `assert_falling_edge=1`, and the kernel
timestamps it. So the feed waits for an assert on `/dev/pps0`, reads the seconds register
once, and reports that second as having begun at the kernel's timestamp. The instant costs
nothing and is good to microseconds; noticing the edge late is harmless, because the
timestamp is not ours. The one thing that is not harmless is reading the label *after the
next* rollover, which would pair a timestamp with the wrong second -- so a read that
misses its window is dropped rather than sent.

Without an edge there is no sample, and the feed says so rather than sending the chip's
label on its own -- that would leave the second ambiguous, which is the error this cannot
absorb. There is no second path: a chip reachable over SPI whose 1 Hz is not reaching
GPIO24 is not a state the board has, since both come off the same distribution board.

**What the sample rate is for.** The chip's contribution is one integer -- which second it
is -- and sampling it faster does not sharpen that. What a faster rate buys is cold start:
chrony will not select a refclock it has not characterised across several samples, so at
the 64 s period this used to run, a boot spent minutes with no usable time even though the
pulse was arriving cleanly the whole while. The pods are behind it, taking the second over
the gadget link, so the coordinator's convergence is the front of their queue too.

**Measured on the coordinator, cold boot, with `poll 2` and the pulse pacing the feed.**
chronyd started at 06:46:55.17 and selected a source at 06:47:10.18 -- **15.0 s**, and the
`Can't synchronise: no selectable sources` that a slower feed produced does not appear at
all. The feed's first sample landed 0.58 s after its unit started.

The steady state a minute later, which is the division of labour this is built for:

```
#- DS32   poll 2  reach 377  lastrx 2-4   +381ns      +/-  10us
#* PPS    poll 2  reach 377  lastrx 1-5   +317ns      +/- 1.5us
Reference ID : 50505300 (PPS)    System time : 244 ns fast    RMS offset : 915 ns
```

The **pulse** is the selected source and steers the clock; the **chip** is reachable and
supplies the second but is excluded from combining, its 10 us dispersion being no use
against the pulse's 1.5. `reach 377` on both is the point of `poll 2`: every poll yields a
filtered sample. At `poll 0` the chip's register read `4 -> 20 -> 100 -> 1`.

And the model keeps tightening after the first sync, which is what the 64-sample
regression window is for -- one minute apart, same boot:

| | NP | Span | Freq Skew | Std Dev |
|---|---|---|---|---|
| DS32 | 5 -> 10 | 15 -> 36 s | 1.702 -> 0.052 ppm | 1683 -> 368 ns |
| PPS | 4 -> 7 | 12 -> 23 s | 3.794 -> 0.079 ppm | 1102 -> 205 ns |

**`poll 2` on a 1 Hz feed, because four samples per poll is the real constraint.** A
refclock's filter refuses to produce a filtered sample below a minimum of four
(`SPF_CreateInstance(MIN(filter_length, 4), ...)` in chrony's `refclock.c`, and
`select_samples()` returning 0 under `min_samples` in `samplefilt.c`), and a poll that
produces nothing shifts a zero into the source's reach register. That minimum is normally
hidden because `filter_length` gets clamped to what one poll can hold -- but only for
drivers with a poll function, and `RCL_SOCK_driver`'s is `NULL`, so a SOCK refclock keeps
the default 64 and the minimum stays 4. `RCL_PPS_driver` has one, which is why the PPS
line's filter is clamped to exactly the four pulses a 4 s poll collects.

So the shortest poll a 1 Hz source can fill is 4 s. At `poll 0` the chip's source sat at
reach `4 -> 20 -> 100 -> 1`, a single bit marching, while the pulse sat at `377`. The feed
is paced by the pulse rather than by a configured period, so this rate follows from the
signal rather than from two ends being kept in step by hand.

```
refclock SOCK /run/chrony.ds3234.sock refid DS32 precision 1e-5 poll 2
refclock PPS  /dev/pps0 refid PPS lock DS32 precision 1e-7 poll 2
```

**That is the whole of `chrony.conf` on the coordinator, not a drop-in.** Debian's file
declares `pool 2.debian.pool.ntp.org`, `sourcedir /run/chrony-dhcp` and
`sourcedir /etc/chrony/sources.d` -- three ways an upstream server arrives -- and a
`conf.d` drop-in can only add directives, never un-declare them. Upstream NTP here would
be a second seconds source competing with the chip the pulse is locked to, which is the
one thing this arrangement is built to avoid. It also carries `local stratum 10`, so the
coordinator still answers the pods before the chip has an epoch: a second that is the same
for all of them is worth more than no answer, since what they need from it is which second
it is and their phase comes off the wire.

**The pods take the second from the coordinator and the phase off their own leg** --
`server <coordinator> iburst` plus an unlocked `refclock PPS /dev/pps0`. Unlocked because
there is nothing to lock to: a pod has no SPI connection to the chip. A PPS refclock with
no lock is used once chrony is synchronised to within half a second, which the server
clears by orders of magnitude.

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
it sets the chip; the chip then sets chrony. `should_set_chip(no_epoch, chip_error, is_armed)`
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

**The chip's error is measured on `CLOCK_MONOTONIC_RAW`, not on the system clock.** The
system clock follows the chip, so after a write chrony slews it toward the new time at up
to 83 ms/s. An FC offset taken against the system clock is then stale by that slew, which
reads as chip error on the next pass. Until
[coordinator#593](https://github.com/symmatree/coordinator/issues/593) the router published
the offset against realtime, and with a GPS fix the chip was rewritten every 3-10 s,
mostly by about 0.17 s at a time. The router now publishes UTC minus the raw clock, with
the boot id since the raw clock restarts at boot. `coord-rtc` puts the edge on the raw
clock and times the write on it. Plain `CLOCK_MONOTONIC` would not do: it is slewed along
with realtime.

**After replacing the coin cell, or on a new RTC, set the chip once by hand.** An unset
chip reads `2000-01-01`, so the first GPS fix would write a 26-year correction into it --
a jump of unknown consequence arriving outdoors, immediately before flying. Setting it
once makes that first fix a sub-second correction instead. Not automated: it is one
command per cell.

`coord-rtc systohc` copies the **system clock** into the chip, and on a converged
coordinator there is nothing good to copy: this configuration gives chrony no source but
the chip itself, so an unset chip means an unset system clock. Set the system clock first
(`sudo timedatectl set-time '<UTC>'`), then `sudo coord-rtc systohc`. Seconds of accuracy
is the whole requirement -- the point is to avoid the 26-year jump, and the FC corrects
the rest once it has a fix.

**Why `fc_armed` is the right gate rather than merely an available one.** pod-link's
publisher "watches the arm file the MAVLink router already writes and publishes it as
capture intent", so gating on that file gates on the same value the pods act on. There is
no second signal. The constraint that follows, for
[coordinator#42, disarmed bench capture](https://github.com/symmatree/coordinator/issues/42):
whatever implements it has to flow through the same file, or two things will both mean "capture" and can
disagree.

**Correcting the chip is coherent, not disruptive.** The write moves the label and the
edge together, and every consumer takes a buffered copy of that edge, so they all step at
the same instant by the same amount -- common-mode, not a divergence. An unreadable arm
flag counts as armed: the only thing it gates is a write that would disturb capture.

## What a re-phase looks like

Writing the seconds register resets the countdown chain, so the edge moves. The datasheet
says nothing about the output's state between the write and the transition that follows, so
the pin can emit a runt pulse, skip a transition, or look normal --
[central-hub.md](rekon10/central-hub.md) has that, and that none of it is visible on the
board.

What the consumers do with it is here:

- `pps-gpio` timestamps a runt as an ordinary assert, so chrony sees an interval that is
  neither 1 s nor a clean step.
- The FC's feedback ISR counts edges, so a runt is an extra `CAM` record and a skipped
  transition is a gap.

Both are benign and both look like faults. Written down so a 0.3 s interval next to a
correction is recognised rather than chased. Not worth optimising the landing instant: the
disturbance is the size of the correction, which is the error being fixed.

## UTC is a label, and where it comes from

`coordinator-mavlink` logs the FC-derived offset to `fc_time.jsonl` per session, against
the realtime clock. Nothing on the device reads it; it is for putting a date on a flight
afterwards. The current value `coord-rtc` reads is the same composition against
`CLOCK_MONOTONIC_RAW` instead (above).

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
S11, with a hardware-captured timestamp. The parameters are below. The coordinator and the pods record theirs
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
for (persistent journald,
[coordinator#100](https://github.com/symmatree/coordinator/issues/100)). The namespace has its own 64 MB cap, the same 10 s sync bound, and
`Storage=persistent` restated because a drop-in in `journald.conf.d/` reaches the main
instance only. `coord-sessions package` asks for the namespace by name and the bundle
carries `pps.log`.

On every device, unconditionally. With no leg connected no samples arrive, the second comes
from NTP, and it starts working when a wire goes on.

## The FC leg

The FC takes a buffered copy on servo pad **S11**, bound as a camera feedback input, so
every edge lands in the dataflash with a hardware-captured timestamp beside the EKF state
at that instant. That is what ties a capture to a pose, and it does not go through UTC.

**Status, 2026-10-28:** no flight log to date has a `CAM` row. The firmware arms the
feedback pin only inside `take_picture()`, and nothing had ever called it; the fix is a
one-shot Lua script on the FC card, `ardupilot/scripts/pps-feedback-arm.lua`, and the
first log after it is deployed is the first test of the S11 wire itself. Mechanism in
[rekon10/ardupilot.md](rekon10/ardupilot.md), "Camera feedback pin".

Which pad, why it is free and what else could take it:
[rekon10/flight-platform.md](rekon10/flight-platform.md). The parameters and what each
means: [rekon10/ardupilot.md](rekon10/ardupilot.md).

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

### After the reflash, on the falling edge

`assert_falling_edge=1` landed on both roles in dotfiles-symm#81 and the coordinator was
reflashed. Two readings, a chrony restart apart:

```
2026-10-15   Reference ID 50505300 (PPS)   stratum 1   system time 33 ns fast
             RMS offset 313 ns   skew 0.028 ppm   update interval 4.0 s
             #- DS32  reach 377  -568ns  +/-   10us      sourcestats 30 samples / 114 s span, 1.3 us stdev
             #* PPS   reach 377  -495ns  +/-  628ns

2026-10-18   Reference ID 50505300 (PPS)   stratum 1   system time 390 ns slow
             RMS offset 2.9 us   179 asserts this boot
             #- DS32  reach 377  -155ns  +/-   10us
             #* PPS   reach 377  -126ns  +/- 1194ns
```

So the lock is routine rather than a one-off, and it survives a reboot: the boot unit
configures the chip unattended and chrony selects PPS without intervention. The pods are
chrony clients of the coordinator over the gadget link -- stratum 2, 15.2 us slow and 16.9
us fast, RMS 31 and 26 us, reach 377 on both. Their own PPS refclock was absent from the
rendered config until coordinator#495 deleted the flag gating it, so no pod figure on the
fraction exists yet.

Capture-side pairing is flight-platform's measurement rather than this layer's: at
`buffer_count=4` all 249 shared seconds pair within 52 us, against a median -76.7 ms at
`buffer_count=1`, because libcamera runs the IPA once per queued application request and a
single buffer let the frame-duration correction stand for every sensor frame in the gap.

### How this fails, which no software reading can diagnose

A DuPont came off the distribution board on 2026-10-18. The symptoms:

```
every DS3234 register    0x00
coord-rtc status time    2000-00-00T00:00:00
/sys/class/pps/pps0      zero asserts for the whole boot
```

**All registers reading `0x00` is the bus, not the chip's contents.** Status reads `0x48`
when the chip is powered, so a zero there is an undriven MISO and carries no information --
`osf=clear` in that state is not evidence about the battery. `2000-00-00` is not a value the
part can hold either: the BCD day and month registers power up to `01`, never `00`, so
all-zero date registers are themselves the tell.

One cause covers both symptoms. With `BBSQW=0` the square wave stops on battery while
timekeeping continues, so losing VCC gives a dead bus *and* zero edges -- and the chip's
time was correct the moment power returned.

**The 1 Hz LED on a spare buffer channel is the discriminator**, and it is the reason to
fit one: "unpowered or not oscillating" and "chip fine, bus broken" are indistinguishable
over SPI, and the LED is driven off SQW through a buffer with no software in the path.
Look at it first.

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

- [coordinator#11](https://github.com/symmatree/coordinator/issues/11) -- what is still to do
- [rekon10/central-hub.md](rekon10/central-hub.md) -- the board, the fan-out, which edge
- [campod-software.md](campod-software.md), [flight-data-layout.md](flight-data-layout.md)
- `device-sw/cli/coord-rtc`, `host/ansible/roles/chrony`
