# ArduPilot FC configuration -- rekon10

Flight-controller configuration for the **rekon10** airframe, concentrated in the
coordinator repo alongside the code that supports it. Board **TBS_LUCID_H7**,
firmware **ArduCopter 4.7.0**.

The FC export is authoritative for values; the fables `Drones/rekon10/*.md` docs
(especially `ardupilot.md`) carry the hardware/build narrative.

## Layout

| Path | What it is |
|------|------------|
| `rekon10-methodi.param` | **Ground truth** -- the last full parameter dump exported from the FC (Mission Planner *Write Params* -> *Save to File*). Do not hand-edit; replace wholesale with a fresh export. |
| `inputs/*.param` | **Decomposition** -- the config we maintain, split by device/subsystem, one commented `.param` per group. Inspired by the ArduPilot Methodic Configurator; apply in filename (numeric-prefix) order. |
| `overrides.csv` | Classification of every non-default param (config vs the FC's own calibration/identity state); input to `verify.py`'s coverage check. |
| `verify.py` | Verifies the decomposition against the ground truth (see [Verifying](#verifying)). |
| `gen_defaults_sitl.py` | Dumps the running firmware's code-defaults from SITL, for reclassifying against a new firmware. |

The fragments are hand-curated: they carry the rationale and provenance behind the
build, which is the point -- not a generated projection. Grouping is by device, so
things that interact sit together: the F9P Rover Lite's serial, GNSS, and onboard
compass in `20-gps-compass`; the Walksnail's link, OSD, and power relay in `65-vtx-osd`.

Anything the FC produces and updates on its own -- learned hover throttle, barometer
ground pressure, auto-detected device IDs, boot/flight counters -- plus the per-unit
sensor calibrations you re-establish by running a cal, stays only in the ground-truth
export, never pinned here. Deliberate tuning (e.g. autotune results) *is* pinned.

## Verifying

`verify.py` checks two things and exits non-zero on either:

1. **Round-trip** -- every value in `inputs/` equals the value in the export. Keeps the
   fragments from silently drifting from what was on the FC.
2. **Coverage** -- every deliberate override is pinned by some fragment. Stops a config
   change from being left out of the decomposition.

It runs in CI as a **merge gate** (`.github/workflows/ardupilot-verify.yaml`), not a
commit hook -- a raw re-export won't round-trip until you reconcile the fragments, and
you must be able to commit and push it first. Run it by hand while reconciling:
`python3 ardupilot/verify.py`.

## Changing or adding a parameter

1. Make the change on the FC, re-export, and replace `rekon10-methodi.param` (LF).
2. Put the value in the matching fragment with a one-line rationale and
   `provenance: <sha> <date>`. Per-unit calibration/identity stays in the export only.
3. `python3 ardupilot/verify.py`. A round-trip failure means the fragment disagrees with
   the export; a coverage failure means a new override isn't pinned yet. Intermediate
   states can be committed; the gate only has to be green to merge.

For a firmware bump, regenerate the defaults baseline with `gen_defaults_sitl.py` and
reclassify; board-gated params (serial protocols, `BATT_MONITOR`, relay pins, ...) come
from the board hwdef, not SITL.
