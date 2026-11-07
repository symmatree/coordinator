"""Render a synthetic capture: config in, an ODM project's inputs plus the truth out.

    python3 analysis/mapping_sim/render.py <config.yaml> <out_dir> [--scale 0.25] [--limit N]

Writes into `out_dir`:

- `images/<capture>_<camera>.jpg` -- one still per camera per capture, with EXIF make, model,
  focal length and capture time so ODM groups and orders them. The model is the camera's name,
  so OpenSfM fits each camera's intrinsics separately, as for four physical lenses;
- `geo.txt` -- each image's true optical-centre position as WGS84 longitude, latitude and height,
  at the accuracy the config gives (ODM's per-image GPS prior). Orientation is left `nan`;
- `truth.json` -- every image's true camera rotation and centre in the local scene frame (east,
  north, up about `ORIGIN_LLA`), each lens's intrinsics in OpenSfM's convention, the origin, and
  the config itself, which is
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

# The scene's origin on WGS84: the map-test mission's home (ardupilot/map-test.waypoints), with its
# altitude taken as ellipsoidal height. The scene frame is the local tangent plane there: x east,
# y north, z up.
ORIGIN_LLA = (40.5325098, -80.0420550, 357.2)
WGS84_A, WGS84_F = 6378137.0, 1 / 298.257223563
EPOCH = dt.datetime(2026, 1, 1, 12, 0, 0)


def _ecef(lat_deg: float, lon_deg: float, h: float) -> np.ndarray:
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    e2 = WGS84_F * (2 - WGS84_F)
    n = WGS84_A / np.sqrt(1 - e2 * np.sin(lat) ** 2)
    return np.array([(n + h) * np.cos(lat) * np.cos(lon), (n + h) * np.cos(lat) * np.sin(lon), (n * (1 - e2) + h) * np.sin(lat)])


def local_to_lla(p: np.ndarray) -> tuple[float, float, float]:
    """A scene point (east, north, up metres about ORIGIN_LLA) -> latitude, longitude (degrees) and
    ellipsoidal height (m), exactly: through ECEF, not a flat-earth approximation."""
    lat0, lon0 = np.radians(ORIGIN_LLA[0]), np.radians(ORIGIN_LLA[1])
    R_ecef_enu = np.array(
        [
            [-np.sin(lon0), -np.sin(lat0) * np.cos(lon0), np.cos(lat0) * np.cos(lon0)],
            [np.cos(lon0), -np.sin(lat0) * np.sin(lon0), np.cos(lat0) * np.sin(lon0)],
            [0.0, np.cos(lat0), np.sin(lat0)],
        ]
    )
    x, y, z = _ecef(*ORIGIN_LLA) + R_ecef_enu @ p
    e2 = WGS84_F * (2 - WGS84_F)
    lon = np.arctan2(y, x)
    r = np.hypot(x, y)
    lat = np.arctan2(z, r * (1 - e2))
    for _ in range(10):  # converges to well under a micrometre in a few rounds
        n = WGS84_A / np.sqrt(1 - e2 * np.sin(lat) ** 2)
        h = r / np.cos(lat) - n
        lat = np.arctan2(z, r * (1 - e2 * n / (n + h)))
    return float(np.degrees(lat)), float(np.degrees(lon)), float(h)


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
    geo = ["EPSG:4326"]
    shots = {}
    work = []
    for k, pose in enumerate(poses):
        when = EPOCH + dt.timedelta(seconds=pose.time_s)
        for cam in cams:
            name = f"{k:05d}_{cam.name}.jpg"
            work.append((cam, pose, out / "images" / name, quality, _exif(cam, when, pixel_um)))
            R_wc = pose.R_wb @ cam.R_bc
            C = pose.p_wb + pose.R_wb @ cam.t_bc
            lat, lon, h = local_to_lla(C)
            # EPSG:4326 in geo.txt is longitude, latitude, height. 1e-9 degrees is under 0.1 mm.
            geo.append(f"{name} {lon:.10f} {lat:.10f} {h:.4f} nan nan nan {acc_h} {acc_v}")
            shots[name] = {"camera": cam.name, "time_s": pose.time_s, "R_wc": R_wc.tolist(), "center": C.tolist()}

    _draw(scene, work, jobs)

    (out / "geo.txt").write_text("\n".join(geo) + "\n")
    truth = {
        "frame": "local ENU metres, the tangent plane at origin_lla",
        "origin_lla": list(ORIGIN_LLA),
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
