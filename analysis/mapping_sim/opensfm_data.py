"""Read what OpenSfM wrote before reconstruction, and say what each feature truly looked at.

The readers take an OpenSfM directory (ODM's `opensfm/`) as ODM 3.6.2 leaves it:

- `features/<image>.features.npz` -- `points` is (x, y, scale, angle) in OpenSfM's normalised image
  coordinates (origin at the image centre, divided by the longer side);
- `matches/<image>_matches.pkl.gz` -- {other image: (k, 2) feature-index pairs} for every pair
  OpenSfM tried, after its geometric check; a pair it tried and rejected is present and empty;
- `tracks.csv` -- binary `OPENSFM_TRACKS_VERSION_v102`: per observation a little-endian u16 name
  length, u16 track-id length, the name, the track id, then u32 feature id, three f32 (x, y,
  scale), three u8 colour and one byte of segmentation.

`feature_truth` casts each feature's ray through the rendered camera into the scene, so every
feature has the surface point, the solid and the range it truly saw; `solid_labels` names the
solids the way a person would (ground, house, house star, tree ring at r m).
"""

from __future__ import annotations

import gzip
import pathlib
import pickle
import struct

import numpy as np

from lens import KannalaBrandt
from scene import Box, Scene, box_ring


def to_pixels(points: np.ndarray, width: int, height: int) -> np.ndarray:
    """OpenSfM normalised (x, y) -> render pixel coordinates, whose pixel centres are at i + 0.5."""
    size = max(width, height)
    return np.stack([points[:, 0] * size + width / 2.0, points[:, 1] * size + height / 2.0], axis=1)


def read_features(opensfm: pathlib.Path, image: str) -> np.ndarray:
    with np.load(opensfm / "features" / f"{image}.features.npz") as f:
        return f["points"]


def read_matches(opensfm: pathlib.Path, image: str) -> dict[str, np.ndarray]:
    with gzip.open(opensfm / "matches" / f"{image}_matches.pkl.gz", "rb") as f:
        return pickle.load(f)


TRACKS_HEADER = b"OPENSFM_TRACKS_VERSION_v102\n"


def read_tracks(path: pathlib.Path) -> tuple[list[str], dict[str, np.ndarray]]:
    """-> (image names, columns): `image` (index into the names), `track`, `feature`, `x`, `y`,
    `scale`, one row per observation in file order."""
    b = path.read_bytes()
    assert b.startswith(TRACKS_HEADER), b[:40]
    names: dict[bytes, int] = {}
    image, track, feature, xys = [], [], [], []
    i = len(TRACKS_HEADER)
    unpack_lens = struct.Struct("<HH").unpack_from
    unpack_obs = struct.Struct("<I3f").unpack_from
    while i < len(b):
        n, m = unpack_lens(b, i)
        i += 4
        name = b[i : i + n]
        i += n
        tid = b[i : i + m]
        i += m
        fid, x, y, s = unpack_obs(b, i)
        i += 20
        image.append(names.setdefault(name, len(names)))
        track.append(int(tid))
        feature.append(fid)
        xys.append((x, y, s))
    xys_a = np.array(xys, dtype=np.float32).reshape(-1, 3)
    cols = {
        "image": np.array(image, dtype=np.int32),
        "track": np.array(track, dtype=np.int64),
        "feature": np.array(feature, dtype=np.int32),
        "x": xys_a[:, 0],
        "y": xys_a[:, 1],
        "scale": xys_a[:, 2],
    }
    return [k.decode() for k in names], cols


def solid_labels(scene_cfg: dict) -> list[str]:
    """One label per solid in `Scene.from_config` order."""
    out = []
    for s in scene_cfg["solids"]:
        kind = s["kind"]
        if kind == "box_ring":
            ring = box_ring(**{k: v for k, v in s.items() if k != "kind"})
            out += [f"tree r={s['radius']:g}"] * len(ring)
        elif kind == "box" and s.get("decals"):
            out.append("house")
        else:
            out.append({"plane": "ground"}.get(kind, kind))
    return out


def feature_truth(scene: Scene, lens: KannalaBrandt, R_wc: np.ndarray, centre: np.ndarray, uv: np.ndarray) -> dict[str, np.ndarray]:
    """Cast the rays through pixel coordinates `uv` from one rendered camera.

    -> `point` (world, NaN where the ray meets nothing), `solid` (index, -1 for sky), `range` (m),
    `footprint` (m across one pixel at the hit, along the surface), `star` (on a Siemens star)."""
    rays, theta = lens.unproject(uv)
    d = rays @ R_wc.T
    o = np.broadcast_to(centre, d.shape)
    t, idx, n = scene.intersect(o, d)
    hit = idx >= 0
    point = np.full(d.shape, np.nan)
    point[hit] = centre + t[hit, None] * d[hit]
    rng = np.where(hit, t, np.nan)
    cos_inc = np.abs(np.einsum("ij,ij->i", n, d))
    footprint = np.where(hit, t * lens.pixel_angle(theta) / np.maximum(cos_inc, 0.05), np.nan)
    star = np.zeros(len(uv), dtype=bool)
    for i in np.unique(idx[hit]):
        s = scene.solids[i]
        if isinstance(s, Box) and s.decals:
            m = idx == i
            star[m] = ~np.isnan(s.decal(point[m], footprint[m]))
    return {"point": point, "solid": idx, "range": rng, "footprint": footprint, "star": star}


def project(lens: KannalaBrandt, R_wc: np.ndarray, centre: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """World points -> (pixel coordinates, in front of the camera)."""
    cam = (points - centre) @ R_wc
    return lens.project(cam), cam[:, 2] > 0
