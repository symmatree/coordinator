# Power-loss-tolerant filesystem & capture (coordinator)

The coordinator is powered from the avionics 5 V rail, so **every normal power-down is a yank**
(disarm → unplug the XT60) and crashes / brownouts cut it mid-write. This is the pointer doc for how
we make the coordinator survive that without data loss. It is a **plan**, landing incrementally — not
yet fully built.

## The design lives in three places

- **The chosen pattern** — `facts/topics/power-unstable-pi.md` (private `facts` repo): a shared
  resilient-setup pattern for power-unstable, often-offline Pis, with an explicit **rekon10 Coordinator
  device profile**. Core idea = the container/pod model: **immutable read-only base + thin writable
  overlay + persistent local data**.
- **The tracking issue + coordinator-specific decisions** —
  [#41](https://github.com/symmatree/coordinator/issues/41). See its pinned design comment for what we
  simplified for the coordinator (SD not SSD; simple throwaway overlay, not the fancy conditional-reset
  variant; graceful sync-at-disarm as the primary mechanism).
- **The base recipe** — `symmatree/dotfiles-symm` (`ubuntu-zsh/` Ansible + the `rpi-console` profile):
  the **master host bootstrap** the whole Pi fleet converges onto; the RO base *is* this recipe. The
  first device onto the full pattern is the PocketTerm35 "pipboy", tracked in
  [tiles #599](https://github.com/symmatree/tiles/issues/599).

## The approach in one glance

| Layer | Backing | Coordinator contents |
|-------|---------|----------------------|
| **Base** (read-only, can't rot) | SD, robust FS | Pi OS Lite + Docker + **baked images** |
| **Overlay** (simple throwaway) | SD | OS RW churn, container writable layers, `journald`, `ipc/` sockets, `state/` — install-but-doesn't-persist-unless-in-the-ansible-recipe |
| **Persisted data** (survives reboot) | SD partition | `/var/lib/coordinator/{config,captures}` + the **operator home** (interactive scratch, the checkout) |
| **NAS** | when connected only | never a boot dependency (offline in flight) |

The primary safety mechanism is **graceful sync at disarm**, not a fancy filesystem: if every disarm
flushes + syncs, the only lossy events left are pulling power while armed or a brownout — where a
perfect mapping mission isn't expected anyway.

## Open work (issues)

- #87 — Top pHAT: laptop-free graceful shutdown/reboot + power button + readiness indicator.
- #88 — On DISARM: stop still capture, finalize + `fsync` capture writers, `sync` filesystem.
- #89 — Power-loss-safe on-disk format for the in-flight `.feat` capture (#78/#83) + stills (#72).
- #90 — Bake container images into the RO base with an easy update/rebake path.
- #41 — the RO-base + overlay + persisted-data layout itself (this doc's parent).

## Related

- [coordinator-network.md](coordinator-network.md) — the 2026-07-04 recovery this spun out of, and #41.
- [architecture.md](architecture.md) — runtime paths (`/var/lib/coordinator/*`) and the Top pHAT UC2
  control surface used by #87.
- #42 — disarmed bench capture (the concrete "the in-progress file is the irreplaceable artifact" case).
