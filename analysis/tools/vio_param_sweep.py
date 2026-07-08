#!/usr/bin/env python3
"""Offline VINS parameter sweep + scoring (coordinator #63).

Answer "does hyperparameter X matter?" from RECORDED data, no flight per value.
For each value in a grid, regenerate the estimator pose deterministically by running
``vins_fusion_offline`` in the estimator image (hermetic -> reproducible), then score
it against the FC EKF/GPS trajectory with ``vio_ekf_compare.compare`` (ATE + recovered
Umeyama scale). Emits a provenance-stamped result JSON next to the fixture.

The docker step is the robustness: same image + same config + same fixture -> byte-identical
pose (verified: MULTIPLE_THREAD=0, single-threaded Ceres, SOLVER_TIME pinned huge in
main_offline.cpp, so the iteration cap is the live knob). So a sweep is a set of independent
hermetic runs; no bespoke harness needed beyond this driver.

Prereqs: the estimator image built (``containers/vio-estimator/build-local.sh`` or pull CI
image) and the analysis deps (pandas/numpy/pymavlink) -- i.e. run this on the dev/bench box,
not inside a container. Config is OpenCV YAML (``%YAML:1.0``); we substitute scalar keys by
line, not with a YAML parser (PyYAML can't read ``!!opencv-matrix``).

Examples:
  # deployed stereo-only config, sweep the Ceres iteration cap on one flight
  vio_param_sweep.py \\
    --fixture /mnt/s/flights/rekon10/260705-vio-logged/wave-20260705-114708.feat \\
    --fc-bin  "/mnt/s/flights/rekon10/260705-vio-logged/1980-01-06 10-14-19.bin" \\
    --param max_num_iterations --grid 2,4,6,8,12,16,24,32

  # the imu:1 diagnostic (shows the runaway config scores on a degenerate window)
  vio_param_sweep.py ... --set imu=1 --param max_num_iterations --grid 8
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # analysis/ for vio_ekf_compare

DEFAULT_CONFIG = _HERE.parents[1] / "host/ansible/roles/coordinator/files/oak_d.yaml"
DEFAULT_IMAGE = "coordinator-vio-estimator:local"
BIN = "/opt/coordinator/bin/vins_fusion_offline"
METRIC_KEYS = ("ate_rmse_m", "ate_median_m", "ate_max_m", "umeyama_scale",
               "usable_track_s", "align_lag_s", "n_samples")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def set_scalar(text, key, value):
    """Replace a top-level scalar `key: ...` line (comments dropped on that line)."""
    pat = re.compile(rf"^{re.escape(key)}\s*:.*$", re.MULTILINE)
    new, n = pat.subn(f"{key}: {value}", text)
    if n == 0:
        raise SystemExit(f"config has no scalar key '{key}' to set")
    return new


def regen_pose(image, config_text, fixture, out_csv):
    """Run vins_fusion_offline in the image; write out_csv. Deterministic per (image,config,fixture)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "cfg.yaml").write_text(config_text)
        # fixture + outputs share one mounted dir so the container writes where we can read.
        work = out_csv.parent
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{work}:/work",
            "-v", f"{td}/cfg.yaml:/config/cfg.yaml:ro",
            "-v", f"{fixture}:/work/{fixture.name}:ro",
            "--entrypoint", "sh", image, "-c",
            f"mkdir -p /tmp/vins && {BIN} /config/cfg.yaml /work/{fixture.name} /work/{out_csv.name} 2>/dev/null",
        ]
        subprocess.run(cmd, check=True)
    if not out_csv.exists():
        raise RuntimeError(f"{BIN} produced no CSV for {out_csv.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", required=True, type=Path)
    ap.add_argument("--fc-bin", required=True, type=Path, help="FC .bin (EKF/GPS ground truth)")
    ap.add_argument("--config", default=DEFAULT_CONFIG, type=Path)
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--param", default="max_num_iterations", help="config scalar key to sweep")
    ap.add_argument("--grid", required=True, help="comma-separated values for --param")
    ap.add_argument("--set", dest="overrides", action="append", default=[],
                    metavar="KEY=VAL", help="extra base config override (repeatable)")
    ap.add_argument("--out", type=Path, help="result JSON (default: <fixture>.<param>-sweep.json)")
    args = ap.parse_args()

    # Fail fast on missing inputs -- otherwise a bad path wastes the whole docker sweep
    # before the provenance hash trips over it.
    for p in (args.fixture, args.fc_bin, args.config):
        if not p.exists():
            ap.error(f"not found: {p}")

    # Import here so --help works without the analysis deps installed.
    import vio_ekf_compare as V
    from functools import lru_cache
    V.load_fc = lru_cache(maxsize=2)(V.load_fc)  # FC .bin is big; parse once across the grid

    base = args.config.read_text()
    for ov in args.overrides:
        k, v = ov.split("=", 1)
        base = set_scalar(base, k, v)

    grid = [g.strip() for g in args.grid.split(",") if g.strip()]
    out_json = args.out or args.fixture.with_suffix(f".{args.param}-sweep.json")
    rows = []
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        for val in grid:
            cfg = set_scalar(base, args.param, val)
            csv = work / f"{args.param}_{val}.csv"
            regen_pose(args.image, cfg, args.fixture.resolve(), csv)
            try:
                m = V.compare(str(csv), str(args.fc_bin), run_name=f"{args.param}={val}", make_plot=False)
                row = {"value": val, **{k: m.get(k) for k in METRIC_KEYS}}
            except Exception as exc:  # scoring can fail (bad alignment) -- record, keep going
                row = {"value": val, "error": str(exc)}
            rows.append(row)
            print(f"  {args.param}={val}: " +
                  (row["error"] if "error" in row
                   else f"ATE_rmse={row['ate_rmse_m']} scale={row['umeyama_scale']} "
                        f"track_s={row['usable_track_s']} n={row['n_samples']}"))

    result = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "param": args.param, "grid": grid, "overrides": args.overrides,
        "instrument": {"image": args.image, "binary": Path(BIN).name},
        "object": [
            {"name": args.fixture.name, "sha256": sha256_file(args.fixture)},
            {"name": args.config.name, "sha256": sha256_file(args.config)},
            {"name": args.fc_bin.name, "sha256_head": sha256_file(args.fc_bin)[:16]},
        ],
        "results": rows,
    }
    out_json.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {out_json}")


if __name__ == "__main__":
    main()
