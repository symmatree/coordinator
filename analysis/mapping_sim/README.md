# mapping_sim

Synthetic captures with a known answer, for working on the mapping pipeline without waiting on
flights. A config names a scene, a camera rig and a path; `render.py` produces what ODM takes as
input -- stills, a `geo.txt` of position priors -- plus `truth.json`, the exact camera poses,
intrinsics and scene geometry. After ODM (or OpenSfM directly) has run, `score.py` measures the
reconstruction against that truth.

A config is one experiment. Varying something -- a lens, a mount angle, prior accuracy -- is a
second config, not a feature of the tool.

| file | what |
|---|---|
| [`lens.py`](lens.py) | Kannala-Brandt fisheye, which is OpenCV's fisheye model and OpenSfM's `fisheye_opencv`, so recovered intrinsics compare directly with rendered ones. |
| [`scene.py`](scene.py) | ground plane, yawed boxes, vertical cylinders, intersected in closed form; non-repeating 3-D value-noise texture, band-limited to each pixel's footprint so nothing aliases. Unlit: no shadows or shading, so a surface looks the same from every view. |
| [`rig.py`](rig.py) | camera mounts as measured on the airframe (azimuth from forward, depression below horizontal, roll, position in FLU), and an ideal lawnmower path. |
| [`render.py`](render.py) | config -> `images/`, `geo.txt`, `truth.json`. `--scale` renders at a fraction of sensor resolution for quick local checks. |
| [`score.py`](score.py) | OpenSfM `reconstruction.json` + `truth.json` -> per-image position and rotation error, images not reconstructed, sparse-point distance to the true surfaces, recovered vs rendered intrinsics. Fits a similarity transform first and reports it. |
| [`configs/as-built.yaml`](configs/as-built.yaml) | the four campods with B0310 lenses, ideal capture. |
| [`test_mapping_sim.py`](test_mapping_sim.py) | synthetic tests, run by `test.sh`. |

What the render does not model: rolling shutter, motion blur, vibration, defocus, noise, exposure,
lens shading, lighting. Each is an addition to `render_image`, not a change to anything else.

## Running

Small checks run here (`--scale 0.125 --limit 4` takes seconds). A full-resolution render is
about 19 s and 430 MB per image single-threaded, and ODM needs far more, so real runs are the
`mapping-sim` WorkflowTemplate in `tiles` (`tanka/environments/mapping-sim/`): render, ODM to the
end of OpenSfM, score, all in one run directory on the NAS `datasets` share under `mapping-sim/`.
