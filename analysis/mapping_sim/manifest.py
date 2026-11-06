"""What produced a rendered dataset, written beside it so it can be kept and reprocessed.

A render directory is an input to any number of downstream runs (ODM with different settings,
OpenSfM directly, a rig model or not), so it carries its own record rather than relying on
whichever run made it:

- `manifest.json` -- the coordinator commit and whether the mapping_sim code or the config had
  uncommitted changes, the command line, the config's repo path, contents and SHA-256, package
  versions, host, start and finish times, and the SHA-256 of every file written;
- `README.md` -- the same for a person: what the rig, path and scene are, how to reproduce it,
  and what each file is.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import platform
import subprocess
import sys

import numpy as np
import PIL
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CODE_DIR = pathlib.Path(__file__).resolve().parent


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], check=True, capture_output=True, text=True).stdout.strip()


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _repo_path(path: pathlib.Path) -> str:
    p = path.resolve()
    return str(p.relative_to(REPO_ROOT)) if p.is_relative_to(REPO_ROOT) else str(p)


def build(out: pathlib.Path, config_path: pathlib.Path, cfg: dict, truth: dict, argv: list[str], started: dt.datetime, finished: dt.datetime) -> dict:
    config_rel = _repo_path(config_path)
    dirty = _git("status", "--porcelain", "--", str(CODE_DIR), str(config_path.resolve()))
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name not in ("manifest.json", "README.md"))
    images = [n for n in truth["shots"]]
    first = truth["cameras"][next(iter(truth["cameras"]))]
    return {
        "kind": "mapping_sim render",
        "software": {
            "repo": _git("config", "--get", "remote.origin.url"),
            "commit": _git("rev-parse", "HEAD"),
            "uncommitted_changes": dirty.splitlines(),
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pillow": PIL.__version__,
            "pyyaml": yaml.__version__,
            "host": platform.node(),
        },
        "command": argv,
        "config": {"path": config_rel, "sha256": _sha256(config_path), "contents": cfg},
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "render_s": round((finished - started).total_seconds(), 1),
        "images": {"count": len(images), "width": first["width"], "height": first["height"], "scale": truth["scale"]},
        "files": {str(p.relative_to(out)): _sha256(p) for p in files},
    }


def readme(m: dict) -> str:
    cfg = m["config"]["contents"]
    sw = m["software"]
    rig = cfg["rig"]
    cams = ", ".join(f"{c['name']} (az {c['azimuth_deg']}, down {c['depression_deg']})" for c in rig["cameras"])
    lenses = "; ".join(f"{k}: {v['width']} x {v['height']}, focal {v['focal_px']} px, k {v['k']}" for k, v in rig["lenses"].items())
    traj = cfg["trajectory"]
    traj_desc = ", ".join(f"{k} {v}" for k, v in traj.items())
    solids = cfg["scene"]["solids"]
    kinds: dict[str, int] = {}
    for s in solids:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    dirty = "none" if not sw["uncommitted_changes"] else "; ".join(sw["uncommitted_changes"])
    return f"""# mapping_sim render

A synthetic capture with a known answer, from coordinator `analysis/mapping_sim`. This directory
is an input: downstream runs (ODM, OpenSfM, other settings) read it and write elsewhere.

| | |
|---|---|
| config | `{m['config']['path']}` (sha256 `{m['config']['sha256'][:12]}`; full contents in `manifest.json` and `truth.json`) |
| software | {sw['repo']} at `{sw['commit']}`; uncommitted changes in mapping_sim or the config: {dirty} |
| command | `{' '.join(m['command'])}` |
| rendered | {m['started_utc']} to {m['finished_utc']} ({m['render_s']} s) on {sw['host']} |
| images | {m['images']['count']} at {m['images']['width']} x {m['images']['height']} (scale {m['images']['scale']}) |
| cameras | {cams} |
| lenses | {lenses} |
| path | {traj_desc} |
| priors | accuracy {cfg.get('priors', {}).get('accuracy_m')} m (horizontal, vertical) |
| scene | {', '.join(f'{n} {k}' for k, n in kinds.items())} config entries |

To reproduce: check out `{sw['commit']}` and run the command above from the repo root.
`manifest.json` has the SHA-256 of every file, to check a copy against.

## Files

- `images/` -- one JPEG per camera per capture, `<capture>_<camera>.jpg`, with EXIF make, model,
  focal length and capture time;
- `geo.txt` -- ODM's per-image position priors, UTM with a fixed false origin;
- `truth.json` -- true camera poses, intrinsics in OpenSfM's convention, the UTM offset and the
  config: what `score.py` scores against;
- `manifest.json` -- the record above, machine-readable.
"""


def write(out: pathlib.Path, config_path: pathlib.Path, cfg: dict, truth: dict, argv: list[str], started: dt.datetime, finished: dt.datetime) -> dict:
    m = build(out, config_path, cfg, truth, argv, started, finished)
    (out / "manifest.json").write_text(json.dumps(m, indent=1))
    (out / "README.md").write_text(readme(m))
    return m
