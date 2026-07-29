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

## Canonical tree

```
<flight>/
  <fc-log>.bin                      # SOURCE: the FC dataflash log (one per flight). Read-only.
  NOTES.md                          # optional human narrative for this flight
  manifest.json                     # flight-level index: sessions + artifact paths + key facts
  polisher.json                     # flight-level provenance sidecar (flight-analysis run)

  captures/                         # SOURCE: OAK-D capture sessions (0..n; keyed by device + time)
    <MxId>/                         #   OAK-D serial / MxId (coordinator #32)
      <session>/                    #   ISO-basic UTC session stamp, e.g. 20260712T132731Z
        <MxId>_<session>.feat            # estimator input record (IMU + features)
        <MxId>_<session>.feat.json       # capture metadata sidecar
        <MxId>_<session>.vinspose.csv         # DERIVED pose (1:1 regen of THIS .feat)
        <MxId>_<session>.vinspose.polisher.json
        frames/                          # per-frame records for this session
          <MxId>_<seq>_<ts>.json           #   always: per-frame telemetry/feature record
          <MxId>_<seq>_<ts>.{png,jpg}      #   optional: stills (#72), when still-capture ran

  derived/                          # DERIVED: flight-level analysis products (combine sources)
    flight-analysis-<logstem>.ipynb # executed FC-log notebook + PDF (see Deviations: currently root)
    flight-analysis-<logstem>.pdf
    vio-quality.json                # VINS-vs-EKF score (per session; see Path resolution)
    vio-online-offline-comparison.json
    image-sharpness-vs-motion.json
    *.png / *.mp4                   # figures produced by the above
```

Two rules make this navigable:

1. **Sources are immutable and live at fixed places.** The `.bin` at the flight root; every
   OAK-D capture under `captures/<MxId>/<session>/`. A flight may have **zero or many** capture
   sessions (bench record, in-flight tee #78, multiple OAK-D power cycles).
2. **A derived product lives with what it derives from.** The per-session **pose** is a
   deterministic 1:1 regeneration of *one* `.feat`, so it sits **next to that `.feat`** at
   `<feat-path-without-ext>.vinspose.csv`. Everything else (analysis that *combines* sources --
   pose vs EKF, sharpness vs motion, the FC-log notebook) is flight-level and lives in
   `derived/`.

## What the automation produces

| Producer | Trigger | Reads | Writes (path) |
|----------|---------|-------|---------------|
| **vio-tracker** tee (#78) | in-flight, on the vehicle | live OAK-D | `captures/<MxId>/<session>/<MxId>_<session>.feat` (+ `.feat.json`, `frames/*.json`) |
| **oak-still-capture** (#72) | in-flight / bench | OAK-D RGB | `captures/<MxId>/<session>/frames/<MxId>_<seq>_<ts>.{png,jpg}` (+ `.json`) |
| `bin/vio-ipc-record` (bench) | manual bench | estimator sockets | a capture session (same `captures/...` shape) |
| **flight-analysis** CronJob (tiles) | nightly 04:00 UTC | `<fc-log>.bin` | `flight-analysis-<logstem>.{ipynb,pdf}`, `manifest.json`, `polisher.json` |
| **vio-offline** CronJob (tiles) | nightly 05:00 UTC | each `*.feat` | `<feat>.vinspose.csv` + `<feat>.vinspose.polisher.json` (next to the `.feat`) |
| `analysis/vio-quality.ipynb` | manual / after cron | pose CSV + `.bin` + `manifest.json` | `derived/vio-quality.json` (+ figures) |
| `analysis/vio-online-offline-comparison.ipynb` | manual | pose CSV + `VISP` from `.bin` | `derived/vio-online-offline-comparison.json` (+ figures) |
| `analysis/image-sharpness-vs-motion.ipynb` | manual | `frames/` stills + `.bin` | `derived/image-sharpness-vs-motion.json` (+ figures) |
| `analysis/tools/vio_param_sweep.py` | manual (bench, docker) | a `.feat` + `.bin` | `<feat>.<param>-sweep.json` (next to the `.feat`) |

**Provenance sidecars** (RO-Crate-ish field names, coordinator #40):
- `<feat>.vinspose.polisher.json` -- per pose: estimator source SHA, `vins_fusion` commit, fixture
  + config sha256, pose-row count. Freshness (skip-if-unchanged) is keyed on these.
- `polisher.json` -- per flight-analysis run: notebook SHA, `.bin` sha256, output shas.
- `manifest.json` -- the flight-analysis notebook's own self-description: input file, parameters,
  and key flight facts (duration, armed time, GPS status, vibe, EKF errors). Consumed by
  `vio-quality` as the FC-truth summary rather than re-deriving it.

## Path resolution -- how a consumer finds an artifact (no globbing)

The rule that started this doc: **do not `glob` for a file whose path is computable.** A
consumer that has a capture-session directory can construct every artifact path within it
directly -- the pose is `<session-dir>/<MxId>_<session>.vinspose.csv`, full stop. What a consumer
needs first is the **list of sessions**, and the `captures/` tree *is* that index: its levels are
defined (`captures/<MxId>/<session>/`), so enumerating the session directories is a structured,
predictable walk -- not a fragile `glob("*.vinspose.csv")` over the whole flight dir.

So the canonical resolution is:

1. list `captures/*/*/` -> the flight's capture sessions (0..n);
2. for each, the `.feat`, pose, and `frames/` are at computed names within that directory.

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

1. **`vio-quality` globs and asserts one pose per flight.** `pose_csvs = sorted(D.glob("*.vinspose.csv")); assert len==1`
   with `D = <fc-log>.bin`'s parent. This finds the flat 260705 pose but **zero** for 260712
   (pose is under `captures/`), and would break on any multi-session flight. *Fix:* enumerate the
   `captures/*/*/` sessions and read each pose at its computed path (per Path resolution), drop the
   "exactly one" assumption. (This is the trigger for this doc; the fix is structured resolution,
   **not** teaching the tool to `rglob` an inconsistent tree.)
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

## NAS housekeeping (ignore, don't process)

Synology sprinkles `@eaDir/` and `Thumbs.db` throughout the share (indexer/thumbnail artifacts).
Listing/processing tools should skip them; the cron runners already match on `*.bin` / `*.feat`
so they are unaffected.

## Related

- `analysis/README.md` -- who owns what across repos; the flight-analysis CronJob.
- `docs/vio-offline-replay.md` -- the offline pose-regen paths (Option C is the vio-offline CronJob).
- `tiles/tanka/environments/{flight-analysis,vio-offline}/` -- the runners that write these products.
