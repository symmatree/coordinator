"""OpenSfM's rig model for a render: the cameras as one rigid assembly, triggered together.

    python3 analysis/mapping_sim/opensfm_rig.py <render_dir> <opensfm_dir>

Writes `rig_cameras.json` (each camera's mount, from the config the render recorded in its
manifest) and `rig_assignments.json` (one rig instance per capture, its images from `geo.txt`)
into an OpenSfM project before it reconstructs. With them, OpenSfM solves one pose per capture
rather than one per image, and the mounts are shared by every capture.

The rig frame is the body frame (FLU) moved to the mean of the camera centres: OpenSfM's GPS prior
on a rig instance is the mean of its images' positions, applied to the instance's origin, so the
origin is put where that prior says it is. A rig camera's pose maps rig-frame points into the
camera frame, as an OpenSfM shot pose maps world points: rotation `R_bc^T` as a rotation vector,
translation `-R_bc^T (t_bc - mean t_bc)`. OpenSfM holds the mounts at these values while a reconstruction has 10
captures or fewer, and past that adjusts them in bundle adjustment against them as priors
(`rig_translation_sd`, `rig_rotation_sd`); `reconstruction.json` records what it ended with.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import defaultdict

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from rig import rig_from_config


def opensfm_camera_id(image: pathlib.Path) -> str:
    """The camera model id OpenSfM gives an image (`exif.camera_id_` at ODM 3.6.2's OpenSfM), from
    its EXIF make, model, size and 35 mm focal length, as ODM is run with `--camera-lens
    fisheye_opencv`."""
    with Image.open(image) as im:
        ex = im.getexif()
        f35 = ex.get_ifd(0x8769)[0xA405]
        width, height = im.size
    make, model = ex[0x010F], ex[0x0110].replace(ex[0x010F], "")
    parts = ["v2", make.strip(), model.strip(), str(width), str(height), "fisheye_opencv", str(float(f35 / 36))[:6]]
    return " ".join(parts).lower()


def rig_files(render: pathlib.Path) -> tuple[dict, dict]:
    cfg = json.loads((render / "manifest.json").read_text())["config"]["contents"]
    # Images are <capture>_<camera>.jpg, the capture zero-padded (render.py).
    by_camera = defaultdict(list)
    for line in (render / "geo.txt").read_text().splitlines()[1:]:
        name = line.split()[0]
        capture, camera = pathlib.Path(name).stem.split("_", 1)
        by_camera[camera].append((capture, name))
    # Each rig camera is named after its camera model. OpenSfM's compute_statistics fails on a rig
    # camera named anything else (rig_statistics tests a stale loop variable), and ODM with it.
    ids = {c: opensfm_camera_id(render / "images" / shots[0][1]) for c, shots in by_camera.items()}
    assert len(set(ids.values())) == len(ids), f"cameras share an OpenSfM camera model: {ids}"
    cams = rig_from_config(cfg["rig"])
    centroid = np.mean([cam.t_bc for cam in cams], axis=0)
    cameras = {}
    for cam in cams:
        R_cb = cam.R_bc.T
        cameras[ids[cam.name]] = {
            "rotation": Rotation.from_matrix(R_cb).as_rotvec().tolist(),
            "translation": (-R_cb @ (cam.t_bc - centroid)).tolist(),
        }
    assignments = defaultdict(list)
    for camera, shots in by_camera.items():
        for capture, name in shots:
            assignments[capture].append([name, ids[camera]])
    return cameras, dict(assignments)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("render", type=pathlib.Path)
    ap.add_argument("opensfm", type=pathlib.Path)
    args = ap.parse_args()
    cameras, assignments = rig_files(args.render)
    args.opensfm.mkdir(parents=True, exist_ok=True)
    (args.opensfm / "rig_cameras.json").write_text(json.dumps(cameras, indent=1))
    (args.opensfm / "rig_assignments.json").write_text(json.dumps(assignments, indent=1))
    print(f"{len(cameras)} rig cameras, {len(assignments)} rig instances -> {args.opensfm}")


if __name__ == "__main__":
    main()
