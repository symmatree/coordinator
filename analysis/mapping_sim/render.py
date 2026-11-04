"""Render a synthetic capture: config in, an ODM project's inputs plus the truth out.

    python3 analysis/mapping_sim/render.py <config.yaml> <out_dir> [--scale 0.25] [--limit N]

Writes into `out_dir`:

- `images/<capture>_<camera>.jpg` -- one still per camera per capture, with EXIF make, model,
  focal length and capture time so ODM groups and orders them;
- `geo.txt` -- each image's true optical-centre position, in UTM with a fixed false origin, at the
  accuracy the config gives (ODM's per-image GPS prior). Orientation is left `nan`;
- `truth.json` -- every image's true camera rotation and centre in the local scene frame, each
  lens's intrinsics in OpenSfM's convention, the UTM offset, and the config itself, which is
  everything `score.py` needs to judge a reconstruction.

`--scale` renders at a fraction of the sensor resolution (intrinsics scaled to match) for quick
local checks; a real run uses 1.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib

import numpy as np
import yaml
from PIL import Image

from rig import Camera, Pose, rig_from_config, trajectory_from_config
from scene import Scene

# A false origin in UTM zone 17N, roughly where the house is, so ODM sees plausible coordinates.
UTM_EPSG = 32617
UTM_ORIGIN = (585000.0, 4487000.0, 300.0)
EPOCH = dt.datetime(2026, 1, 1, 12, 0, 0)


def render_image(scene: Scene, cam: Camera, pose: Pose, rows_per_chunk: int = 128) -> np.ndarray:
    lens = cam.lens
    R_wc = pose.R_wb @ cam.R_bc
    origin = pose.p_wb + pose.R_wb @ cam.t_bc
    img = np.empty((lens.height, lens.width), dtype=np.float32)
    cols = np.arange(lens.width) + 0.5
    for r0 in range(0, lens.height, rows_per_chunk):
        rows = np.arange(r0, min(r0 + rows_per_chunk, lens.height)) + 0.5
        uu, vv = np.meshgrid(cols, rows)
        rays, theta = lens.unproject(np.stack([uu.ravel(), vv.ravel()], axis=1))
        d = rays @ R_wc.T
        o = np.broadcast_to(origin, d.shape)
        img[r0 : r0 + len(rows)] = scene.shade(o, d, lens.pixel_angle(theta)).reshape(len(rows), -1)
    return img


def _exif(cam: Camera, when: dt.datetime, pixel_um: float) -> Image.Exif:
    ex = Image.Exif()
    ex[0x010F] = "SimArducam"  # Make
    ex[0x0110] = f"B0310-sim-{cam.lens.width}x{cam.lens.height}"  # Model
    ifd = ex.get_ifd(0x8769)
    ifd[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
    ifd[0x9291] = f"{when.microsecond // 1000:03d}"  # SubSecTimeOriginal
    focal_mm = cam.lens.fx * pixel_um / 1000.0
    ifd[0x920A] = round(focal_mm, 4)  # FocalLength
    # 35 mm equivalent by the long side: OpenSfM takes focal = f35 / 36 as its starting point.
    ifd[0xA405] = int(round(36.0 * cam.lens.fx / max(cam.lens.width, cam.lens.height)))
    return ex


def render(cfg: dict, out: pathlib.Path, scale: float = 1.0, limit: int | None = None) -> dict:
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
    for k, pose in enumerate(poses):
        when = EPOCH + dt.timedelta(seconds=pose.time_s)
        for cam in cams:
            name = f"{k:05d}_{cam.name}.jpg"
            img = render_image(scene, cam, pose)
            Image.fromarray(np.round(img * 255).astype(np.uint8), mode="L").convert("RGB").save(
                out / "images" / name, quality=quality, exif=_exif(cam, when, pixel_um)
            )
            R_wc = pose.R_wb @ cam.R_bc
            C = pose.p_wb + pose.R_wb @ cam.t_bc
            e, n, u = C + UTM_ORIGIN
            geo.append(f"{name} {e:.4f} {n:.4f} {u:.4f} nan nan nan {acc_h} {acc_v}")
            shots[name] = {"camera": cam.name, "time_s": pose.time_s, "R_wc": R_wc.tolist(), "center": C.tolist()}

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
    a = ap.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    truth = render(cfg, a.out, a.scale, a.limit)
    print(f"{len(truth['shots'])} images -> {a.out}")


if __name__ == "__main__":
    main()
