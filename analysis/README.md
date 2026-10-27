# analysis/

Flight-log and VIO analysis for the rekon10 platform.

**Before trusting a number out of any of this:** which clock to use, how a sortie appears in a log,
and what each stream is and is not good for are in
[`docs/flight-data-interpretation.md`](../docs/flight-data-interpretation.md). Several entries there
exist because a notebook or a script here got it wrong first.

| file | what |
|------|------|
| [`flight-analysis.ipynb`](flight-analysis.ipynb) | the per-flight FC-log notebook: sortie timeline, modes and arm events, GPS, vibration, ESC, attitude and EKF summaries, and the agent-readable `manifest.json` the other notebooks consume. Parameterized by `input_file` (the `.bin`). Run nightly by the tiles CronJob (below). |
| [`time-sync.ipynb`](time-sync.ipynb) | every clock pairing on the vehicle for one flight, each against the figure `docs/time-base.md` advertises: FC `TimeUS` vs GPS (rate, residual, boot epoch), `CAM` PPS edges, each machine's `pps.log` (lock after boot, phase once locked), chrony per source from collectd, `timesync.jsonl` and `fc_time.jsonl` (the FC against the coordinator's PPS-disciplined clock, two routes), `frames.jsonl` and still sidecars (where in its second each exposure sat, pod against pod), and the closure -- a still's UTC label against GPS through the chain. Pairs each `.bin` to a coordinator session and FC boot by boot epoch rather than assuming one per session. Parameterized by `flight_dir`; emits `derived/time-sync.json` and figures. |
| [`ardupilot_log.py`](ardupilot_log.py) | canonical `parse_log()` + ArduPilot message-type constants. Imported by the notebooks. |
| [`feat_stream.py`](feat_stream.py) | decode a `.feat` estimator-input fixture: IMU/feature datagrams, **frame-loss accounting** against the device timestamp grid (E31, [#156](https://github.com/symmatree/coordinator/issues/156)), intrinsics recovered from the stream, stereo triangulation and depth stats (E32). CLI: `python3 analysis/feat_stream.py <fixture>`. |
| [`capture_align.py`](capture_align.py) | put capture data on the FC clock: detect and **repair the boot-time NTP wall-clock step**, fit coordinator-wall -> FC time from `VISP.RTimeUS` (no GPS arithmetic), and derive the airborne window physically (E30). |
| [`still_banding.py`](still_banding.py) | horizontal blur banding in rolling-shutter stills, OAK-D or campod. **The row-sharpness model is `row_sharpness`: the 95th percentile of local gradient magnitude across each row, blown-out pixels excluded -- not var-of-Laplacian or any row mean**, for reasons the module docstring gives. `frame_bands` fits it on one frame; `score_still` summarises a still for a session sweep. Also the older scene-cancelled two-frame method (E34). Reports the periodogram and rejects peaks at the search bound. CLI: `python3 analysis/still_banding.py <session-dir>`. |
| [`image-sharpness-vs-motion.ipynb`](image-sharpness-vs-motion.ipynb) | every still scored with `still_banding`, placed on the FC clock (campod by `exposure_wall_ns` through the FC's GPS->UTC fit; OAK-D by gyro cross-correlation) and set against angular rate, EKF speed, `VIBE` and each motor's rev rate, including whether the fitted band frequency follows any motor frame by frame. Parameterized by `flight_dir`, `captures_dir`, `fc_bin`, `campod`; emits `image-sharpness-vs-motion-<camera>.json` and figures. |
| [`local_pose.py`](local_pose.py) | Step A -- frame-to-frame relative pose direct from the tracker features, VINS out of the loop; rotation from bearings, translation from depth-scaled RANSAC, integration that never bridges a dropped-frame gap (E35). |
| [`vibration-spectrogram.ipynb`](vibration-spectrogram.ipynb) | vibration spectrum over time for **every accelerometer on the vehicle** -- FC raw `ACC` plus each campod ADXL345 pair -- on one shared FC time axis, with the per-motor rev and blade-pass lines overlaid from ESC RPM. Places every ADXL345 sample on the FC clock from its own batch stamp by the timestamp chain (boot -> clk -> the FC's GPS-dated time), reports the pod clock against its PPS edges, and runs every spectral estimate on hole-free runs interpolated onto a uniform grid -- the parts' emitted rates are only sanity-checked against nominal, never used for a frequency. Reports amplitude ratios between mounting points. Parameterized by the FC `.bin`, plus `captures_dir` and `campod` -- one pod per run, so a flight with two instrumented pods runs it twice; emits `vibration-spectrogram-<pod>.{png,json}`. |
| [`image-quality-experiments.md`](image-quality-experiments.md) | experiment ledger for **image quality** -- sharpness, banding, focus, blur -- graded in the form of the house-model doc, with dead theories kept struck through rather than deleted. Keeps the **OAK-D and the campod as separate instruments**: different sensor, readout, mount, and vibration field, so a result from one is not a result about the airframe. |
| [`pose_calibration.py`](pose_calibration.py) | per-axis scale and bias from held poses, and the rotation between two accelerometers on one body. Selects a **contiguous span per held orientation** and takes the window from its **centre**, because a first-satisfying-window search lands where the transient is still decaying. Reports how many poses are **independent** rather than how many there are, and says so when the fit has no spare degrees of freedom. CLI: `python3 analysis/pose_calibration.py <capture-dir> --json out.json`. |
| [`test_pose_calibration.py`](test_pose_calibration.py) | synthetic tests for it. Each pins a way the answer can be confidently wrong -- a cross-axis error vanishing into a diagonal fit, a window taken from a span's leading edge, a held-but-noisier pose being rejected, and a rotation residual that cannot see a sensor moved about its own axis. |
| [`ringdown.py`](ringdown.py) | bump-test analysis: find each tap, fit its decay per candidate band, and report **frequency and damping** per tap so the scatter across taps is the error bar. Refuses to fit a record that is not impulses-in-silence -- run on a steady fan it would otherwise return that fan's shaft harmonics as a table of modes. `compare_arms` pairs two nominally identical arms. CLI: `python3 analysis/ringdown.py <accel-*.jsonl> [<other-arm.jsonl>]`. |
| [`ringdown.ipynb`](ringdown.ipynb) | runs `ringdown.py` over one time window of one `campod-accel` capture and shows what it found -- impulses located, bands searched, modes that survived, three figures, and a `ringdown.json` summary beside the capture. Takes the window as a parameter because a capture normally holds startup, handling and pose changes too, all of which are impulses. Makes no argument about any particular airframe; that goes in the theory docs. |
| [`test_analysis_modules.py`](test_analysis_modules.py) | synthetic-fixture tests for the four modules above. No NAS, no hardware, no pytest: `python3 analysis/test_analysis_modules.py`. |
| [`test_ringdown.py`](test_ringdown.py) | synthetic tests for `ringdown.py`, same style: `python3 analysis/test_ringdown.py`. Each pins a way the fit can be confidently wrong -- damping biased low by fitting into the noise floor, a mode invented out of an empty band's filter transient, a steady source fitted as a modal table. |
| [`vio-input-alignment.ipynb`](vio-input-alignment.ipynb) | aligns a `vio-ipc-record` fixture to the FC `.bin` by motion cross-correlation, compares OAK-D IMU vs FC IMU, and asks whether the camera IMU sees motor vibration (coordinator [#42](https://github.com/symmatree/coordinator/issues/42)). Run manually; not part of the nightly cron. |
| [`vio_ekf_compare.py`](vio_ekf_compare.py) | vetted comparison lib: load VINS pose + FC EKF, time-align by angular-rate cross-correlation, Umeyama scale/rotation, ATE over the usable window. Imported by `vio-quality.ipynb`. |
| [`estimator-input-audit.ipynb`](estimator-input-audit.ipynb) | **input-side** audit for one flight, parameterized by the FC `.bin`: clock tie and airborne window, frame loss, feature supply and scene depth, local relative pose, still banding, contact sheet. Wires up the four modules above and emits `estimator-input-audit.json` alongside the input. Regenerates E30-E32, E34, E35. |
| [`vio-quality.ipynb`](vio-quality.ipynb) | per-flight comparison of the **deterministically regenerated** VINS pose (`*.vinspose.csv` from `vio-offline-runner`, with a provenance sidecar) against FC EKF/GPS truth. Parameterized by `input_file` (the `.bin`); **consumes** flight-analysis's `manifest.json` for FC facts rather than re-deriving them; emits `vio-quality.json`. Runs *after* flight-analysis. |
| [`tools/vio_param_sweep.py`](tools/vio_param_sweep.py) | offline VINS hyperparameter sweep + scoring ([#63](https://github.com/symmatree/coordinator/issues/63)): regenerates pose per config value via `vins_fusion_offline` in the estimator image (hermetic -> reproducible), scores each vs FC EKF with `vio_ekf_compare`, writes a provenance-stamped `<fixture>.<param>-sweep.json` in the flight dir. Runs on the dev/bench box (needs docker + the analysis deps). |

## The flight-analysis notebook runs as a nightly CronJob — it lives in `tiles`, not here

**Don't go looking for a runner in this repo.** The per-flight analysis
([`flight-analysis.ipynb`](flight-analysis.ipynb), source in **this** repo) is executed by a
Kubernetes **CronJob** defined in the **`tiles`** repo:

> **`tiles/tanka/environments/flight-analysis/`** — [`main.jsonnet`](https://github.com/symmatree/tiles/blob/main/tanka/environments/flight-analysis/main.jsonnet), [`runner.py`](https://github.com/symmatree/tiles/blob/main/tanka/environments/flight-analysis/runner.py), [`README.md`](https://github.com/symmatree/tiles/blob/main/tanka/environments/flight-analysis/README.md) ← the authoritative doc.

The runner clones this repo and reads the notebook at the path in its `NOTEBOOK_REL`
(default `analysis/flight-analysis.ipynb`; it was `docs/rekon10/flight-analysis.ipynb`
until 2026-10-27). `instrument.sha` in `polisher.json` is the notebook's git blob hash, so
provenance follows the file, not the directory. The runner and its notebook set are
flight-analysis's responsibility; they are checked into `tiles` because that is where the
cluster's environments live, not because another role owns them.

Locally (this machine): `~/tiles/tanka/environments/flight-analysis/`.

### What runs on every flight

| notebook | per | reads | writes |
|---|---|---|---|
| `flight-analysis.ipynb` | each `.bin` | the `.bin` | `flight-analysis-<stem>.{ipynb,pdf}`, `manifest.json`, `polisher.json` at the flight root |
| `time-sync.ipynb` | the flight, first | every `.bin`, every `captures/<node>/<session>/`, `ground/` | `derived/time-sync.{ipynb,pdf,json}`, figures |
| `vibration-spectrogram.ipynb` | each campod session with `accel-*.jsonl` | `captures/<pod>/<session>/`, the `.bin` | `derived/vibration-spectrogram-<pod>.{ipynb,pdf,json,png}` |
| `image-sharpness-vs-motion.ipynb` | each campod session with stills | `captures/<pod>/<session>/`, the `.bin` | `derived/image-sharpness-vs-motion-<pod>.{ipynb,pdf,json,png}` |

The pods' sessions arrive as `<node>_<session>.tar.zst` at the flight root (indexed by
`collected/<node>_<session>.json`); the runner extracts each into `captures/` -- the tar
paths are `<node>/<session>/...`, so the result is the canonical
`captures/<node>/<session>/` tree -- before running the per-pod notebooks.

Not per flight: the `vio-*` notebooks and `estimator-input-audit.ipynb` (VIO is not flown),
`ringdown.ipynb` (bench captures).

### What it does

- **Schedule:** `0 4 * * *` (04:00 UTC daily), `concurrencyPolicy: Forbid`. Namespace `flight-analysis`.
- Clones the notebook's repo fresh, then for **every `.bin` under the NAS `flights` share**
  (`raconteur.ad.local.symmatree.com:/volume2/datasets/flights`, mounted `/mnt/flights`) runs
  `papermill` on `flight-analysis.ipynb` with `-p input_file <bin>`, then `nbconvert --to webpdf`.
- Writes **alongside each `.bin`**:
  ```
  <flight-dir>/
    <logname>.bin                          # source (read-only)
    flight-analysis-<logname>.ipynb        # executed notebook  (spaces in stem -> dashes)
    flight-analysis-<logname>.pdf          # rendered PDF (webpdf / headless Chromium)
    polisher.json                          # per-dir provenance sidecar (RO-Crate-ish, #40)
  ```
- **Freshness / incremental:** a log is skipped if `polisher.json` already records both the current
  notebook git SHA (`instrument.sha`) and the `.bin`'s sha256 (`object[0].sha256`). New logs always run;
  a new notebook commit re-runs **all** logs on the next nightly. Force reprocess by deleting `polisher.json`.

### Trigger a run (don't reinvent it locally)

```bash
kubectl create job --from=cronjob/flight-analysis \
  flight-analysis-manual-$(date +%Y%m%d-%H%M%S) -n flight-analysis
kubectl logs -n flight-analysis -l job-name=<name> -c runner -f
```

New flights land on the NAS; the next nightly picks them up automatically. Use the manual trigger only
when you don't want to wait for 04:00 UTC.

### Local papermill is a fallback, not the cron

You *can* run the notebook by hand (any venv with papermill):
`papermill analysis/flight-analysis.ipynb <out>.ipynb -p input_file <bin> -p debug false`.
But note it is **not** equivalent to the CronJob: it does **not** write `polisher.json`, and if you name
outputs differently (`flight-analysis.html` / the notebook's own `manifest.json`) they will sit *next to*
the cron's `flight-analysis-<stem>.{ipynb,pdf}` rather than replace them. Prefer the `kubectl` trigger so
provenance and naming stay consistent.

## Repo split (who owns what)

| lives in | what |
|----------|------|
| **`coordinator`** `analysis/flight-analysis.ipynb` (here) | the per-flight FC-log analysis notebook (source of truth) |
| **`tiles`** `tanka/environments/flight-analysis/` | the nightly CronJob that executes it (schedule, NFS PV, runner, provenance) |
| **`tiles`** `containers/datascience-notebook-ssh/` | the runner image (papermill, playwright/Chromium, LaTeX) |
| **`coordinator`** `analysis/` (here) | `parse_log()` helper + VIO-specific analysis (`vio-input-alignment.ipynb`) |

Rendered outputs are **derived data on the NAS**, not source-controlled (see
`fables/notebook-analysis/design-intent.md` and `coordinator/docs/calibration.md`).
