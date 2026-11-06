"""OpenSfM's rig model for a render: the cameras as one rigid assembly, triggered together.

    python3 analysis/mapping_sim/opensfm_rig.py <render_dir> <opensfm_dir>

Writes `rig_cameras.json` (each camera's mount, from the config the render recorded in its
manifest) and `rig_assignments.json` (one rig instance per capture, its images from `geo.txt`)
into an OpenSfM project before it reconstructs. With them, OpenSfM solves one pose per capture
rather than one per image, and the mounts are shared by every capture.

The rig frame is the body frame (FLU). A rig camera's pose maps rig-frame points into the camera
frame, as an OpenSfM shot pose maps world points: rotation `R_bc^T` as a rotation vector,
translation `-R_bc^T t_bc`. OpenSfM holds the mounts at these values while a reconstruction has 10
captures or fewer, and past that adjusts them in bundle adjustment against them as priors
(`rig_translation_sd`, `rig_rotation_sd`); `reconstruction.json` records what it ended with.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import defaultdict

from scipy.spatial.transform import Rotation

from rig import rig_from_config


def rig_files(render: pathlib.Path) -> tuple[dict, dict]:
    cfg = json.loads((render / "manifest.json").read_text())["config"]["contents"]
    cameras = {}
    for cam in rig_from_config(cfg["rig"]):
        R_cb = cam.R_bc.T
        cameras[cam.name] = {
            "rotation": Rotation.from_matrix(R_cb).as_rotvec().tolist(),
            "translation": (-R_cb @ cam.t_bc).tolist(),
        }
    # Images are <capture>_<camera>.jpg, the capture zero-padded (render.py).
    assignments = defaultdict(list)
    for line in (render / "geo.txt").read_text().splitlines()[1:]:
        name = line.split()[0]
        capture, camera = pathlib.Path(name).stem.split("_", 1)
        assert camera in cameras, f"{name}: no camera {camera!r} in the render's rig"
        assignments[capture].append([name, camera])
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
