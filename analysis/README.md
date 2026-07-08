# analysis/

Flight-log and VIO analysis for the rekon10 platform.

| file | what |
|------|------|
| [`ardupilot_log.py`](ardupilot_log.py) | canonical `parse_log()` + ArduPilot message-type constants. Imported by the notebooks. |
| [`vio-input-alignment.ipynb`](vio-input-alignment.ipynb) | aligns a `vio-ipc-record` fixture to the FC `.bin` by motion cross-correlation, compares OAK-D IMU vs FC IMU, and asks whether the camera IMU sees motor vibration (coordinator [#42](https://github.com/symmatree/coordinator/issues/42)). Run manually; not part of the nightly cron. |
| [`vio_ekf_compare.py`](vio_ekf_compare.py) | vetted comparison lib: load VINS pose + FC EKF, time-align by angular-rate cross-correlation, Umeyama scale/rotation, ATE over the usable window. Imported by `vio-quality.ipynb`. |
| [`vio-quality.ipynb`](vio-quality.ipynb) | per-flight comparison of the **deterministically regenerated** VINS pose (`*.vinspose.csv` from `vio-offline-runner`, with a provenance sidecar) against FC EKF/GPS truth. Parameterized by `input_file` (the `.bin`); **consumes** flight-analysis's `manifest.json` for FC facts rather than re-deriving them; emits `vio-quality.json`. Runs *after* flight-analysis. |
| [`tools/vio_param_sweep.py`](tools/vio_param_sweep.py) | offline VINS hyperparameter sweep + scoring ([#63](https://github.com/symmatree/coordinator/issues/63)): regenerates pose per config value via `vins_fusion_offline` in the estimator image (hermetic -> reproducible), scores each vs FC EKF with `vio_ekf_compare`, writes a provenance-stamped `<fixture>.<param>-sweep.json` in the flight dir. Runs on the dev/bench box (needs docker + the analysis deps). |

## The flight-analysis notebook runs as a nightly CronJob — it lives in `tiles`, not here

**Don't go looking for a runner in this repo.** The initial per-flight analysis
(`Drones/rekon10/flight-analysis.ipynb`, whose source is in the **`fables`** repo) is executed by a
Kubernetes **CronJob** defined in the **`tiles`** repo:

> **`tiles/tanka/environments/flight-analysis/`** — [`main.jsonnet`](https://github.com/symmatree/tiles/blob/main/tanka/environments/flight-analysis/main.jsonnet), [`runner.py`](https://github.com/symmatree/tiles/blob/main/tanka/environments/flight-analysis/runner.py), [`README.md`](https://github.com/symmatree/tiles/blob/main/tanka/environments/flight-analysis/README.md) ← the authoritative doc.

Locally (this machine): `~/tiles/tanka/environments/flight-analysis/`.

### What it does

- **Schedule:** `0 4 * * *` (04:00 UTC daily), `concurrencyPolicy: Forbid`. Namespace `flight-analysis`.
- Clones `fables` fresh, then for **every `.bin` under the NAS `flights` share**
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

You *can* run the notebook by hand (e.g. the `fables` `.venv` has papermill):
`papermill fables/Drones/rekon10/flight-analysis.ipynb <out>.ipynb -p input_file <bin> -p debug false`.
But note it is **not** equivalent to the CronJob: it does **not** write `polisher.json`, and if you name
outputs differently (`flight-analysis.html` / the notebook's own `manifest.json`) they will sit *next to*
the cron's `flight-analysis-<stem>.{ipynb,pdf}` rather than replace them. Prefer the `kubectl` trigger so
provenance and naming stay consistent.

## Repo split (who owns what)

| lives in | what |
|----------|------|
| **`fables`** `Drones/rekon10/flight-analysis.ipynb` | the per-flight FC-log analysis notebook (source of truth) |
| **`tiles`** `tanka/environments/flight-analysis/` | the nightly CronJob that executes it (schedule, NFS PV, runner, provenance) |
| **`tiles`** `containers/datascience-notebook-ssh/` | the runner image (papermill, playwright/Chromium, LaTeX) |
| **`coordinator`** `analysis/` (here) | `parse_log()` helper + VIO-specific analysis (`vio-input-alignment.ipynb`) |

Rendered outputs are **derived data on the NAS**, not source-controlled (see
`fables/notebook-analysis/design-intent.md` and `coordinator/docs/calibration.md`).
