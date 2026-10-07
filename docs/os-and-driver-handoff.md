# OS and driver layer: handoff

For whoever holds the OS/driver role next. This is the layer **below** the payload: the
disk image, the boot path, the kernel and its modules, device tree, the filesystem layout,
and the network plumbing those sit on.

Most of it lives in another repo --
[`dotfiles-symm/pi-image`](https://github.com/symmatree/dotfiles-symm/tree/main/pi-image):
`build-image.sh`, `assemble-btrfs.sh`, the per-role `roles/*.env` and `config.append.txt`,
`provision/`, and the claim checker. Start at
[`PIPELINE.md`](https://github.com/symmatree/dotfiles-symm/blob/main/pi-image/PIPELINE.md).
Parts of it land here, in `host/ansible`, when the thing being configured is kernel or
driver or network plumbing rather than payload: module loading, the gadget network, swap
and journald policy, i2c.

## What this role is, and where its knowledge actually lives

The job is narrower than "the OS" and wider than "the image build". It is everything that
has to be true before userspace, plus the things that should not be rewritten on a running
device, plus whatever plumbing the payload assumes and nobody owns. In practice that has
meant: why the card boots, why the filesystem survives a yanked battery, why a device-tree
line did or did not take effect, and why a 417 MiB board behaves differently from a
workstation. The payload asks "why is capture slow"; this role answers "because page-cache
refaults on a contended card", or says it cannot yet.

The role has had several holders -- `sdcard-guy`, then `os-and-driver-guy` -- and the
transcripts are in the private `facts` repo under `claude-transcripts/`. **Grep them for a
specific question; do not read them.** "What did we conclude about X" is a search; "what
happened last time" will eat your context window and teach you less than the committed
artifacts will.

The durable knowledge is in four places, and they are not equally trustworthy. The
**comments in `build-image.sh` and `assemble-btrfs.sh`** are unusually dense and almost
entirely load-bearing -- each one tends to encode a specific failure that cost a card or a
session, and they are the single highest-value thing to read. **`PIPELINE.md` and
`BOOT-SELECTION.md`** carry the shape and the settled boot-mechanism findings. The
**established-facts table below** is the compressed version, with a check per row.
Everything else -- issue descriptions especially -- should be treated as a snapshot of what
somebody believed on a particular date.

## The boundary with flight-platform, and the honest overlap

[flight-platform-handoff.md](flight-platform-handoff.md) covers the payload. The usual case
is clean and worth stating first: **device-tree configuration comes from the image, module
loading comes from ansible.** An overlay is read by the firmware before userspace exists, so
nothing in a converge substitutes for it; a module is loaded by a running system, so nothing
in the image needs to.

The split is not the directory, though, and the middle is genuinely shared. Seth's framing:

- *"the payload is ignoring signals"* -- flight-platform's
- *"the signal cannot be delivered"* -- this role's
- *"should we stop using docker to stop things"* -- neither alone

Also expect Seth to keep work in whatever session is already open rather than splitting and
briefing a second agent mid-flow. For a change like switching the stop path to signals, that
is cheaper than coordinating. So **finding a change in this layer that you did not make is
normal**, not a scope problem, and not evidence that something is unowned.

A corollary learned the hard way: ownership notes in a *peer's* handoff are addressed to
that peer. `ground-platform-handoff.md` says a particular bug "is not yours to analyse" --
that is one agent telling another agent what not to touch, and I used it to put down work in
my own layer. One agent's file is not an allocation of your scope.

## Reading the artifacts here

Three kinds of document, three different failure modes, three different checks.

**A bug describes a change, so its requirement and its assumptions age differently.** If it
says "it works like x and should work like y" and the code says x, the code is *confirming*
it. Check the facts a bug asserts about current state; leave its requirement alone. Do not
substitute "read the code first" for reading the bug -- that closes valid work. #372 says
reimaging needs a card pull, and the code indeed cannot reimage without one; that makes #372
correct, not stale.

**Read the comments, not just the description.** Under the working model above, a bug's
current shape is usually in its comments and sometimes only in a commit message. I audited
#316 from its description and reported as "still unmeasured" a thing its own comments had
already corrected, and separately re-derived a correction Seth had written in that same
issue days earlier.

**Distrust urgency, in docs especially.** A doc describes what is, so it stays useful as it
ages, but an outage narrative inside one keeps firing long after the fix. "Stops answering
SSH for minutes" outlived its own resolution by weeks. When writing: aim for a static record
of currently-understood facts, not a record of events. Seth writes dry deliberately, and the
reason is that one "holy shit, ongoing outage" sentence will stick with an agent far longer
than all the later text saying it was reflashed and is fine.

## What to read first, in order

Read these before forming opinions, and expect to disagree with some of it -- the point is
that you can check every claim rather than inherit it.

1. **`CLAUDE.md`** (this repo and `dotfiles-symm`, same content). "Troubleshooting is the
   danger zone" is the load-bearing line. The rules about not manufacturing resistance and
   not stating inference as fact are the ones most likely to catch you.
2. **`dotfiles-symm/pi-image/PIPELINE.md`**, then **`build-image.sh`** and
   **`assemble-btrfs.sh`** end to end. Slow, worth it.
3. **`dotfiles-symm/pi-image/BOOT-SELECTION.md`** -- why tryboot is dead on the Zero 2 W,
   what is honoured instead, and a list of dead ends recorded so they are not re-run.
4. **Run the claim checker** -- `pi-image/run-claims-check.sh <host>`. Its output *is* the
   current state of a device, which no document can be.
5. **`docs/power-loss-filesystem.md`**, **`docs/deployment-model.md`**,
   **`docs/coordinator-network.md`** (gadget net, closed-network state, the route into a
   campod that does not need its radio).
6. **`analysis/pi-zero-unresponsiveness-experiments.md`** -- read the status header first.
   The teardown half is resolved; the startup half is live and is #434's.
7. **Issues, comments included:** #434 (warm idle-ready), #11 and #370 (time distribution),
   #312 and #372 (reimage), #326 (the manifest vocabulary and the device-reports-a-map
   shape), #316 (closed, but its comments are the real CMA reasoning).
8. **`flight-platform-handoff.md`** and **`ground-platform-handoff.md`** -- for the
   boundaries, and to see how the other roles think.

## Established, with the check that would unseat it

True when written (**2026-10-06**) and each cost a session or more to establish. None is
settled in the sense of being beyond question -- a row whose check fails is a row that
rotted, and the check is cheaper than the original derivation. Do that rather than
deferring to this table.

| | check |
|---|---|
| **btrfs compression is off deliberately** (`dotfiles-symm` `011eb43`; it earned ~96 KB/s). Effective only between `f1bb7db` 09-12 and `011eb43` 09-16, so cards outside that window never had it | `grep -c compress /etc/fstab` -> `0`; `git log -S compress -- pi-image/assemble-btrfs.sh` for the window |
| **`ro`-`/usr` holds.** A converge remounts it `rw` and cannot restore it live; the reboot ending a converge does. A box reading `rw` has been converged since its last boot | `findmnt /usr -o OPTIONS` |
| **tryboot is inert on the Zero 2 W** -- firmware stores the flag and ignores it. The **partition number** in the same reboot argument *is* honoured | `sudo vcmailbox 0x00030064 4 4 0`; after `reboot '0 tryboot'`, `/boot/firmware/flash/result.txt` is still absent |
| **`cma-128` carries ~1.3x, not 2.5x** -- the running config allocates `buffer_count=2`, 99.6 MiB of 128 MB, byte-identical on two pods. A third buffer (~145 MiB) does not fit | with capture running: `sudo cat /sys/kernel/debug/dma_buf/bufinfo \| tail -2` |
| **`CmaFree` is not headroom.** The page allocator fills the region with movable pages, so it falls over uptime with identical client demand -- 40 MB free at 107 s against 0.4 MB hours later on the same workload | compare `CmaFree` on two pods at different uptimes with the same `bufinfo` total |
| **ADXL345 runs at full rate** -- 3243 Hz at ODR 3200, both sensors, differing ~3% from each other. The old 1567 Hz was the drain loop taking half of what accumulated | fit `sum(n)` over the `boot_ns` span of any `accel-*.jsonl` |
| **The gadget network works** -- 199 Mbit/s to one pod, 241 Mbit/s aggregate across two, ~0.35 ms. A single pod does not saturate the bus | `ping 10.55.0.1` from a pod; `ls /sys/class/net/br0/brif/` on the coordinator |
| **Steady-state capture, camera only at 1 Hz:** ~128 refaults/s, <1 pgmajfault/s, 1.25 MB/s written, 0.45 MB/s read, Dirty flat ~12 MiB with Writeback 0, capture RSS 29 MiB flat. Turning WiFi off changes none of it | two `/proc` samples 120 s apart in one ssh session; difference them |
| **Container uptime is not a duration.** No RTC, so a container's start is recorded under the pre-NTP clock while elapsed is computed against the post-step one. `boot_ns` and `monotonic_ns` in session files are honest | `docker ps` uptime against `/proc/uptime` |
| **`watchdog0/bootstatus` is always 0 here** -- `bcm2835_wdt.c` declares no `WDIOF_CARDRESET` and never assigns the field. Not "did not fire": not reported | grep `WDIOF_CARDRESET` and `bootstatus` in `drivers/watchdog/bcm2835_wdt.c` |
| **NM will not manage a `DEVTYPE=gadget` interface.** Without the `90-` udev override the campod profile is inert and `usb0` stays DOWN with no error | `nmcli -f GENERAL.REASON device show usb0` -> reason 77 when the override is missing |
| **NM keyfile list properties are `;`-separated.** A space-separated `match.driver` is one pattern matching nothing, and autoconnect falls back to a default DHCP profile silently | `nmcli connection up campod-bridge-port ifname usbN` names the mismatch |
| **NM persists the radio switch.** `WirelessEnabled` is written to and read back from `/var/lib/NetworkManager/NetworkManager.state`, so `coord radio closed` survives reboots | `grep -i wireless /var/lib/NetworkManager/NetworkManager.state` |
| **`collectd`'s `vmem` plugin cannot report `workingset_refault_file`** -- it dispatches `nr_*` by prefix plus a fixed list of named keys, and `workingset_*` matches none of them. It *does* give `pgmajfault` and all 51 `nr_*` fields | read the key dispatch in `collectd/src/vmem.c`; `grep -c '^nr_' /proc/vmstat` for the metric count |

## Open work (2026-10-06)

Treat the state column as the part that rots; check `origin/main` and the issue's comments.

| | state |
|---|---|
| **#372** re-image a rootfs without pulling the card | Unstarted and the substantial one. No selection mechanism exists on a campod. Blocks #312. Adopting one costs every device a reflash |
| **#312** touchless reimage, bench side | Needs a mechanism from #372 |
| **#434** warm up and hold idle-ready | WiFi side is PR #435. The open measurement here is whether libcamera holds its CMA buffers while nothing retrieves |
| **#370** campod time | Under #11's five-edge design. Parts in hand, nothing wired |
| **#251** disarm RO snapshot of `@data` | A decision, never made |
| **#90** bake container images into the image | Not built |
| **#236**, **#261** provisioning service, fleet SSH key | Not started |
| **#41** power-loss filesystem | Umbrella |
| wired `eth0` on the coordinator | Nothing configures it; NM's DHCP defaults are all there is. Parked until Seth can run a cable. `10.55.1.1/24` was the suggestion, and the address is his because it constrains his laptop |

### Hopes, in rough order of how much they would change things

**The claim check as a gate rather than a habit.** `run-claims-check.sh` currently needs a
human to run it. The prize is a merge gate: build the image, boot it, assert the claims,
fail the PR. `-M virt` qemu will not do it -- the Pi downstream kernel does not initialise
virtio on that platform -- so it is `raspi4b`-machine qemu or a spare Pi with automated
flashing. **Which is why #372 matters more than its own description suggests**: a working
touchless reimage is also the thing that makes an automated boot gate possible.

**A third boot partition, because it dissolves a problem rather than working around one.**
Booting from a partition that is neither p1 nor p2 means neither is in use, so both become
writable in one pass, and #312's "a coordinated change to both partitions needs a card"
stops being true. The one-shot property comes free: `__bcm2835_restart()` clears and
rewrites the partition bits every restart.

**Settle time as a property of every boot rather than a measurement campaign.** Today
"how long did this boot take to become ready" is answerable only by someone watching.
Seth's steer is to use what collectd already offers rather than building new tooling --
`vmem` for `pgmajfault` and the `nr_dirty`/`nr_writeback` writeback picture, `processes`
with a `ProcessMatch` for capture RSS and per-process faults -- and to accept the file I/O
rather than engineer around it. Note while you are in there that the config loads no logging
plugin, so a collectd write failure would be invisible; whether that matters is unexamined.

**zram, as an experiment and not a fix.** With no swap, file-backed text is the only
reclaimable thing, which is the mechanism behind the refault storms. zram would let
anonymous pages be reclaimed without touching the card. It trades CPU for that on a
four-core board during exactly the busy window, so it could easily make things worse -- it
is a discriminating experiment, not a remedy. Swap on the SD card is the one I would not
try: the measured failure is read volume on a contended card and swap adds writes to it.

**Readiness is a system property, not an application one.** I argued that `capture.py`
should report its own warm state; Seth's correction is the right one and worth carrying --
a process that allocates CMA buffers and thereby evicts pages has very little visibility
into that secondary flow faulting back in elsewhere. So readiness needs outside
observation, which is what makes the collectd question above matter rather than being
tidiness.

## How to work here

**Seth is the only decision-maker, and he is unusually good at catching a wrong premise.**
When he corrects you, the correction is normally right and normally sharper than your
version -- check it rather than taking it, then accept it rather than defending. Several
times this session he reframed something in one sentence that I had been circling for
several: that the probe-quiesce collision is "asking makes them not ready"; that bench
recovery is a solvable problem rather than a constraint on the design; that `CmaFree` was
never the number. Peers do not speak for him, and a peer reporting that he ruled something
is not authorisation.

**The machines are the thing to be careful with, and the hazard is not what you would
guess.** He has physical access, so lockout is not the risk. The risk is that **agents will
not leave the devices alone if remote access exists at all** -- and a device may be in the
middle of a controlled measurement that your "read-only" probe destroys. `find`, `du`,
`grep -r` and `journalctl` all walk the filesystem and generate reads on the box whose
paging you would be perturbing. I did exactly this, hours after writing the threat model
into this file. **Ask whether a measurement is running before you touch anything**, prefer
one ssh session that does everything to several that each do a little, and read `/proc`
rather than running tools.

**Do not invent failsafes, recovery paths, or gates he did not ask for.** I built an
automatic boot-time mechanism to re-enable WiFi, to solve a lockout risk he had already
dismissed twice, and it had to be deleted: an automatic mechanism for turning the radio
*on* is a fault on the flight line, not a rescue. The principle to hold is **mission state
is automatic, bench state is achievable** -- the configuration that has to come up even if
he pulls the plug three times chasing GPS lock is the flight one, and the bench is something
you deliberately enter. Related: when he says he wants a thing turned off, he means off. Do
not translate it into "probably on, with a plan to turn it off later if everyone agrees."

**In filed artifacts, report what you measured and not how much it matters.** No grading
adjectives, no priority stamps, no worth-judgments, no instructions to a future
implementer. Importance is his. The same sentence costs one turn in conversation and gets
corrected; in an issue it persists and reaches every later reader, including the agent sent
to work that bug. And resist the reflex to append a lessons-learned paragraph -- it reads as
insight, contains none, and in his voice it reads as a ruling on his process.

**Finally: do the thing rather than announcing it.** He notices the pattern of declaring an
intention and then finding a more interesting problem, and he is right that it indicates
you do not believe in the task. If you think something is not worth building, say that
instead. And verify before relaying -- three separate times this session I passed on a claim
I had not checked, and each time either he or a peer caught it before I did.

## The charge on this file

Update it at the **end** of a session, not during one. When something here becomes a durable
fact about the system it graduates to a real doc; when it becomes work it becomes an issue.
What is left is what would otherwise be re-learned by collision.
