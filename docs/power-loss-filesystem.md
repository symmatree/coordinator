# Power-loss-tolerant filesystem & capture (coordinator)

The coordinator is powered from the avionics 5 V rail, so **every normal power-down is a yank**
(disarm → unplug the XT60) and crashes / brownouts cut it mid-write. This is the pointer doc for how
we make the coordinator survive that without data loss. It is a **plan**, landing incrementally — not
yet fully built.

## Where this lives

**This document is the design.** It absorbed the general pattern from
`facts/topics/power-unstable-pi.md` on 2026-09-12; that page is now a pointer here plus the
PocketTerm35 device profile, which is not coordinator material.

- **The tracking issue + decisions** —
  [#41](https://github.com/symmatree/coordinator/issues/41), the umbrella. See its **2026-07-12 design
  update** for the chosen filesystem and the scope→issue index.
- **The image build** — `symmatree/dotfiles-symm/pi-image`: `build-image.sh`, `assemble-btrfs.sh`,
  the per-role knobs, and `provision/` for per-unit identity at flash time
  ([#96](https://github.com/symmatree/coordinator/issues/96)). Host convergence is **this repo's**
  `host/ansible`, reached via `host/one_time.sh <role>` — `dotfiles-symm/ubuntu-zsh` is a workstation
  bootstrap and is not run on fleet devices.
- **Sibling device** — the PocketTerm35 "pipboy"
  ([tiles #599](https://github.com/symmatree/tiles/issues/599)), first unit on this layout.

## The chosen filesystem: btrfs subvolumes, no overlay

Not a RO-base + overlay (that was the earlier call — superseded 2026-07-12). Instead, btrfs with
**granular per-subvolume ro/rw** — stronger protection than an overlay, with no ramdisk catching
writes and no custom initramfs:

> [!WARNING]
> **Read-only `/usr` is NOT actually enforced as built** (verified 2026-08-14 on the first unit, pipboy).
> The `ro` reaches fstab, the generated `usr.mount` unit, and even the initramfs mounts `/usr` read-only —
> but `@usr` and `@` are **one btrfs filesystem sharing one superblock**, so when `systemd-remount-fs`
> remounts `/` **read-write** at boot, `/usr` comes up **`rw`** too, and a live `/usr` can't be flipped
> back (`remount,ro` → "busy"). Consequences: the "mount `ro` → `remount,rw` for apt → `remount,ro` after"
> cycle described in the table **cannot complete**, and the *"SD is fine because ro-`/usr` keeps write
> volume low"* rationale below **does not currently hold** — which matters most for the SD roles
> (coordinator + campods). Open design question tracked in [#96](https://github.com/symmatree/coordinator/issues/96);
> full evidence chain in `facts/topics/power-unstable-pi.md` → "Reality check — read-only `/usr` is NOT
> actually enforced". Candidate fixes: put `@usr` on a **separate btrfs filesystem** (its own superblock,
> so its `ro` is independent), a late-boot unit that re-asserts `ro` after `systemd-remount-fs`, or drop
> the live-ro claim and use per-service `ProtectSystem` + snapshots instead.

All subvolumes `noatime`; the filesystem is `mkfs.btrfs -m single` (single metadata, no DUP — SD write-amplification). One btrfs FS → one UUID; the `subvol=` mount option differentiates the mounts (in `/etc/fstab`).

| Subvolume | Mount (option) | Contents / why |
|-----------|----------------|----------------|
| `@` | `/` (`compress=zstd`) | root. |
| `@usr` | `/usr` (**`ro`** — ⚠️ *not enforced as built, see warning above*) | the OS binaries/libraries — can't be written mid-cut, so can't corrupt. **Mount-option `ro`** (not the btrfs ro *property*), *intended* so `remount,rw` → apt → `remount,ro` works live for ansible maintenance, no reboot. **In practice `/usr` comes up `rw` (shared superblock); [#96](https://github.com/symmatree/coordinator/issues/96).** |
| `@var` | `/var` (`compress=zstd`) | `journald`, Docker `data-root` (images survive reboot; `/var/lib/docker` is `chattr +C` / nodatacow — CoW-on-CoW footgun for overlay2), spool. |
| `@home` | `/home` (`compress=zstd`) | operator home (the checkout, interactive scratch that must survive a reboot). |
| `@data` | `/var/lib/coordinator` (`compress=zstd`) | config + captures — the precious data; **nests under `/var`** (mount after `@var`). Disarm takes an **RO snapshot** of this (#88). |
| `@snapshots` | `/.snapshots` | snapshot store (incl. the disarm RO-snapshots). |
| FAT | `/boot/firmware` (**`rw`**) | firmware. Was `ro` by design; it is **`rw` as built**, and deliberately so: every vendor first-boot mechanism *deletes its own trigger file* from this partition (`firstrun.sh` removes itself; `imager_fixup` rewrites `cmdline.txt`), so `ro` broke all of them. Related: `nofail` here dropped the mount's `Before=local-fs.target` ordering and let `firstrun.sh` race an empty mountpoint ([dotfiles-symm#41](https://github.com/symmatree/dotfiles-symm/pull/41)). |
| `/tmp`, `/run` | tmpfs | normal, small — the *only* ramdisk. |

Boot config is **standard, no custom initramfs hook**: `cmdline.txt` carries `rootfstype=btrfs rootflags=subvol=@`, and the stock Pi initramfs already has the btrfs module.

Why btrfs over the overlay: tmpfs-upper overlay costs RAM we can't spare on the 512 MB Zero 2 W campods;
disk-upper + conditional-reset needs a custom initramfs hook. Subvolumes give ro-where-it-matters +
CoW crash-consistency + checksums (detect SD FTL rot ext4 serves silently) + snapshots, with only
standard btrfs-root boot config. **Medium:** SD is fine *because* ro-`/usr` keeps write volume low;
escape hatch if capture volume grows is an f2fs data partition or a USB SSD (btrfs is unambiguously
good on the pipboy's NVMe).

## How it's built: convert the vendor image (in `dotfiles-symm/pi-image`)

A custom subvolume layout can't come from a stock flash, and the Foundation's declarative
`rpi-image-gen` can't express it either — a 2026-07-30 spike found it does a **single** btrfs root
(+ `-m single`) natively but has **no subvolume support** (its genimage step populates the top-level
subvolume; the generated fstab is hardcoded `defaults`). So the layout is assembled by our own code.

**What is built and running** is the **convert** path: take the official Raspberry Pi OS Lite image,
regenerate its initramfs with btrfs in a native arm64 chroot, and re-lay its rootfs into the
subvolumes above via `dotfiles-symm/pi-image/assemble-btrfs.sh`, which also writes the `fstab` and
fixes up `cmdline.txt`/`config.txt`. Per-role, in CI, on an arm64 runner. Keeping the vendor boot
stack known-good made the btrfs root the only new variable.

**mmdebstrap-from-scratch** (build the rootfs from nothing, then the same assembly step, then
genimage) remains the reproducible follow-on and is not built.

**Status (2026-09-12): the gate is cleared.** A Pi does boot from a btrfs-subvolume root on the
stock initramfs — proven on two units and two media: the PocketTerm35 on a Pi 5 from SD (then cloned
to NVMe), and a campod on a **Zero 2 W from SD**, provisioned headless. `@` and `@usr` both mount,
`initramfs8` loads under `auto_initramfs=1`, and the vendor first-boot mechanism runs. The subvolume
assembly is separately verified against a real btrfs kernel: all seven subvolumes assemble, mount per
the fstab, and the split is exclusive.

**Still unproven: the Pi 4B.** The coordinator itself has never booted this image — the two proven
units are a Pi 5 and a Zero 2 W. That is the remaining hardware-class gate, and the coordinator has
no serial console by design (the FC owns that UART), so HDMI is the diagnostic for a boot that does
not come up.

(Gotcha found in the spike: btrfs `compress` is a **per-superblock** option, not per-mount, so
`@usr` inherits `@`'s `compress` regardless of its fstab line — harmless. `noatime`/`nodev` are true
per-mount VFS flags — but **`ro` turned out NOT to be reliably per-mount here**: because `@usr` shares
`@`'s superblock, remounting `/` rw at boot drops `/usr`'s read-only flag, so `/usr` ends up `rw`
despite the fstab `ro`. This is the correction to the spike's assumption — see the warning at the top
of this section and [#96](https://github.com/symmatree/coordinator/issues/96).)

### Built images (where they are, how they were made)

The first flashable image was built via the **convert** path (fastest to a testable image; the
mmdebstrap from-scratch build stays the reproducible follow-on): take the **official Raspberry Pi
OS Lite (Bookworm, arm64, pinned 2025-05-13)** and re-lay its rootfs into the subvolume layout
above, keeping the vendor boot stack known-good.

- **Built by:** `symmatree/dotfiles-symm`, `pi-image/build-image.sh` + the `build-pi-image` GitHub
  Actions workflow (arm64 runner -- needs a btrfs kernel + native arm64 chroot; can't run on the
  x86/no-btrfs notebook). PR [dotfiles-symm#21](https://github.com/symmatree/dotfiles-symm/pull/21).
- **Where to get one:** the `build-pi-image` workflow's artifacts, per role
  (`coordinator-pi-btrfs-img`, `campod-pi-btrfs-img`, `pocketterm-pi-btrfs-img`). Regenerable, not
  source-controlled; the artifact is a zip containing `<role>-pi-<YYYYMMDD>.img.xz`.
- **Which image a card came from** is recorded on the card itself at `/etc/fleet-image`
  (`IMAGE`, `ROLE`, `SOURCE` = the dotfiles-symm commit, `BASE` = the upstream filename), echoed
  into the journal each boot and shown pre-login on the console. It is the one layer that cannot
  compute its own staleness, because nothing tells a card what the newest artifact is.
- **There is no automated boot test.** A `qemu-system-aarch64 -M virt` smoke test existed and was
  removed: the RPi downstream kernel doesn't init virtio on that synthetic platform, so it returned
  UNKNOWN on all 38 runs it was in while costing 9 minutes of arm64 runner time per build. A
  hardware boot answers the question it stood in for, and answers it better.

## Why a snapshot survives a cut

The disarm mechanism below rests on a property worth stating explicitly, because it is what makes
"snapshot and walk away" safe without a shutdown: **a btrfs read-only snapshot survives a later power
cut.** Take it as *stop writers → `sync` → `btrfs subvolume snapshot -r`*. Once committed it is
immutable, and because btrfs is copy-on-write, committed data and metadata are never overwritten — a
cut drops you back to the last committed transaction with the snapshot intact. If the *live* `@data`
is scrambled by the later cut, the snapshot is still clean and `scrub`-verifiable.

The residual risk is the drive's own volatile cache in the seconds around the snapshot, which is not
a concern for a snapshot taken well before the cut. This is why #88 does not need to also power the
machine down — and must not, since you need to re-arm freely.

## Capture formats that survive a torn tail

The filesystem protects what has been committed; it cannot help a file that was mid-write. For data
the vehicle *must* keep, two layers:

- **Primary: event-triggered flush + snapshot.** On the natural "we're done" event (disarm), stop
  writers → `sync` → read-only snapshot. Durable immediately (#88).
- **Backstop: segmented, append-only, self-framing, checksummed logs** (#89). Roll the file every N
  seconds or MB; `fsync` and close each segment, and `fsync` the *directory* when creating the next
  so the new dirent is durable. A cut then damages only the currently-open segment; every closed
  segment is complete. Records carry length prefixes and CRCs so a reader recovers everything up to a
  torn tail.

The backstop is what saves you when no disarm fires, which is exactly the uncontrolled-loss case in
the worked example below.

| | |
|---|---|
| **Good formats** | framed records with CRC, NDJSON with per-line integrity, SQLite in WAL mode, rosbag2 **mcap**, MAVLink dataflash/tlog |
| **Dangerous** | anything that writes its index or footer only at *close* — naive Parquet/HDF5, container video indexed on close. Segment these, or use a streamable variant. |

This is writer-dependent: it needs a format that tolerates a torn tail, so it gets built once the
actual writer and format are known.

## Trimming the write surface

Fewer writes means a smaller corruption window and longer flash life, at no loss of capability:
`noatime` everywhere (already in the fstab above), `/tmp` and `/run` on tmpfs, **zram swap** rather
than disk swap, and journald local — readable on-screen when offline, shipped when connected.

## Two data sinks, not one

Do not conflate these:

- **Local `@data`** on the device's own drive — always available, offline, snapshotted. This is the
  store that matters, and the only one the vehicle depends on.
- **A network share** streamed to *when connected* — optional, never required to boot, and absent in
  flight by definition. Useful for the PocketTerm; never for the coordinator airborne.

Telemetry follows the same rule. Ship to the cluster when reachable, but **do not build unbounded
buffering** — that is a config matter, not an architecture one: Grafana Alloy's shippers buffer to a
disk-backed WAL, so point the WAL at `@scratch` and cap it by age and size. Offline data queues to a
bounded on-disk buffer and drops oldest past retention; metrics simply have holes for disconnected
windows, which is expected. (Exact WAL block names and limits: confirm against current Alloy docs —
not verified here.)

## Rescue and recovery paths

- **Keep the previous card.** The single cheapest recovery path is the card the device was running
  before a reflash. It is also the discriminator when a fresh image does not boot: boots on the old
  card means the image, boots on neither means the hardware.
- **Boot order.** On the Pi 5 an SD can sit second in the boot order (`BOOT_ORDER=0xf416` tries NVMe
  then SD) so a corrupted primary falls back on its own. The Pi 4B and Zero 2 W are single-medium;
  their rescue path is swapping the card.
- **Why Raspberry Pi OS and not Ubuntu.** First-party Pi hardware support — display overlays,
  `rpi-eeprom`, fan dtparams, the vendor-assumed kernel and firmware. Ubuntu runs on a Pi but you
  fight harder for those, and its one draw (`overlayroot`) is moot because the subvolume layout does
  that job without an overlay.

## The primary safety mechanism is graceful sync at disarm

Not the filesystem — the discipline. If every disarm flushes + `sync`s (later: btrfs RO-snapshot of
`@data`) **and signals done physically** (you're at the vehicle, no SSH), the only lossy events left
are pulling power while armed or a brownout — where a perfect mapping mission isn't expected anyway.
`coord shutdown` is ~an alias (a clean `poweroff` already unmounts + syncs); its value is being the
pHAT button target + the safe-to-cut indicator hook.

## Worked example — 260712 tree-crash (first real drop during capture)

An uncontrolled hard cut mid-flight (tree strike, Pi physically disconnected, **no graceful disarm**)
gave us ground truth — [full writeup on #41](https://github.com/symmatree/coordinator/issues/41):

- **ext4 + `fsck.repair` recovered fully clean, automatically** (journal replay + orphan cleanup, no
  I/O / SD / ext4 errors, zero intervention). This is the **reward for planning** for power loss — not
  a reason to stop: the btrfs subvolume migration (#96) stays the planned next step (stronger
  guarantees + fleet repeatability), with ext4 + append-only as the working *interim* parachute. You
  don't rely on the parachute for your commute — build btrfs deliberately off-vehicle, not live.
- **The append-only `.feat` lost exactly one 162-byte frame** of a 34,505-frame recording — the framed
  format is the resilient pattern (#89).
- **The several 0-byte image files (a ~30 s tail) are a coordinator write-path artifact — code-confirmed,
  not a camera event.** #72 writes each still/disparity with a synchronous `cv::imwrite` and **no
  `fsync`** (`feature_tracker.cpp`), one fresh file per frame — so each lands in the OS page cache and
  returns "ok"; Linux holds dirty pages up to ~30 s (default `dirty_expire`) before writeback. The cut
  lost the whole unflushed window and ext4 delayed allocation left those inodes at 0 length. The camera
  almost certainly ran to the end; the apparent "images stopped ~20 s early" is differential durability:
  `.feat` is one continuously-flushed file (tail-only loss), the images are many fresh unflushed files
  (whole-file loss).
- **Crash survival rides on the on-disk write path (#89), not the disarm-flush (#88)** — no disarm fires
  on an uncontrolled loss. #89 is now **demonstrated, not latent**: **tmp → `fsync` → `rename`** per file
  collapses the loss from ~30 s of files to at most the one in flight (making stills behave like
  `.feat`); plus verify the `.feat` reader tolerates the torn final record. New gap: persist the journal
  ([#100](https://github.com/symmatree/coordinator/issues/100)).

## Scope → issues

| Aspect | Issue |
|--------|-------|
| FS/power-loss architecture (umbrella + decision) | [#41](https://github.com/symmatree/coordinator/issues/41) |
| Repeatable btrfs image build, fleet-wide (**convert** path built in `dotfiles-symm/pi-image`; mmdebstrap-from-scratch is the follow-on; `rpi-image-gen` can't do subvolumes) | [#96](https://github.com/symmatree/coordinator/issues/96) |
| Laptop-free shutdown: pHAT button + poweroff + safe-to-cut indicator | [#87](https://github.com/symmatree/coordinator/issues/87) |
| DISARM → stop still capture + fsync + `sync`/snapshot + physical done-signal | [#88](https://github.com/symmatree/coordinator/issues/88) |
| Power-loss-safe capture format (`.feat` #83 + stills #72) | [#89](https://github.com/symmatree/coordinator/issues/89) |
| Images present offline (pre-baked at build time / rw `@var`) | [#90](https://github.com/symmatree/coordinator/issues/90) |
| Stack auto-starts capturing on boot (systemd oneshot) | [#97](https://github.com/symmatree/coordinator/issues/97) |
| Persist the journal for post-crash forensics | [#100](https://github.com/symmatree/coordinator/issues/100) |
| Sibling / first btrfs device (PocketTerm) | tiles #599 |

**Near-term** (software, any RAM size, no reflash — lands on the current card *and* survives into the
btrfs image unchanged): #87 + #88 + #89 + #97. The btrfs image (#96) exists and boots; what remains
there is the coordinator's own first flash onto a Pi 4B.

## Related

- [coordinator-network.md](coordinator-network.md) — the 2026-07-04 recovery this spun out of, and #41.
- [architecture.md](architecture.md) — runtime paths (`/var/lib/coordinator/*`) and the Top pHAT UC2
  control surface used by #87.
- #42 — disarmed bench capture (the concrete "the in-progress file is the irreplaceable artifact" case).
