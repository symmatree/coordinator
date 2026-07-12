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

| Subvolume | Mount | Contents / why |
|-----------|-------|----------------|
| `@usr` (+ `/boot`) | **read-only** | the OS binaries/libraries — can't be written mid-cut, so can't corrupt. Remounts rw *live* for ansible maintenance (`remount,rw` → apt → `remount,ro`, no reboot). |
| `@var` | rw | `journald`, Docker `data-root` (images survive reboot), spool — CoW crash-consistent + snapshottable. |
| `@home` | rw | operator home (the checkout, interactive scratch that must survive a reboot). |
| `@data` (`/var/lib/coordinator`) | rw | config + captures — the precious data. Disarm takes an **RO snapshot** of this (#88). |
| `/tmp`, `/run` | tmpfs | normal, small — the *only* ramdisk. |

Why btrfs over the overlay: tmpfs-upper overlay costs RAM we can't spare on the 512 MB Zero 2 W pods;
disk-upper + conditional-reset needs a custom initramfs hook. Subvolumes give ro-where-it-matters +
CoW crash-consistency + checksums (detect SD FTL rot ext4 serves silently) + snapshots, with only
standard btrfs-root boot config. **Medium:** SD is fine *because* ro-`/usr` keeps write volume low;
escape hatch if capture volume grows is an f2fs data partition or a USB SSD (btrfs is unambiguously
good on the pipboy's NVMe).

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
  format is the resilient pattern (#89), and it kept appending healthily well after the image streams
  stopped, which proves the coordinator FS was fine through the window.
- **The 0-byte image files are not a coordinator-FS artifact, but their cause is unknown.** The image
  streams stopped ~20 s before the cut; why is TBD — the camera may have lost power / been disconnected
  before the Pi (power-loss ordering unknown), or a USB / X_LINK / pipeline dropout. The X_LINK we saw
  was on the bench post-crash with the camera reassembled from pieces — **not** in-flight evidence; the
  camera appears fine. Needs investigation, not attribution.
- **Crash survival rides on the on-disk format (#89), not the disarm-flush (#88)** — no disarm fires on
  an uncontrolled loss. Remaining #89 work: verify torn-tail handling on `.feat`, make stills atomic (a
  latent risk this event did **not** demonstrate). New gap: persist the journal
  ([#100](https://github.com/symmatree/coordinator/issues/100)).

## Scope → issues

| Aspect | Issue |
|--------|-------|
| FS/power-loss architecture (umbrella + decision) | [#41](https://github.com/symmatree/coordinator/issues/41) |
| Repeatable btrfs image build, fleet-wide (rpi-image-gen) | [#96](https://github.com/symmatree/coordinator/issues/96) |
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
