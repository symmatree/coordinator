#!/usr/bin/env python3
"""Offline VIO pose-regeneration runner (coordinator #42).

For each ``*.feat`` fixture under ``$FLIGHTS_DIR`` (default ``/mnt/flights``), runs
``vins_fusion_offline`` to regenerate the estimator pose *deterministically* and writes
``<stem>.vinspose.csv`` next to the fixture, plus a ``<stem>.vinspose.polisher.json``
provenance sidecar. Fixtures whose sidecar is already up to date -- same estimator source
SHA and same fixture + config hashes -- are skipped.

This is the estimator-image analogue of the tiles flight-analysis ``runner.py``: the pose
regen needs the C++ binary (so it runs here, in the estimator image), while the human/agent
analysis notebook consumes this CSV in the jupyter image. Outputs are derived data on the
NAS alongside their source fixtures -- not source-controlled. Provenance uses RO-Crate-ish
field names without the JSON-LD context, matching flight-analysis (coordinator #40).

Determinism is a property of the binary (MULTIPLE_THREAD=0, single-threaded Ceres, no
wall-clock solver cap; see overlay/.../main_offline.cpp): the same source SHA + same inputs
must produce a byte-identical CSV. The sidecar records enough to prove that.

  vio-offline-runner                 # walk $FLIGHTS_DIR
  vio-offline-runner a.feat b.feat   # just these fixtures
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

FLIGHTS_DIR = Path(os.environ.get("FLIGHTS_DIR", "/mnt/flights"))
CONFIG = Path(os.environ.get("VINS_CONFIG", "/config/oak_d.yaml"))
BINARY = Path(os.environ.get("VINS_OFFLINE_BIN", "/opt/coordinator/bin/vins_fusion_offline"))

# Baked into the image at build (see Dockerfile). IMAGE_DIGEST is injected at run time by
# the k8s Job (same convention as flight-analysis runner.py). All default to "unknown" so
# the runner still works for a bare local invocation.
ESTIMATOR_SHA = os.environ.get("COORDINATOR_SHA", "unknown")  # source of overlay + runner
VINS_FUSION_SHA = os.environ.get("VINS_FUSION_SHA", "unknown")  # pinned upstream commit
IMAGE_DIGEST = os.environ.get("IMAGE_DIGEST", "unknown")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def is_fresh(sidecar: Path, feat_sha: str, cfg_sha: str) -> bool:
    """Up to date iff same estimator source SHA and same (fixture, config) input hashes.
    Keyed on source SHA rather than image digest: the binary is deterministic, so the
    same source rebuilt into a new image still yields the same pose -- no need to redo."""
    if not sidecar.exists():
        return False
    try:
        data = json.loads(sidecar.read_text())
        if data.get("instrument", {}).get("sha") != ESTIMATOR_SHA or ESTIMATOR_SHA == "unknown":
            return False
        have = {o["name"]: o["sha256"] for o in data.get("object", [])}
        return have.get(CONFIG.name) == cfg_sha and feat_sha in have.values()
    except Exception:
        return False


def process(feat_path: Path) -> None:
    stem = feat_path.stem.replace(" ", "-")
    out_csv = feat_path.parent / f"{stem}.vinspose.csv"
    sidecar = feat_path.parent / f"{stem}.vinspose.polisher.json"

    feat_sha = sha256_file(feat_path)
    cfg_sha = sha256_file(CONFIG)
    if is_fresh(sidecar, feat_sha, cfg_sha):
        print(f"  skip (fresh): {feat_path.name}", flush=True)
        return

    print(f"  run: {feat_path.name}", flush=True)
    start = datetime.now(timezone.utc).isoformat()

    # Binary writes the CSV itself (argv[3]); its own stdout is diagnostics, not the pose.
    subprocess.run([str(BINARY), str(CONFIG), str(feat_path), str(out_csv)], check=True)

    if not out_csv.exists():
        raise RuntimeError(f"{BINARY.name} produced no output CSV")
    # rows excluding the header -- a cheap domain fact (0 => estimator never converged).
    n_rows = max(0, sum(1 for _ in out_csv.open()) - 1)
    end = datetime.now(timezone.utc).isoformat()

    sidecar.write_text(json.dumps({
        "startTime": start,
        "endTime": end,
        "instrument": {
            "name": BINARY.name,
            "sha": ESTIMATOR_SHA,
            "image": IMAGE_DIGEST,
            "vins_fusion_sha": VINS_FUSION_SHA,
        },
        "object": [
            {"name": feat_path.name, "sha256": feat_sha},
            {"name": CONFIG.name, "sha256": cfg_sha},
        ],
        "result": [
            {"name": out_csv.name, "sha256": sha256_file(out_csv), "pose_rows": n_rows},
        ],
    }, indent=2) + "\n")
    print(f"  done: {out_csv.name} ({n_rows} poses)", flush=True)


def main() -> None:
    if not BINARY.exists():
        sys.exit(f"vio-offline-runner: binary not found: {BINARY}")
    if not CONFIG.exists():
        sys.exit(f"vio-offline-runner: config not found: {CONFIG} (set VINS_CONFIG)")

    args = [Path(a) for a in sys.argv[1:]]
    fixtures = args if args else sorted(FLIGHTS_DIR.rglob("*.feat"))
    if not fixtures:
        print(f"vio-offline-runner: no *.feat fixtures under {FLIGHTS_DIR}", flush=True)
        return

    print(f"estimator SHA: {ESTIMATOR_SHA}  vins: {VINS_FUSION_SHA}  config: {CONFIG}", flush=True)
    errors = 0
    for feat_path in fixtures:
        try:
            process(feat_path)
        except Exception as exc:  # per-fixture: one bad fixture must not sink the batch
            print(f"  ERROR {feat_path.name}: {exc}", file=sys.stderr, flush=True)
            errors += 1

    if errors:
        sys.exit(f"{errors} fixture(s) failed")


if __name__ == "__main__":
    main()
