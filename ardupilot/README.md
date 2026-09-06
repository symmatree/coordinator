# ArduPilot FC configuration -- rekon10

Flight-controller configuration for the **rekon10** airframe, concentrated in the
coordinator repo alongside the code that supports it. Board **TBS_LUCID_H7**,
firmware **ArduCopter 4.7.0**.

The fragments in `inputs/` are the source we maintain: small per-subsystem files we edit
and **apply to the FC**. `rekon10-methodi.param` is the last full dump off the FC -- the
ground truth `verify.py` checks the fragments against. Hardware/build narrative lives in
[`docs/rekon10/`](../docs/rekon10/README.md) (especially [`ardupilot.md`](../docs/rekon10/ardupilot.md)).

## Layout

| Path | What it is |
|------|------------|
| `rekon10-methodi.param` | **Ground truth** -- the last full parameter dump exported from the FC (Mission Planner *Write Params* -> *Save to File*). Do not hand-edit; replace wholesale with a fresh export. |
| `inputs/*.param` | **The maintained config** -- one commented `.param` per device/subsystem, named for what it holds, applied to the FC. Inspired by the ArduPilot Methodic Configurator. |
| `verify.py` | Round-trip check: every fragment value matches the export (see [Verifying](#verifying)). |
| `gen_defaults_sitl.py` | Dumps a firmware version's code-defaults from SITL -- used on a firmware bump to tell "the default changed" from "our value changed." |

The fragments are hand-curated: they carry the rationale and provenance behind the
build, which is the point. Grouping is by device, so things that interact sit together:
the F9P Rover Lite's serial, GNSS, and onboard compass in `gps-compass`; the Walksnail's
link, OSD, and power relay in `vtx-osd`.

Anything the FC produces and updates on its own -- learned hover throttle, barometer
ground pressure, auto-detected device IDs, boot/flight counters -- plus the per-unit
sensor calibrations you re-establish by running a cal, stays only in the ground-truth
export, never pinned here. Deliberate tuning (e.g. autotune results) *is* pinned.

## Verifying

`verify.py` is a round-trip check: every value in `inputs/` must match the value in the
export. The fragments are what we apply to the FC; the export is what came back off it, so
a mismatch means a fragment edit wasn't applied, or the FC drifted from the fragments.

It runs in CI (`.github/workflows/ardupilot-verify.yaml`) but only reports -- it is
**not** a required/blocking check. A config change is a PR that edits a fragment, which
you then apply to the FC and re-export; the fragment legitimately sits ahead of the
export (red) between merge and apply, so blocking on green would be backwards. Red just
means "not yet applied." Run it by hand while reconciling: `python3 ardupilot/verify.py`.

## Changing a parameter

1. Edit the value in the matching fragment, with a one-line rationale and
   `provenance: <sha> <date>`.
2. Apply that fragment to the FC (Mission Planner / Methodic Configurator load), re-export,
   and replace `rekon10-methodi.param` (LF).
3. `python3 ardupilot/verify.py` -- a mismatch means the fragment and the FC still disagree.

On a firmware bump, diff the new export against the old to find new/changed/renamed params
and fold the keepers into the fragments; `gen_defaults_sitl.py` dumps the new version's
code-defaults so you can separate a changed default from a changed value.
