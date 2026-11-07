"""Render a synthetic capture: config in, an ODM project's inputs plus the truth out.

    python3 analysis/mapping_sim/render.py <config.yaml> <out_dir> [--scale 0.25] [--limit N]

Writes into `out_dir`:

- `images/<capture>_<camera>.jpg` -- one still per camera per capture, with EXIF make, model,
  focal length and capture time so ODM groups and orders them. The model is the camera's name,
  so OpenSfM fits each camera's intrinsics separately, as for four physical lenses;
- `geo.txt` -- each image's true optical-centre position, in UTM with a fixed false origin, at the
  accuracy the config gives (ODM's per-image GPS prior). Orientation is left `nan`;
- `truth.json` -- every image's true camera rotation and centre in the local scene frame, each
  lens's intrinsics in OpenSfM's convention, the UTM offset, and the config itself, which is
  everything `score.py` needs to judge a reconstruction;
- `manifest.json` and `README.md` -- the commit, command, config and file hashes that produced
  the directory (see `manifest.py`).

Pixels come from `raster.py`: the scene's triangles drawn with OpenGL, then resampled through the
lens.

`--scale` renders at a fraction of the sensor resolution (intrinsics scaled to match) for quick
local checks; a real run uses 1. `--jobs` is how many threads encode and write the JPEGs while
the GPU draws one image at a time.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import pathlib
import sys

import numpy as np
import yaml
from PIL import Image

import manifest
from rig import Camera, rig_from_config, trajectory_from_config
from raster import Rasterizer
from scene import Scene

# A false origin in UTM zone 17N, roughly where the house is, so ODM sees plausible coordinates.
UTM_EPSG = 32617
UTM_ORIGIN = (585000.0, 4487000.0, 300.0)
EPOCH = dt.datetime(2026, 1, 1, 12, 0, 0)


def _exif(cam: Camera, when: dt.datetime, pixel_um: float) -> Image.Exif:
    ex = Image.Exif()
    ex[0x010F] = "SimArducam"  # Make
    ex[0x0110] = cam.name  # Model
    ifd = ex.get_ifd(0x8769)
    ifd[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
    ifd[0x9291] = f"{when.microsecond // 1000:03d}"  # SubSecTimeOriginal
    focal_mm = cam.lens.fx * pixel_um / 1000.0
    ifd[0x920A] = round(focal_mm, 4)  # FocalLength
    # 35 mm equivalent by the long side: OpenSfM takes focal = f35 / 36 as its starting point.
    ifd[0xA405] = int(round(36.0 * cam.lens.fx / max(cam.lens.width, cam.lens.height)))
    return ex


def _save(img: np.ndarray, path: pathlib.Path, quality: int, exif: Image.Exif) -> None:
    Image.fromarray(np.round(img * 255).astype(np.uint8), mode="L").convert("RGB").save(path, quality=quality, exif=exif)


def _draw(scene: Scene, work: list, jobs: int) -> None:
    rasterizer = Rasterizer(scene)
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        pending = []
        for cam, pose, path, quality, exif in work:
            img = rasterizer.render(cam.lens, pose.R_wb @ cam.R_bc, pose.p_wb + pose.R_wb @ cam.t_bc)
            pending.append(pool.submit(_save, img, path, quality, exif))
            # Keep the queue of images waiting to be encoded, 48 MB each at full resolution, short.
            while len(pending) > 2 * jobs:
                pending.pop(0).result()
        for f in pending:
            f.result()


def render(cfg: dict, out: pathlib.Path, scale: float = 1.0, limit: int | None = None, jobs: int = 1) -> dict:
    scene = Scene.from_config(cfg["scene"])
    cams = rig_from_config(cfg["rig"])
    if scale != 1.0:
        for c in cams:
            c.lens = c.lens.scaled(scale)
    poses = trajectory_from_config(cfg["trajectory"])
    if limit is not None:
        poses = poses[:limit]
    pixel_um = float(cfg["rig"].get("pixel_um", 1.4)) / scale
    acc_h, acc_v = (float(x) for x in cfg.get("priors", {}).get("accuracy_m", (0.01, 0.01)))
    quality = int(cfg.get("jpeg_quality", 95))

    (out / "images").mkdir(parents=True, exist_ok=True)
    geo = [f"EPSG:{UTM_EPSG}"]
    shots = {}
    work = []
    for k, pose in enumerate(poses):
        when = EPOCH + dt.timedelta(seconds=pose.time_s)
        for cam in cams:
            name = f"{k:05d}_{cam.name}.jpg"
            work.append((cam, pose, out / "images" / name, quality, _exif(cam, when, pixel_um)))
            R_wc = pose.R_wb @ cam.R_bc
            C = pose.p_wb + pose.R_wb @ cam.t_bc
            e, n, u = C + UTM_ORIGIN
            geo.append(f"{name} {e:.4f} {n:.4f} {u:.4f} nan nan nan {acc_h} {acc_v}")
            shots[name] = {"camera": cam.name, "time_s": pose.time_s, "R_wc": R_wc.tolist(), "center": C.tolist()}

    _draw(scene, work, jobs)

    (out / "geo.txt").write_text("\n".join(geo) + "\n")
    truth = {
        "frame": "local ENU metres; UTM = local + utm_origin",
        "utm_epsg": UTM_EPSG,
        "utm_origin": list(UTM_ORIGIN),
        "scale": scale,
        "cameras": {c.name: c.lens.opensfm() for c in cams},
        "shots": shots,
        "config": cfg,
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=1))
    return truth


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", type=pathlib.Path)
    ap.add_argument("out", type=pathlib.Path)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--limit", type=int, default=None, help="render only the first N captures")
    ap.add_argument("--jobs", type=int, default=1, help="images rendered in parallel")
    a = ap.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    started = dt.datetime.now(dt.timezone.utc)
    truth = render(cfg, a.out, a.scale, a.limit, a.jobs)
    manifest.write(a.out, a.config, cfg, truth, sys.argv, started, dt.datetime.now(dt.timezone.utc))
    print(f"{len(truth['shots'])} images -> {a.out}")


if __name__ == "__main__":
    main()
