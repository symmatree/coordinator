# Flight-data layout on the NAS (canonical)

The shared datasets NAS holds one directory per flight. Automation (the tiles CronJobs and the
analysis notebooks) reads sources and writes derived products into these directories, and
downstream steps read each other's outputs. For that to work **without globbing or per-flight
special-casing, every artifact must live at a path a consumer can compute** -- from the flight
directory plus, where relevant, a capture-session id from the predictable `captures/` structure.

This doc defines that canonical layout, records what each automation produces and where, and
**flags the places today's data does not conform** (a to-revisit list, not a claim of current
state). It is the reference `analysis/README.md`, `docs/vio-offline-replay.md`, and the tiles
`flight-analysis` / `vio-offline` runners should agree with.

> **Status:** the canonical layout below is the **target**. The cron runners already mostly
> follow it (see the table); the **Deviations** section lists where current data and some
> hand/bench steps diverge. Do not assume an arbitrary existing flight matches this yet.

## Share and platform

```
<nfs>:/volume2/datasets/flights/          -> mounted /mnt/flights in-cluster
  <platform>/                             e.g. rekon10/
    <flight>/                             one directory per flight (the unit of everything below)
```

Rendered outputs are **derived data on the NAS, not source-controlled** (see
`fables/notebook-analysis/design-intent.md`, `coordinator/docs/calibration.md`). The source of
truth for *code* is the repos; the NAS holds captured inputs + regenerable products.

### Flight-directory naming

`<YYMMDD>-<slug>` (e.g. `260712-crash`, `260728-sunny-baseline`) -- date first so it sorts
chronologically, then a short human slug. The directory name is the **stable human key** for a
flight; nothing downstream should parse meaning out of the FC log filename (see the `1980-` note
under Deviations).

## The invariant: capture is immutable, derived is separate

The load-bearing rule, and the reason for the source/derived split: **preserve "what the FC and
the cameras actually saw," byte-for-byte, forever** -- and keep everything we *recompute* out of
that space, even when it has the same shape as a capture. Concretely:

- **`captures/` and the FC `.bin` are append-only source of truth.** Automation reads them and
  **never writes into them.** A regenerated pose has the same shape as an online pose but is a
  *recomputation*; it must not land beside (or overwrite) the captured input.
- **Everything derived lives under `derived/`**, is freely overwritten as the derivation changes,
  and carries provenance (what produced it, from which inputs + config). Losing a derived file is
  cheap (rerun); losing a capture is not.
- Compat is **bounded**, not forever: prefer to **migrate or archive** legacy-format flights over
  teaching every tool to read every historical shape (see Archiving).

## Canonical tree

```
<flight>/
  <fc-log>.bin                      # SOURCE: the FC dataflash log (one per flight). Read-only.
  NOTES.md                          # optional human narrative for this flight
  manifest.json                     # flight-level index: sessions + artifact paths + key facts
  polisher.json                     # flight-level provenance sidecar (flight-analysis run)

  captures/                         # SOURCE (immutable): OAK-D capture sessions (0..n)
    <MxId>/                         #   OAK-D serial / MxId (coordinator #32)
      <session>/                    #   ISO-basic UTC session stamp, e.g. 20260712T132731Z
        <MxId>_<session>.feat            # estimator input record (IMU + features)
        <MxId>_<session>.feat.json       # capture metadata sidecar
        stills/                          # per-type media subdirs, each file's JSON beside it
          <MxId>_<seq>_<ts>.jpg          #   (#72 RGB stills)
          <MxId>_<seq>_<ts>.json
        disparity/                       # e.g. depth/disparity frames + their JSON
          ...
        features/                        # per-frame feature/telemetry records
          <MxId>_<seq>_<ts>.json
      # NO derived files here -- the regenerated pose does NOT live in captures/

  derived/                          # DERIVED (regenerable, provenance-stamped) -- everything we recompute
    pose/
      <MxId>_<session>.vinspose.csv       # offline regen of that session's .feat (deployed config)
      <MxId>_<session>.vinspose.polisher.json
    reconstructions/<label>/              # config-variant regens (sweeps, imu on/off, offline sims)
      <MxId>_<session>.vinspose.csv       #   each with its own provenance
    flight-analysis-<logstem>.ipynb       # executed FC-log notebook + PDF (see Deviations: currently root)
    flight-analysis-<logstem>.pdf
    vio-quality.json                      # VINS-vs-EKF score
    image-sharpness-vs-motion.json
    *.png / *.mp4                         # figures
```

Two rules make this navigable:

1. **Sources are immutable and live at fixed places.** The `.bin` at the flight root; every OAK-D
   capture under `captures/<MxId>/<session>/`, media split by type with each file's JSON beside it.
   A flight may have **zero or many** capture sessions (bench record, in-flight tee #78; multiple
   only if the OAK-D/tracker restarts mid-session -- rare/hypothetical today).
2. **Nothing derived lives in the capture area.** The regenerated pose is *derived* -- a
   recomputation of a session's `.feat` -- so it lives under `derived/pose/` (deployed config) or
   `derived/reconstructions/<label>/` (config variants), keyed by session, **never** beside the
   `.feat`. Same for every analysis product. The path is still computable (from the session id),
   so consumers resolve it directly.

