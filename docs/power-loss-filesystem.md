# Power-loss-tolerant filesystem & capture (coordinator)

The coordinator is powered from the avionics 5 V rail, so **every normal power-down is a yank**
(disarm → unplug the XT60) and crashes / brownouts cut it mid-write. This is the pointer doc for how
we make the coordinator survive that without data loss. It is a **plan**, landing incrementally — not
yet fully built.

## The design lives in three places

- **The shared pattern** — `facts/topics/power-unstable-pi.md` (private `facts` repo): a
  resilient-setup pattern for power-unstable, often-offline Pis, with a **rekon10 Coordinator device
  profile**. Core idea = shrink the corruptible surface, then defend it.
- **The tracking issue + decisions** —
  [#41](https://github.com/symmatree/coordinator/issues/41), the umbrella. See its **2026-07-12 design
  update** for the chosen filesystem and the scope→issue index.
- **The base recipe + image build** — `symmatree/dotfiles-symm` (`ubuntu-zsh/` Ansible): the master
  host bootstrap the whole Pi fleet converges onto, and the intended home for the **image-build
  pipeline** ([#96](https://github.com/symmatree/coordinator/issues/96)). First btrfs device is the
  PocketTerm35 "pipboy" ([tiles #599](https://github.com/symmatree/tiles/issues/599)).

## The chosen filesystem: btrfs subvolumes, no overlay

Not a RO-base + overlay (that was the earlier call — superseded 2026-07-12). Instead, btrfs with
**granular per-subvolume ro/rw** — stronger protection than an overlay, with no ramdisk catching
writes and no custom initramfs:

All subvolumes `noatime`; the filesystem is `mkfs.btrfs -m single` (single metadata, no DUP — SD write-amplification). One btrfs FS → one UUID; the `subvol=` mount option differentiates the mounts (in `/etc/fstab`).

| Subvolume | Mount (option) | Contents / why |
|-----------|----------------|----------------|
| `@` | `/` (`compress=zstd`) | root. |
| `@usr` | `/usr` (**`ro`**) | the OS binaries/libraries — can't be written mid-cut, so can't corrupt. **Mount-option `ro`** (not the btrfs ro *property*), so `remount,rw` → apt → `remount,ro` works live for ansible maintenance, no reboot. |
| `@var` | `/var` (`compress=zstd`) | `journald`, Docker `data-root` (images survive reboot; `/var/lib/docker` is `chattr +C` / nodatacow — CoW-on-CoW footgun for overlay2), spool. |
| `@home` | `/home` (`compress=zstd`) | operator home (the checkout, interactive scratch that must survive a reboot). |
| `@data` | `/var/lib/coordinator` (`compress=zstd`) | config + captures — the precious data; **nests under `/var`** (mount after `@var`). Disarm takes an **RO snapshot** of this (#88). |
| `@snapshots` | `/.snapshots` | snapshot store (incl. the disarm RO-snapshots). |
| FAT | `/boot/firmware` (**`ro`**) | firmware; `remount,rw` for kernel/eeprom updates. |
| `/tmp`, `/run` | tmpfs | normal, small — the *only* ramdisk. |

Boot config is **standard, no custom initramfs hook**: `cmdline.txt` carries `rootfstype=btrfs rootflags=subvol=@`, and the stock Pi initramfs already has the btrfs module.

Why btrfs over the overlay: tmpfs-upper overlay costs RAM we can't spare on the 512 MB Zero 2 W pods;
disk-upper + conditional-reset needs a custom initramfs hook. Subvolumes give ro-where-it-matters +
CoW crash-consistency + checksums (detect SD FTL rot ext4 serves silently) + snapshots, with only
standard btrfs-root boot config. **Medium:** SD is fine *because* ro-`/usr` keeps write volume low;
escape hatch if capture volume grows is an f2fs data partition or a USB SSD (btrfs is unambiguously
good on the pipboy's NVMe).

## How it's built: mmdebstrap-in-CI (in `dotfiles-symm`)

A custom subvolume layout can't come from a stock flash, and the Foundation's declarative
`rpi-image-gen` can't express it either — a 2026-07-30 spike found it does a **single** btrfs root
(+ `-m single`) natively but has **no subvolume support** (its genimage step populates the top-level
subvolume; the generated fstab is hardcoded `defaults`). So the image build is **mmdebstrap-in-CI**
(#96): build an arm64 Debian rootfs, then an assembly step
(`dotfiles-symm/pi-image/assemble-btrfs.sh`) lays it into the subvolumes above and writes the
`fstab` + `cmdline`, then genimage packages the `.img`. Repeatable, per-role, in CI.

**De-risk status (2026-07-30): the assembly is verified; one gate remains.** The subvolume
assembly was built and tested against a real btrfs kernel — all six subvolumes assemble, mount per
the fstab, the split is exclusive, and `remount,rw /usr` works. The subvolume logic is only ~30
lines of straightforward bash over a plain rootfs copy (cheap — this is why subvolumes were kept
rather than dropping to a single-subvolume btrfs). The **one thing still unproven** is that a Pi
actually **boots** from a btrfs-subvolume root on the stock initramfs (mounts `subvol=@` and pivots)
— the gate, testable off flight-hardware by building a real `.img` and booting it under
`qemu-system-aarch64` (RPi firmware) or on a **spare** SD card (the ext4 card stays as instant
rollback). Everything else (the mmdebstrap rootfs, genimage packaging) is hardware-free.

(Gotcha found in the spike: btrfs `compress` is a **per-superblock** option, not per-mount, so
`@usr` inherits `@`'s `compress` regardless of its fstab line — harmless. `ro`/`noatime`/`nodev`
*are* true per-mount VFS flags, which is why the `ro`-`/usr` mount behaves as intended.)

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
| Repeatable btrfs image build, fleet-wide (**mmdebstrap-in-CI** in `dotfiles-symm`; `rpi-image-gen` can't do subvolumes) | [#96](https://github.com/symmatree/coordinator/issues/96) |
| Laptop-free shutdown: pHAT button + poweroff + safe-to-cut indicator | [#87](https://github.com/symmatree/coordinator/issues/87) |
| DISARM → stop still capture + fsync + `sync`/snapshot + physical done-signal | [#88](https://github.com/symmatree/coordinator/issues/88) |
| Power-loss-safe capture format (`.feat` #83 + stills #72) | [#89](https://github.com/symmatree/coordinator/issues/89) |
| Images present offline (pre-baked at build time / rw `@var`) | [#90](https://github.com/symmatree/coordinator/issues/90) |
| Stack auto-starts capturing on boot (systemd oneshot) | [#97](https://github.com/symmatree/coordinator/issues/97) |
| Persist the journal for post-crash forensics | [#100](https://github.com/symmatree/coordinator/issues/100) |
| Sibling / first btrfs device (PocketTerm) | tiles #599 |

**Near-term** (software, any RAM size, no reflash — lands on the current ext4 card *and* survives into
the btrfs image unchanged): #87 + #88 + #89 + #97. **Next reflash:** the btrfs image (#96) carrying the
layout above.

## Related

- [coordinator-network.md](coordinator-network.md) — the 2026-07-04 recovery this spun out of, and #41.
- [architecture.md](architecture.md) — runtime paths (`/var/lib/coordinator/*`) and the Top pHAT UC2
  control surface used by #87.
- #42 — disarmed bench capture (the concrete "the in-progress file is the irreplaceable artifact" case).
