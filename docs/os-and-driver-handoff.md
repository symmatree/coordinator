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
- *"should we stop using docker to stop things"* -- neither alone (and docker is
  gone now: #451 replaced it with podman + quadlet)

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
8. **`flight-platform-handoff.md`** and **`handoffs/ground-platform-handoff.md`** -- for the
   boundaries, and to see how the other roles think.

## Where the fleet actually is (2026-10-18)

All three devices were reflashed from `53d9971` and verified on the metal:
`gpu_mem=32` with full firmware, `/proc/pressure` present on the campods, the
module blocklist holding (41 modules / 5.02 MiB against 80 / 7.8 MiB before),
ISP nodes present and `rpicam-hello` enumerating the IMX708 at 4608x2592 on both
pods. The coordinator has no `gpu_mem` line and no `psi` by design -- neither
applies to that role.

The converge is now two playbooks (#453): `provision.yaml` once per card,
`deploy.yaml` routinely. Provision has no capture quiesce, which is why the
virgin-card `rc=5` failure is gone structurally rather than tolerated. Order for
a fresh fleet is `site.yaml` on the coordinator, then per campod, then
`deploy.yaml` alone -- coordinator first because until both ends are provisioned
there is no gadget network at all.

Packages now come from the image (`APT_INSTALL`), and SPI plus PPS are in the
coordinator role for #11. A converge of a freshly flashed card should no longer
contain a cold apt fetch.

Since then, and verified on the metal rather than inferred: all three devices
were reflashed from `ffac9b4`, the claim checker passes 21/21 on both pods and
15/15 on the coordinator, and the time base is live. The coordinator holds
`Reference ID: PPS` at stratum 1; both pods carry `gpiopin=26` on header 37/39
and are chrony clients of it at tens of microseconds. `chrony` and `pps-tools`
now come from `APT_INSTALL`, so the converge installs no packages at all.

Both pods carry a hand-patched `/boot/firmware/config.txt` -- `gpiopin` 24 to 26
-- so their `disk_image` reads `current: false` against main by design, until
they are next reflashed. That label is informational and gates nothing.

The flash-time seed is now the **fleet** key as its single `authorized_keys`
entry (dotfiles-symm#83), and operator keys arrive at converge from
`roles/ssh-access`, which takes the fleet key from the driver rather than from a
repo literal. Password login is on in the seed (dotfiles-symm#85) because a
single-entry seed with a wrong value denies every principal at once and the
coordinator role has no serial console.

## Established, with the check that would unseat it

True when written (**2026-10-11**) and each cost a session or more to establish. None is
settled in the sense of being beyond question -- a row whose check fails is a row that
rotted, and the check is cheaper than the original derivation. Do that rather than
deferring to this table.

| | check |
|---|---|
| **btrfs compression is off deliberately** (`dotfiles-symm` `011eb43`; it earned ~96 KB/s). Effective only between `f1bb7db` 09-12 and `011eb43` 09-16, so cards outside that window never had it | `grep -c compress /etc/fstab` -> `0`; `git log -S compress -- pi-image/assemble-btrfs.sh` for the window |
| **`ro`-`/usr` holds.** A converge remounts it `rw` and cannot restore it live; the reboot ending a converge does. A box reading `rw` has been converged since its last boot | `findmnt /usr -o OPTIONS` |
| **tryboot is inert on the Zero 2 W** -- firmware stores the flag and ignores it. The **partition number** in the same reboot argument *is* honoured | `sudo vcmailbox 0x00030064 4 4 0`; after `reboot '0 tryboot'`, `/boot/firmware/flash/result.txt` is still absent |
| **`cma-128` carries ~1.3x, not 2.5x** -- 99.6 MiB of 128 MB held, byte-identical on two pods, and a third set (~145 MiB) does not fit. The 2-of-each is NOT picamera2's `buffer_count`, which is 1 and which libcamera's `validate()` does not raise -- the second of each comes from below, so it moves with the suite pin rather than with our code | with capture running: `sudo cat /sys/kernel/debug/dma_buf/bufinfo \| tail -2` |
| **`CmaFree` is not headroom.** The page allocator fills the region with movable pages, so it falls over uptime with identical client demand -- 40 MB free at 107 s against 0.4 MB hours later on the same workload | compare `CmaFree` on two pods at different uptimes with the same `bufinfo` total |
| **ADXL345 runs at full rate** -- 3243 Hz at ODR 3200, both sensors, differing ~3% from each other. The old 1567 Hz was the drain loop taking half of what accumulated | fit `sum(n)` over the `boot_ns` span of any `accel-*.jsonl` |
| **The gadget network works, and when it does not, suspect the cable first.** 199 Mbit/s to one pod, 241 Mbit/s aggregate across two, ~0.35 ms. campod-sw was dark for days with every software layer correct -- `g_ether` bound, `/sys/class/udc` populated, the udev rule working, NM reporting `unavailable` (no carrier) -- because its lead was in the Zero's PWR jack and a charger was in the data jack. `lsusb | grep -c Gadget` on the coordinator is the one-line discriminator: software cannot make a device appear on the host's bus | `ping 10.55.0.1` from a pod; `ls /sys/class/net/br0/brif/` on the coordinator |
| **Steady-state capture, camera only at 1 Hz:** ~128 refaults/s, <1 pgmajfault/s, 1.25 MB/s written, 0.45 MB/s read, Dirty flat ~12 MiB with Writeback 0, capture RSS 29 MiB flat. Turning WiFi off changes none of it | two `/proc` samples 120 s apart in one ssh session; difference them |
| **Container uptime is not a duration.** No RTC, so a container's start is recorded under the pre-NTP clock while elapsed is computed against the post-step one. `boot_ns` and `monotonic_ns` in session files are honest | container uptime against `/proc/uptime` |
| **The PPS chain locks routinely and survives a reboot.** Coordinator at `Reference ID: PPS`, stratum 1, 33 ns fast / 390 ns slow on two readings days apart, RMS 313 ns and 2.9 us, both sources reach `377`. The boot unit configures the chip unattended | `chronyc tracking` for the refid; `cat /sys/class/pps/pps0/assert` for a nonzero sequence |
| **A DS3234 reading `0x00` in every register is the bus, not the chip.** Status reads `0x48` powered, so a zero there is an undriven MISO and `osf` carries no information. `2000-00-00` is impossible for the part -- BCD day and month power up to `01`. With `BBSQW=0` losing VCC stops SQW while timekeeping continues, so one disconnected wire gives a dead bus and zero edges at once | `sudo coord-rtc status`; if every register is zero, look at the 1 Hz LED rather than at software |
| **Capture's footprint grows at arm time, and it tracks frame size rather than `buffer_count`.** 12 MP at one buffer: anon 45.5 -> 135.5 MiB, 8808 minflt/s, which is 35.8 MB per kept frame -- exactly `main`'s buffer. 3 MP at four buffers: anon 45.7 -> 77.8 MiB and minflt at its paused baseline. Four times the buffers produced 2.8x *less* growth | difference `vmpage_number-anon_pages` and `vmpage_faults` across the phase boundary in `frames.jsonl` |
| **The limit on growing CMA is `dirty_background_threshold`, not free memory.** It is a fraction of free-plus-reclaimable, so it falls as capture eats the page cache -- 25 MiB paused to 16 MiB armed -- while dirty rises to 11.5. Armed at full resolution there is 67-71 MiB free with `vmscan_write`, `throttled_written` and writeback all zero and nothing ever blocked | `vmpage_number-{dirty,dirty_background_threshold}` from a 10 Hz bundle, per phase |
| **`watchdog0/bootstatus` is always 0 here** -- `bcm2835_wdt.c` declares no `WDIOF_CARDRESET` and never assigns the field. Not "did not fire": not reported | grep `WDIOF_CARDRESET` and `bootstatus` in `drivers/watchdog/bcm2835_wdt.c` |
| **NM will not manage a `DEVTYPE=gadget` interface.** Without the `90-` udev override the campod profile is inert and `usb0` stays DOWN with no error | `nmcli -f GENERAL.REASON device show usb0` -> reason 77 when the override is missing |
| **NM keyfile list properties are `;`-separated.** A space-separated `match.driver` is one pattern matching nothing, and autoconnect falls back to a default DHCP profile silently | `nmcli connection up campod-bridge-port ifname usbN` names the mismatch |
| **NM persists the radio switch.** `WirelessEnabled` is written to and read back from `/var/lib/NetworkManager/NetworkManager.state`, so `coord radio closed` survives reboots | `grep -i wireless /var/lib/NetworkManager/NetworkManager.state` |
| **`gpu_mem=32` is the floor on a campod, and 16 is unavailable at any price.** 16 is the only value that selects the cut-down firmware, and `start_cd` does not provide the VideoCore services the ISP rides on -- `vc_sm_cma` and `bcm2835_mmal_vchiq` both fail `-22`, `bcm2835_isp` creates no device nodes, libcamera's vc4 pipeline has no ISP. The sensor and Unicam stay healthy throughout, so it presents as a camera that will not start and names nothing about memory. Returns 31.9 MiB: `MemTotal` 424728 -> 457432 kB | `vcgencmd version` for `(start_cd)`; `ls /dev/video1[3-6]`; `rpicam-hello --list-cameras` |
| **The firmware rounds `gpu_mem` down to a multiple of 16**, so 17 and 24 both report `gpu=16M` and both get the cut-down firmware. The documentation presents the cut-down selection as the exact value 16. No granularity is published and `start.elf` is closed | set 24, reboot, `vcgencmd get_mem gpu` |
| **The camera holds its CMA across a pause.** `free_cma` drops from 79.5 MiB to ~1 MiB when the camera starts and shows no step at either phase boundary -- 3.5 MiB median paused against 4.2 MiB capturing. So idle-ready does not release the buffers and resuming does not re-pay the allocation, which was #434's stated mechanism risk | `vmpage_number-free_cma` across a boot that pauses and resumes; the absence of a step is the signal, not the value |
| **Steady-state capture is nowhere near the cliff; the import is the cliff.** Camera-only at 1 Hz: card **11.6%** utilised, 1.592 MB/s written, reads 0.020 MB/s, writeback flat at zero, 0.9 majflt/s, and **0.23 of 4 cores**. The import: **92.4%** utilised, 19.0 MB/s read, 26 majflt/s, 38.9% iowait on 0.25 cores -- blocked on the card, not computing | two `disk_io_time` samples differenced across each phase; `cpu-*` per state |
| **Writeback is not a mechanism here at this rate.** `nr_vmscan_write` and `nr_throttled_written` are both **0** while capturing, with dirty at 13.6 MiB under a 16.6 MiB background threshold. Reclaim never had to write a dirty page and no writer was throttled | `vmpage_number-{dirty,dirty_threshold,vmscan_write,throttled_written}` |
| **The bare OS holds ~49 MiB that cannot be reclaimed** -- 24 MiB anon, 25 MiB unreclaimable slab -- and both are unchanged at `gpu_mem=16`, so they belong to the OS rather than the split. Converged and capturing it is 97.1 and 30.4. So the OS is about a quarter of the unreclaimable anon and the payload stack is the rest | `/proc/meminfo` on a flashed, never-converged card |
| **The container runtime was the largest discretionary item in the ledger**: `dockerd` 26.9 MiB `RssAnon` and `containerd` 15.4 MiB, at **zero** CPU, zero reads and zero writes during capture -- more anon than the entire bare OS. That measurement is the case behind #449/#451, now landed as podman + quadlet | `/proc/<pid>/status` RssAnon against `processes-*` `ps_cputime` and `disk_octets` |
| **Packages come from the image now, not the converge.** `APT_INSTALL` per role installs podman, uidmap, collectd-core, zstd, git, curl, ca-certificates (+ `i2c-tools` on the coordinator) with `--no-install-recommends`. The ansible tasks stay as the path for a card that has not been reflashed | `packages/preinstalled` in the claim check |
| **`vmem`'s metrics are named `vmpage_*`, not `pgmajfault`.** `vmpage_faults` carries minflt and majflt as two values; the `nr_*` fields arrive as `vmpage_number-<name>`. `CollectContextSwitch true` produces no file at all | `ls /var/log/collectd/<boot>/<node>/vmem*` |
| **`collectd`'s `vmem` plugin cannot report `workingset_refault_file`** -- it dispatches `nr_*` by prefix plus a fixed list of named keys, and `workingset_*` matches none of them. It *does* give `pgmajfault` and all 51 `nr_*` fields | read the key dispatch in `collectd/src/vmem.c`; `grep -c '^nr_' /proc/vmstat` for the metric count |

## Open work (2026-10-11)

Treat the state column as the part that rots; check `origin/main` and the issue's comments.

| | state |
|---|---|
| **#372** re-image a rootfs without pulling the card | Unstarted and still the substantial one. No selection mechanism exists on a campod. Blocks #312. Adopting one costs every device a reflash |
| **#312** touchless reimage, bench side | Needs a mechanism from #372 |
| **#447** `gpu_mem` | Answered: 32, measured, shipped. Close it or leave it as the record of the measurement -- that is Seth's |
| **#452** the docs PR carrying the `gpu_mem` findings and the bare-OS baseline | Open last I looked |
| **#434** warm up and hold idle-ready | The measurement that was mine is closed -- the camera holds its CMA across a pause. The rest is flight-platform's |
| **#370** campod time | Built. Both roles carry the overlay on their own pin, the legs are wired, and the pods are chrony clients of the coordinator. What has no figure yet is the pods' own PPS refclock: the line was gated out of the rendered config until coordinator#495, so no pod has been measured on the fraction |
| **#251** disarm RO snapshot of `@data` | A decision Seth has declined to make, twice. Do not re-raise it as a thing to settle; it is not costing anything |
| **#90** bake container images into the image | Not built. Different and harder than baking packages: it needs the image store populated offline |
| **#236**, **#261** provisioning service, fleet SSH key | Not started |
| **#41** power-loss filesystem | Umbrella |
| wired `eth0` on the coordinator | Still nothing configures it. Parked until Seth can run a cable; `10.55.1.1/24` was the suggestion and the address is his |
| baking the rest of provision | Packages and the `/usr/local/bin` symlinks are the pair that would let provision drop the `/usr` hatch and its reboot. Packages are done. The symlinks, the static config files and the `modules-load.d` entries are classified in flight-platform's message on it; `i2c-dev` is cleanly bakeable, the gadget one is coupled to the hostname-derived MACs |

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

**Settle time is now a property of every boot.** Done -- #444 put `vmem` and
`processes` into the collectd config, so `pgmajfault`, the writeback picture and
per-process RSS and fault counts land on every device without anyone watching.
The remaining gap is that the per-process view structurally misses the import:
`capture`'s RSS median over that window is 0, because the process barely exists
for most of those samples, so its fault counters read 0 for the wrong reason.
Whole-system `vmpage_faults` sees it. Note also that the config loads no logging
plugin, so a collectd write failure is still invisible.

**Swap, and it is flash rather than zram.** Seth's framing, which is sharper
than the one this file used to carry: the problem with swap is not that it writes
to the card -- a genuinely cold page is written once and never read back. The
problems are descending into thrash with a narrow margin, and the kernel choosing
to evict something you wanted resident. He is prepared to turn it on given a
crisp theory about which processes hold allocated-but-idle pages.

That theory now has a named holder and a number: the container runtime held
41.3 MiB of `RssAnon` at zero CPU and zero I/O. Whether podman + quadlet changes
that is unmeasured -- the whole point of #451 is that nothing is resident between
starts, so it may have evaporated.

Two instruments if it is ever pursued, both read from source rather than
recalled. `can_reclaim_anon_pages()` returns true only if `get_nr_swap_pages() >
0`, so with no swap anon is pinned, full stop. And proactive reclaim takes a
`swappiness=max` token which sets `SWAPPINESS_ANON_ONLY` and `scan_balance =
SCAN_ANON` -- so `echo "64M swappiness=max" >
/sys/devices/system/node/node0/reclaim` evicts cold anon only, on demand, at a
size you name, with file pages structurally off limits. That addresses both of
his objections by construction rather than by tuning. `CONFIG_ZRAM=m` and
`CONFIG_ZSWAP=y`; `CONFIG_IDLE_PAGE_TRACKING` is unset, so there is no
`page_idle` to ask which anon is cold. MGLRU is on (`CONFIG_LRU_GEN_ENABLED=y`),
so `/sys/kernel/debug/lru_gen` is an aging histogram nobody has looked at.

Size the swap to the cold-anon estimate rather than to RAM; a small device
cannot become a mechanism for running a working set larger than memory.

**Transparent huge pages are not available.** `CONFIG_TRANSPARENT_HUGEPAGE`,
`CONFIG_READ_ONLY_THP_FOR_FS` and `CONFIG_HUGETLBFS` are all unset in the Pi
arm64 defconfig, and `/sys/kernel/mm/transparent_hugepage/` does not exist on the
device. Getting them means a custom kernel, which abandons "take the vendor
kernel as-is" -- the premise the whole image build rests on. Raised because the
page-cache index is 10.7 MiB of slab and huge pages look like they would shrink
it; that is 2.6% of the board and a different order of cost from everything else
on this list.

**Readiness is a system property, not an application one.** I argued that `capture.py`
should report its own warm state; Seth's correction is the right one and worth carrying --
a process that allocates CMA buffers and thereby evicts pages has very little visibility
into that secondary flow faulting back in elsewhere. So readiness needs outside
observation, which is what makes the collectd question above matter rather than being
tidiness.

## If the pods move off the Pi Zero 2 W

Live as of 2026-10-15, with five Radxa Zero 3W (RK3566, 2 GB) ordered and arrival from Hong
Kong unknown. Recorded so it is not re-derived: what is **measured**, what is **read from a
config**, and what is **not established** are labelled, because that distinction is most of
the value here.

**Why the question exists.** Zero 2 W supply and price show no sign of recovering, and the
fleet owns exactly four. Separately, a C-mount Pregius global-shutter camera needs USB 3.0 to
drive it, and a Zero 2 W is USB 2.0 with a single OTG port -- so it cannot host such a camera
at all. The camera choice forces the board question rather than coinciding with it.

**The boot layout, measured** by streaming the first 48 MiB of
`radxa-zero3_debian_bullseye_cli_b6.img.xz` and parsing it. Protective MBR (type `0xee`) plus
GPT, with two raw blobs *ahead* of the first partition:

| offset | content |
|---|---|
| sector 64 | `RKNS` -- Rockchip idbloader |
| sector 16384 | `d00dfeed` -- U-Boot FIT (`u-boot.itb`) |
| sector 32768 | partition `config`, 16.8 MB |
| sector 65536 | partition `boot`, 314.6 MB |
| sector 679936 | partition `rootfs`, 2201.5 MB |

So `build-image.sh`'s `parted mklabel msdos` plus `sfdisk --disk-id 0xc0dec0de` would erase
both loader blobs and the GPT. A re-lay has to preserve sectors 64 through 32767 verbatim and
emit GPT. The fixed-MBR-id trick goes with it -- GPT has its own stable identifiers, so
`disk/partuuid-stable` is replaceable rather than lost, but it is a rewrite and not a rename.
Armbian's own board config agrees with the measurement, which is why this reads as the
platform's shape rather than one build's accident: `IMAGE_PARTITION_TABLE="gpt"` and
`BOOT_SCENARIO="spl-blobs"`.

**The OS base is Armbian, not Radxa.** Radxa's own CLI images for this board stop at `b6`,
2024-01-10, on Debian bullseye; everything since (`rsdk-t1` .. `rsdk-b1`, to 2025-07-15) is
KDE desktop on bookworm. There is no current official CLI image and nothing on trixie. Read
from `armbian/build`: `config/boards/radxa-zero3.conf` is a `.conf` rather than a `.csc`, so
standard support rather than community, maintainer `igorpecovnik`,
`KERNEL_TARGET="vendor,current,edge"` with `KERNEL_TEST_TARGET="vendor,current"`, and
`BOOT_FDT_FILE="rockchip/rk3566-radxa-zero3.dtb"`. A current artifact exists:
`Armbian_26.8.0-trunk.420_Radxa-zero3_forky_vendor_6.1.115_minimal.img.xz`, 2026-07-17 -- a
minimal image on forky, ahead of our trixie rather than behind it.

**The ports, from Radxa's marketing copy rather than a schematic:** USB 3.0 HOST Type-C for
data, USB 2.0 OTG Type-C for power and data. Two controllers, so the two roles a pod needs --
USB device on the coordinator's hub for the gadget link, USB host for the camera -- do not
contend. The gadget half lands on the USB 2.0 OTG port, which is a dwc2-family controller, so
`g_ether` is the configuration already in use with `dr_mode = "peripheral"` in device tree
instead of a `dtoverlay=dwc2` line. It also collapses the two-micro-USB trap that left
campod-sw dark for days: there is one port that carries power and it is the data one.

**What transfers unchanged:** `assemble-btrfs.sh` entirely, `grow-rootfs`, the
`/etc/fleet-image` manifest, the purge-simulate / mark-manual / mask-timers machinery, the
chrony and hardware-sync design, and roughly 12 of the 21 claim checks. The converge is close
to platform-neutral already -- `vcgencmd` is its only Pi dependency and is already behind a
`command -v` guard that self-disables.

**What needs a second implementation:** the firmware config layer. 32 `dtoverlay`/`dtparam`
hits, 25 `cmdline.txt`, 18 `auto_initramfs` across the image build, all of it Pi-firmware
shaped, against ordinary DT overlays and boot args on Rockchip.

**What disappears:** `gpu_mem` and the cut-down-firmware finding, the CMA arithmetic, the
`psi=1` justification, the no-swap reasoning, and the 417 MiB budget generally. At 2 GB, a 128
MiB CMA reservation is 6% rather than 31%. None of those findings become wrong; they stop
applying to pods. And 4x the page cache is aimed at the measured mechanism rather than at
headroom in general -- the import storm was 92.4% card utilisation and 19 MB/s of reads,
blocked on the card.

**What gets better:** A/B boot. U-Boot does real slot selection, so #372 and #312 stop being
blocked on firmware that stores the tryboot flag and ignores it.

**Caveats, both unverified on hardware.** WiFi is an AIC8800 served by an out-of-tree driver
carried as an Armbian extension (`AIC8800_TYPE="sdio"`, `enable_extension "radxa-aic8800"`),
not an in-tree module like `brcmfmac` -- tolerable where the operating state is the gadget
link and WiFi is bench mode, but `coord radio open` depends on a third-party driver continuing
to build. And nobody has checked whether that `vendor_6.1.115` kernel ships `pps_gpio`, the
dwc2 gadget, `btrfs`, `spidev` and `uvcvideo`; every one of those is load-bearing, and the
rootfs sits 348 MB into the image so it needs the full download rather than a prefix.

**Not evaluated:** the Cubie A7s units, which have more RAM. The axis that decides it is
Allwinner's mainline position against Rockchip's -- RK3566 has `current` and `edge` branches in
Armbian's test matrix, where newer Allwinner parts tend to be vendor-BSP-only.

## How to work here

**Seth is the only decision-maker, and he is unusually good at catching a wrong premise.**
When he corrects you, the correction is normally right and normally sharper than your
version -- check it rather than taking it, then accept it rather than defending. Several
times this session he reframed something in one sentence that I had been circling for
several: that the probe-quiesce collision is "asking makes them not ready"; that bench
recovery is a solvable problem rather than a constraint on the design; that `CmaFree` was
never the number. Peers do not speak for him, and a peer reporting that he ruled something
is not authorisation.

**You are the critic you keep arguing with.** The single most repeated correction
of this session. I wrote "the difference between 24 and 32 isn't rounding -- it's
a real fraction of the headroom" and Seth's answer was: nobody thinks resources
are free, stop arguing against a strawman, and stop suggesting one to yourself in
order to beat it. The same habit produced "that is the appliance model rather than
a deviation from it" in a comment, and a whole bold-headed RISK section in a PR
for a thing I had no evidence against. If a sentence exists to pre-empt an
objection, cut it and ask who you thought would make it.

**Say what a number proves, not what it suggests.** I reported 1.09 GB read into a
214 MiB cache as "4.9x oversubscribed, so it is thrashing". The ratio is
read-volume over cache-size and cannot distinguish re-reading a small set from
streaming a large one. Seth caught it immediately. The thing that actually settles
it is `workingset_refault_file`, because a first read of a page has no shadow
entry and does not count as a refault -- and #316 had already measured it at
~6,000 pages/s, matching the entire read rate. The evidence existed; I reached for
an arithmetic flourish instead.

**And the steady state is proof about the working set.** His definitional point:
if the steady-state working set did not fit, the box would be thrashing
continuously, and it is not -- 128 refaults/s, 11.6% card utilisation. So what
exceeds the cache during the import is a transient footprint, not the working set.
I had used the term for the wrong thing and drawn a pessimistic conclusion from it.

**Do not restate another component's internals in this layer's files.** The CMA
comment in `roles/campod/config.append.txt` carried picamera2's whole still
configuration -- `buffer_count`, stream formats, IMX708 geometry. Someone else's
detail, in my file, and by the time anyone read it the measured value was double
what it said. A one-line reference to the issue that holds the arithmetic is what
belongs there. Interface boundaries are supposed to make this unnecessary; #80 is
the cleanup and the categories are worth re-reading before writing a comment.

**Commit messages are for history; comments are for the current design.** "An
earlier version of this argument was that four cores were already short" does not
belong in a role file. Neither does a forecast of what a peer will do in a future
PR.

**A negative result from a tool is a fact about your lookup, not about the
world.** I told Seth `configuration.html` does not mention `gpu_mem`; it
documents `start_cd.elf`, and I had already seen a truncation warning on the
previous fetch. Separately I asserted that the Pi docs say camera use wants more
than the minimum -- unsourced, and the docs say the opposite. Both were stated as
documentation rather than as the limit of what I had read.

**Look it up before spending reboots.** I bisected `gpu_mem` across four reboots
to find a granularity that no amount of bisecting could have *explained*. Seth:
"sequential reboots in a binary search is a heavyweight way to avoid asking
anyone." The search turned out not to contain the answer either -- but establish
that first, not after.

**Use the tooling, and check whether it is current before concluding it is
stale.** I ran ansible locally against a device and justified it on the grounds
that fleet-control's baked playbook was behind. It was not -- the pod had rolled
eighteen minutes earlier and I never looked. The cost is not just tidiness: no run
record, no provenance, no visibility to the other agents, and the thing Seth is
building does not get exercised. `GET /build` answers it in one call.

**His procedure has reasons even where it does not state them.** He said delete
before converge. I deleted on the full card and skipped it on the one with 23 G
free, having substituted my own criterion -- does the converge have room -- for
his, which is that the run starts from a known empty card. Free space and session
age are the two variables nothing has ever separated, so I uncontrolled one of
them on the only pod with the sensors.

**Do not launch anything into a settling box.** I started a converge the instant
the previous one's reboot returned, which put apt into the import storm. The box
went down under it, ansible hung 34 minutes on a dead connection because that task
has no timeout, and the card ended up carrying a stale collectd config. After any
reboot, wait out the settle -- 70-130 s measured -- before the next action.

**When a measurement is already recorded, do not re-caveat it.** I re-derived
doubt about the `rchar`-versus-`read_bytes` attribution that had been taken in
exactly the window in question. If there is no reason to think it has changed,
use it.

**Ask what the question is before answering a bigger one.** He asked whether
installing software in the image was easy. I answered with a migration plan, a
cost table, and an offer to edit someone else's repo. The answer was one line.

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
