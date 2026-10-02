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
driver or network plumbing rather than payload: module loading, the gadget network, swap and
journald policy, i2c.

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

A corollary: ownership notes in a *peer's* handoff are addressed to that peer. One agent's
file saying a bug belongs elsewhere is not an allocation of your scope.

## Reading the artifacts here

Three different failure modes, which need three different checks.

**A bug describes a change, so its requirement and its assumptions age differently.** If it
says "it works like x and should work like y" and the code says x, the code is *confirming*
it. Check the facts a bug asserts about current state; leave its requirement alone. Do not
substitute "read the code first" for reading the bug -- that closes valid work.

**Read the comments, not just the description.** Under the model above, a bug's current shape
is usually in its comments and sometimes only in a commit message. #316's description had
arithmetic its own comments had already corrected.

**Distrust urgency, in docs especially.** A doc describes what is, so it stays useful as it
ages, but an outage narrative inside one keeps firing long after the fix. "Stops answering
SSH for minutes" outlived its own resolution by weeks here. When writing: a static record of
currently-understood facts, not a record of events.

## Checking a device

```bash
dotfiles-symm/pi-image/run-claims-check.sh <host>
```

Asserts that the image's mechanisms took effect on a booted device -- sentinels for fragile
things, not a golden copy of the config. Baselines when last run (2026-09-23): coordinator
14 pass / 0 fail, campod-sw 18 / 0.

## Established, with the check that would unseat it

True when written (**2026-10-02**) and each cost a session or more to establish. None of
them is settled in the sense of being beyond question -- a row whose check fails is a row
that rotted, and the check is cheaper than the original derivation. Do that rather than
deferring to this table.

| | check |
|---|---|
| **btrfs compression is off deliberately** (`dotfiles-symm` `011eb43`; it earned ~96 KB/s). Effective only between `f1bb7db` 09-12 and `011eb43` 09-16, so cards outside that window never had it | `grep -c compress /etc/fstab` -> `0`; `git log -S compress -- pi-image/assemble-btrfs.sh` for the window |
| **`ro`-`/usr` holds.** A converge remounts it `rw` and cannot restore it live; the reboot ending a converge does. A box reading `rw` has been converged since its last boot | `findmnt /usr -o OPTIONS` |
| **tryboot is inert on the Zero 2 W** -- firmware stores the flag and ignores it. The **partition number** in the same reboot argument *is* honoured ([`BOOT-SELECTION.md`](https://github.com/symmatree/dotfiles-symm/blob/main/pi-image/BOOT-SELECTION.md)) | `sudo vcmailbox 0x00030064 4 4 0` reads the flags; after `reboot '0 tryboot'`, `/boot/firmware/flash/result.txt` is still absent |
| **`cma-128` carries ~1.3x, not 2.5x** -- the running config allocates `buffer_count=2`, 99.6 MiB of 128 MB. A third buffer (~145 MiB) does not fit | with capture running: `sudo cat /sys/kernel/debug/dma_buf/bufinfo \| tail -2` |
| **ADXL345 runs at full rate** -- 3243 Hz at ODR 3200, both sensors, differing ~3% from each other. The old 1567 Hz was the drain loop taking half of what accumulated | fit `sum(n)` over the `boot_ns` span of any `accel-*.jsonl` |
| **The gadget network works** -- 199 Mbit/s to one pod, 241 Mbit/s aggregate across two, ~0.35 ms (#354, #373) | `ping 10.55.0.1` from a pod; `ls /sys/class/net/br0/brif/` on the coordinator |
| **`watchdog0/bootstatus` is always 0 here** -- `bcm2835_wdt.c` declares no `WDIOF_CARDRESET` and never assigns the field. Not "did not fire": not reported | grep `WDIOF_CARDRESET` and `bootstatus` in `drivers/watchdog/bcm2835_wdt.c` |
| **NM will not manage a `DEVTYPE=gadget` interface.** Without the `90-` udev override the campod profile is inert and `usb0` stays DOWN with no error | `nmcli -f GENERAL.REASON device show usb0` -> reason 77 when the override is missing |
| **NM keyfile list properties are `;`-separated.** A space-separated `match.driver` is one pattern matching nothing, and autoconnect falls back to a default DHCP profile silently | `nmcli connection up campod-bridge-port ifname usbN` names the mismatch explicitly |

## Open work in this layer (2026-10-02)

Treat the state column as the part that rots; check `origin/main` and the issue's comments.

| | state |
|---|---|
| **#372** re-image a rootfs without pulling the card | Unstarted. No selection mechanism exists on a campod. Blocks #312. Adopting one costs every device a reflash |
| **#312** touchless reimage, bench side | Needs a mechanism from #372 |
| **#434** warm up and hold idle-ready | WiFi side is PR #435. The open measurement in this layer is whether libcamera holds its CMA buffers while nothing retrieves -- `bufinfo` before and after an idle. Not a paging question; CMA is not pageable |
| **#370** campod chrony/PPS client | Under #11's five-edge design. Parts in hand, nothing wired |
| **#251** disarm RO snapshot of `@data` | A decision, never made |
| **#90** bake container images into the image | Not built |
| **#236**, **#261** provisioning service, fleet SSH key | Not started |
| **#41** power-loss filesystem | Umbrella |

## The charge on this file

Update it at the **end** of a session, not during one. When something here becomes a durable
fact about the system it graduates to a real doc; when it becomes work it becomes an issue.
What is left is what would otherwise be re-learned by collision.
