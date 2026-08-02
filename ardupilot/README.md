# ArduPilot FC configuration -- rekon10

Flight-controller configuration for the **rekon10** airframe, concentrated in the
coordinator repo alongside the code that supports it. Board **TBS_LUCID_H7**,
firmware **ArduCopter 4.7.0**.

The FC export is authoritative for values; the fables `Drones/rekon10/*.md` docs
(especially `ardupilot.md`) carry the hardware/build narrative and the rationale.

## Layout

| Path | What it is |
|------|------------|
| `rekon10-methodi.param` | **Ground truth.** The last full parameter dump exported from the FC (Mission Planner *Write Params* -> *Save to File*). 1293 params, every one including untouched defaults. Do not hand-edit -- replace it wholesale with a fresh export. |
| `inputs/*.param` | **Decomposition.** The values we assert, split by subsystem, one commented `.param` per group, overrides only. The "methodical configurator" idea with a coarser, more readable grouping. Numeric prefixes give a sane apply order. |
| `overrides.csv` | Every param that differs from the ArduCopter 4.7.0 default (`key,export,default,kind,default_source`), 224 rows. `kind=config` (172) vs `calibration/identity` (52). Reference data + input to `verify.py`. |
| `verify.py` | Verifies the decomposition against the ground truth (see [Verifying](#verifying)). |
| `gen_defaults_sitl.py` | Boots ArduCopter SITL bare and dumps the running firmware's code-defaults -- the default source for any param a static source-scan can't resolve. Used when rebuilding `overrides.csv` for a new firmware. |

## What the fragments assert, and what they deliberately don't

The fragments pin the deliberate **configuration and tuning** for this airframe --
the 172 `config`-kind overrides (minus the two pure-runtime values `FORMAT_VERSION`
and `MIS_TOTAL`), including things produced by a procedure we want to lock in and
reproduce, like the autotune PID/rate results and the one-time-set radio and mode
config. Some params are pinned even though they currently equal the 4.7.0 default:
physical/wiring facts that won't move if ArduPilot's default does (`SERVO_BLH_POLES`,
which device is on which serial), and load-bearing fusion/notch values we rely on
holding (`EK3_ALT_M_NSE`, the `VISO_*_M_NSE` terms). `verify.py` tracks that allowlist
so nothing at-default is pinned by accident.

Fragments are grouped by **device/subsystem**, not by param-name prefix -- e.g. the
F9P Rover Lite's serial, GNSS, and onboard compass are together in `20-gps-compass`,
not scattered across a serial file and a compass file.

**Not asserted** -- the 52 `calibration/identity` values, which live only in the
ground-truth export. These are per-unit state that the FC or a calibration procedure
produces for *this specific airframe/sensor* and that you re-establish by running the
procedure or letting the FC learn, never by pasting a saved value:

- runtime-learned/estimated on the vehicle: learned hover throttle (`MOT_THST_HOVER`),
  barometer ground pressure (`BARO1_GND_PRESS`), gyro bias, `STAT_*` flight counters;
- sensor calibration + hardware identity: compass and accel/gyro offsets & scales,
  device IDs, board-level accel trim (`AHRS_TRIM`).

(This is *not* "machine-generated vs hand-set" -- the autotune PIDs are machine-generated
too, but they are a tuning decision we version and reflash. The line is per-unit
sensor/runtime state you must re-derive, vs a configuration/tuning choice for the airframe.)

Fragments, in apply order:

```
10-frame           20-gps-compass  40-ekf-vio    50-esc-motors-notch
60-rc-modes        62-radio-cal    65-vtx-osd    70-battery
72-failsafe-fence  80-tuning       90-logging-notify-misc
```

## Verifying

`verify.py` checks two properties and exits non-zero on either:

1. **Round-trip** -- every `KEY,VALUE` in `inputs/` equals the value in
   `rekon10-methodi.param`. This is what keeps the decomposition honest: the fragments
   can never silently drift from what was actually on the FC, and a future re-export
   that changed a value trips this until the fragment is reconciled.
2. **Coverage** -- every `config` override in `overrides.csv` is pinned by some
   fragment (or is on the two-item runtime allowlist). This is what stops a deliberate
   setting from being silently dropped from the decomposition.

**It runs in CI as a merge gate** (`.github/workflows/ardupilot-verify.yaml`, on any PR
touching `ardupilot/`) -- so a PR can't merge with a drifted or incomplete decomposition.
It is deliberately *not* a commit/pre-commit hook: a raw FC re-export won't round-trip
until the fragments are reconciled, and you must be able to commit and push that export
first. Run it by hand with `python3 ardupilot/verify.py` while reconciling.

## Changing or adding a parameter

1. Make the change on the FC, then re-export and replace `rekon10-methodi.param`
   (normalize to LF).
2. Put the value in the matching subsystem fragment under `inputs/`, with a one-line
   rationale and `provenance: <sha> <date>`. If it is a new override (differs from the
   4.7.0 default), add a row to `overrides.csv`; if it is per-unit calibration/identity,
   leave it in the export only.
3. Run `python3 ardupilot/verify.py` (also the PR merge gate). A round-trip failure means
   the fragment and the export disagree; a coverage failure means a new override is not
   pinned yet. You can commit/push intermediate states; the gate just has to be green to merge.

### Rebuilding `overrides.csv` for a new firmware

Run `python3 ardupilot/gen_defaults_sitl.py` (boots SITL built at the matching tag;
confirm the version it prints), diff the export against the dumped defaults, and update
`overrides.csv`. Board-gated params (serial protocols, `INS_FAST_SAMPLE`, `BATT_MONITOR`,
`NTF_LED_TYPES`, relay pins, ...) come from the board hwdef, not SITL.