## What the automation produces

| Producer | Trigger | Reads | Writes (path) |
|----------|---------|-------|---------------|
| **vio-tracker** tee (#78) | in-flight, on the vehicle | live OAK-D | `captures/<MxId>/<session>/<MxId>_<session>.feat` (+ `.feat.json`, `features/*.json`) |
| **oak-still-capture** (#72) | in-flight / bench | OAK-D RGB | `captures/<MxId>/<session>/stills/<MxId>_<seq>_<ts>.jpg` (+ `.json`) |
| `bin/vio-ipc-record` (bench) | manual bench | estimator sockets | a capture session (same `captures/...` shape) |
| **flight-analysis** CronJob (tiles) | nightly 04:00 UTC | `<fc-log>.bin` | `flight-analysis-<logstem>.{ipynb,pdf}`, `manifest.json`, `polisher.json` |
| **vio-offline** CronJob (tiles) | on-demand (manual `create job --from`; #139) | each `*.feat` | `derived/pose/<stem>.vinspose.csv` + sidecar (#139) |
| `analysis/vio-quality.ipynb` | manual / after cron | pose CSV + `.bin` + `manifest.json` | `derived/vio-quality.json` (+ figures) |
| `analysis/vio-online-offline-comparison.ipynb` | manual | pose CSV + `VISP` from `.bin` | `derived/vio-online-offline-comparison.json` (+ figures) |
| `analysis/image-sharpness-vs-motion.ipynb` | manual | `stills/` + `.bin` | `derived/image-sharpness-vs-motion.json` (+ figures) |
| `analysis/tools/vio_param_sweep.py` | manual (bench, docker) | a `.feat` + `.bin` | canonical: `derived/reconstructions/<param>/...`. **Current: `<feat>.<param>-sweep.json` next to the `.feat` -- deviation.** |

**Provenance sidecars** (RO-Crate-ish field names, coordinator #40):
- `derived/pose/<session>.vinspose.polisher.json` -- per pose: estimator source SHA, `vins_fusion`
  commit, fixture + config sha256, pose-row count. Freshness (skip-if-unchanged) is keyed on these.
- `polisher.json` -- per flight-analysis run: notebook SHA, `.bin` sha256, output shas.
- `manifest.json` -- the flight-analysis notebook's own self-description: input file, parameters,
  and key flight facts (duration, armed time, GPS status, vibe, EKF errors). Consumed by
  `vio-quality` as the FC-truth summary rather than re-deriving it.

## Path resolution -- how a consumer finds an artifact (no globbing)

The rule that started this doc: **do not `glob` for a file whose path is computable.** What a
consumer needs first is the **list of sessions**, and the `captures/` tree *is* that index: its
levels are defined (`captures/<MxId>/<session>/`), so enumerating the session directories is a
structured, predictable walk -- not a fragile `glob("*.vinspose.csv")` over the whole flight dir.

So the canonical resolution is:

1. list `captures/*/*/` -> the flight's capture sessions (0..n);
2. for a session, its **capture** artifacts are at computed names *inside* that session dir
   (`<session>.feat`, `stills/`, ...), and its **derived** pose is at a computed name *under
   `derived/`* (`derived/pose/<session>.vinspose.csv`, or `derived/reconstructions/<label>/...`
   for a variant). Source and derived are separate trees, both addressed by the session id.

A flight has **0..n** sessions, so the current `vio-quality` assumption -- `glob("*.vinspose.csv")`
in the flight root, assert exactly one -- is wrong two ways: it finds *zero* when the pose is under
`captures/` (260712), and it cannot represent a multi-session flight at all.

A flight-level `manifest.json` that lists the sessions + their artifact paths would make this a
lookup instead of a walk (and is the more robust target). **Today it does not** -- the
flight-analysis `manifest.json` describes the `.bin` analysis, not the captures -- so either the
`captures/` walk above is the mechanism, or that session index needs adding (see Deviations).

## Deviations in current data (to revisit)

Ordered roughly by how much they bite. None are urgent (all NAS data is regenerable), but each is
a place the layout is not yet canonical.

1. **Derived pose is written into the capture area, and consumers glob for it** (#139; highest
   value). *(a) Writer -- FIXED:* `vio-offline-runner` now writes `derived/pose/<stem>.vinspose.csv`,
   not beside the `.feat`. *(b) Reader -- pending:* `vio-quality` still `glob("*.vinspose.csv")`s
   the flight root and asserts exactly one -- which finds the flat 260705 pose but **zero** for
   260712, and can't represent config variants. Resolve `derived/pose/` per session (structured,
   **not** `rglob`), or drop the offline-CSV path entirely as the notebook goes onboard-`VISP`-direct
   (`vio_ekf_compare.compare_visp_to_ekf`). *(c) Migration -- pending:* move existing poses from
   old locations into `derived/pose/`; variants belong under `derived/reconstructions/<label>/`.
2. **Bench captures dump per-frame files flat in the flight root.** `260724-bench-atrest/` has
   575 `.json` + 481 `.png` + 92 `.jpg` + the `.feat` all at the flight root, instead of under
   `captures/<MxId>/<session>/frames/`. *Fix:* the bench recorder should write the same
   `captures/...` structure the in-flight tee does.
3. **Two pose namings.** `<stem>.vinspose.csv` (vio-offline-runner) vs `260728-baseline.visp_pose.csv`
   (260728, a different/hand path). *Fix:* standardize on `<stem>.vinspose.csv`; retire `visp_pose`.
4. **Derived analysis products land in two places.** `derived/` (260709, 260712) vs the flight
   root (260705's `vio-ekf-comparison.*`, `*-sweep.json`; 260728's `vio-quality-260728.json`).
   *Fix:* all flight-level analysis products under `derived/`.
5. **flight-analysis outputs are at the flight root, not `derived/`.** Consistent across flights,
   and arguably fine as the human browse artifact (open the dir, see the PDF) -- but it is the one
   automation whose products are not in `derived/`. *Decide:* keep at root by intent, or move to
   `derived/` for a single rule. (Touches the tiles `flight-analysis` runner.)
6. **`-<flightdate>` suffix on derived names is inconsistent.** `vio-quality-260712.json` vs a
   plain `vio-quality.json`. The parent dir already names the flight, so the suffix is redundant
   inside `derived/`; *pick one* (I'd drop the suffix and rely on the path).
7. **`.pdf.pdf` double-extension leftovers** from the pre-fix flight-analysis runner (fixed in
   tiles; old artifacts remain in `2026-06-29-vio`, `260613-vertical-bounce`, `260619-loiter-around`).
   *Fix:* delete the `.pdf.pdf` files (a nightly re-run won't, since the good `.pdf` already looks fresh).
8. **`1980-...` FC-log filenames with spaces.** When the FC boots without RTC/GPS time the log is
   epoch-dated (`1980-01-06 08-24-53.bin`), and flight-analysis names its outputs off that stem
   (`flight-analysis-1980-...`). Harmless given the flight *dir* is the human key, but ugly and
   space-containing. *Decide:* leave as-is (don't rename sources), or normalize log names on ingest.
9. **Human-notes filename varies:** `NOTES.md`, `flight-narrative.md`, `260619-flight-notes.md`.
   *Fix:* standardize on `NOTES.md`.
10. **Flight-dir naming is mixed:** `260712-crash` (canonical) alongside `2026-06-29-squirrelly`,
    `1-notch-loop-rate`, `prehistory`, `quicktune-260613`. *Decide:* adopt `<YYMMDD>-<slug>` going
    forward; renaming history is optional.
11. **`config.oak_d.yaml` snapshot** in `260712/derived/` -- a full copy of the config used. The
    pose sidecar already records the config sha256, so a full snapshot is belt-and-suspenders;
    *decide* whether to keep it as a convenience or rely on the sha + repo.
12. **No flight-level session index.** `manifest.json` describes the `.bin` analysis, not the
    captures, so there is no single place that lists a flight's sessions + artifact paths.
    Consumers currently must walk `captures/`. *Decide:* add a session index (extend
    flight-analysis's `manifest.json`, or a small indexer) to make resolution a lookup -- but this
    only pays off once **#2** lands and *every* capture (bench included) is actually under
    `captures/`; until then the flat 260705/260724 `.feat`s have no session dir to enumerate.

## Archiving and format compat

Compat is **bounded on purpose.** Rather than teach every tool to read every historical capture
shape forever, prefer to **migrate** a flight to the canonical layout, or **archive** it out of the
active tree (e.g. `flights/<platform>/_archive/<flight>/`) so the automation only ever sees
canonical flights. Early flights are the ones most likely to be in odd formats -- and, notably, the
ones we were most precious about because they were *firsts* (first autotune, first VIO). That
caution has largely served its purpose: with a working regime, revertible params, and a post-flight
analysis model that confirms behavior, **most flights are cheap to re-fly** -- and soon literally
re-flyable from waypoints instead of stick inputs. So the bias should be **make new data cheap, not
hoard old data**: keep captures we genuinely can't reproduce, archive the rest, and don't let
backward-compat debt accumulate in the active tree. (The place offline replay earns its keep is the
opposite direction -- not re-flying to get data we could recapture, but **silicon leverage**:
hyperparameter sweeps that would otherwise need one flight per grid point, where you tune to a
predicted optimum and fly only a few points to confirm the local slope.)

## NAS housekeeping (ignore, don't process)

Synology sprinkles `@eaDir/` and `Thumbs.db` throughout the share (indexer/thumbnail artifacts).
Listing/processing tools should skip them; the cron runners already match on `*.bin` / `*.feat`
so they are unaffected.

## Related

- `analysis/README.md` -- who owns what across repos; the flight-analysis CronJob.
- `docs/vio-offline-replay.md` -- the offline pose-regen paths (Option C is the vio-offline CronJob).
- `tiles/tanka/environments/{flight-analysis,vio-offline}/` -- the runners that write these products.
