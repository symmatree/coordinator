# ArduPilot FC configuration -- rekon10

Flight-controller configuration for the **rekon10** airframe, concentrated in the
coordinator repo alongside the code that supports it. Board **TBS_LUCID_H7**,
firmware **ArduCopter 4.7.0**.

> Hardware/build narrative (wiring, ESC, radio, tuning log, RTK path) lives in the
> fables `Drones/rekon10/*.md` docs -- especially `ardupilot.md`. This directory is
> the machine-readable config; those docs are the prose. Where a doc and this
> config disagree, **this config (the FC export) is authoritative for values** --
> see "Known discrepancies" below.

## Layout

| Path | What it is |
|------|------------|
| `rekon10-methodi.param` | **Ground truth.** The last full parameter dump exported from the FC (Mission Planner *Write Params* -> *Save to File*). 1293 params, every one including untouched defaults. Do not hand-edit -- replace it wholesale with a fresh export. |
| `inputs/*.param` | **Decomposition.** The values we *rely on*, split by subsystem, one commented `.param` per group, overrides only. This is the "methodical configurator" idea with a coarser, more readable grouping. |
| `overrides.csv` | Every param that differs from the ArduCopter 4.7.0 default (`key,export,default,kind,default_source`), 218 rows. `kind=config` (166) vs `calibration/identity` (52). Reference data + input to `verify.py`. |
| `verify.py` | Checks the decomposition against the ground truth (see below). |

### What the input files cover, and what they don't

`inputs/` pins the **166 `config`-kind overrides** (deliberate configuration) minus
2 runtime values (`FORMAT_VERSION`, `MIS_TOTAL`), plus a handful of load-bearing
params kept for context even though they sit at the default (frame identity, "2nd/3rd
notch off", the wired serial protocols). A few params that ArduPilot *learns* or that
are otherwise per-sample are documented in-file but deliberately left as defaults
rather than pinned (e.g. `VISO_VEL_M_NSE`, `EK3_ALT_M_NSE`, `SERVO_BLH_POLES`).

**Not decomposed** (they live only in `rekon10-methodi.param`): the **52
calibration/identity** values -- magnetometer and accel/gyro offsets/scales/IDs,
`AHRS_TRIM`, `BARO1_GND_PRESS`, learned `MOT_THST_HOVER`, `STAT_*` counters. These are
produced on-vehicle by calibration and by learning; blind-applying a stale snapshot
would be wrong, so they are intentionally excluded from the apply set.

Files, in apply order:

```
10-frame              20-serial            30-compass-gps       40-ekf-vio
50-esc-motors-notch   60-rc-modes-relay    62-radio-cal         70-battery
72-failsafe-fence     80-tuning            90-logging-notify-misc
```

## Verifying

```
python3 ardupilot/verify.py
```

1. **Round-trip** -- every `KEY,VALUE` in `inputs/` must match `rekon10-methodi.param`.
   This proves the decomposition faithfully reflects the FC, and will flag the moment
   a value drifts on a future re-export (re-export, then reconcile the input file).
2. **Coverage** -- every `config` override in `overrides.csv` must be pinned by some
   input file (or be on the small runtime allowlist), so nothing deliberate is missed.

Exit 0 = clean. Run it after any change to an input file or after dropping in a new
export.

### Refreshing after a new FC export

1. Export from the FC, replace `rekon10-methodi.param`.
2. Regenerate `overrides.csv` against 4.7.0 defaults (or the new firmware's) -- see the
   provenance note below; the extraction method is recorded there.
3. `python3 ardupilot/verify.py` and reconcile any round-trip mismatch (a value you
   changed on the FC) or coverage gap (a new deliberate override) into the input files.

## Provenance

Each override's rationale and *when* it arrived is drawn from the git history of the
param file (fables exported + pushed after each change), the fables/coordinator docs,
coordinator issues, and past session transcripts. Provenance appears inline in the
input files as `provenance: <sha> <date>` and `[source]` pointers. Defaults were
extracted from ArduPilot source at tag `Copter-4.7.0` (the hosted parameter metadata
carries no default field); calibration-vs-config split is in `overrides.csv`.

## Known discrepancies (carry these -- do not silently "fix")

The committed export is newer than several prose docs, so the docs lag in places.
The export is authoritative for values **except** the first item, which is a genuine
unresolved conflict:

1. **`RELAY4_DEFAULT` = 0 in the export, but `ardupilot.md` says it must be 1.**
   `0` is documented (and cleanly isolated) as breaking the ELRS link at boot. The
   doc even claims "the FC is at 1", which contradicts this export. Last touched
   `1->0` by `d41a75d 2026-07-26`. **Needs an operator decision + re-export** --
   confirm the live FC value; do not blind-apply. Flagged loudly in
   `60-rc-modes-relay.param`.
2. `INS_HNTCH_ENABLE` = **1** (export) vs `0` (ardupilot.md ESC section) -- notch is on.
3. `VISO_TYPE` = **2** (export) vs `0` "pre-VIO" (docs/ardupilot-vio.md).
4. `EK3_SRC_OPTIONS` = **8** SRC_PER_CORE (export) vs `1` (ardupilot.md).
5. `BATT_FS_LOW_ACT` = **1** Land (export) vs warn-only in ardupilot.md prose.
6. `COMPASS_ORIENT` = **6** flagged historical M100-era; re-confirm from outdoor cal.
7. Firmware is **4.7.0** (git `ea7afee 2026-07-27`), though the `ardupilot.md` header
   still says 4.6.3.

Items 2-5 are just stale narrative (the export is right); item 1 is a real conflict.

## Relationship to fables

This is now the home of the rekon10 FC config. The fables copy
(`Drones/rekon10/config/rekon10-methodi.param`) should be replaced with a pointer
here, and `ardupilot.md`'s links updated -- a separate fables-repo change (see the
PR that introduced this directory).
