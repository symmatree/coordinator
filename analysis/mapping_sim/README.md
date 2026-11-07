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
| [`scene.py`](scene.py) | ground plane, yawed boxes (singly or as a `box_ring` round a circle), vertical cylinders, intersected in closed form; optional Siemens stars on a box's walls and roof; non-repeating 3-D value-noise texture, band-limited to each pixel's footprint so nothing aliases. Unlit: no shadows or shading, so a surface looks the same from every view. Each solid also gives its triangles for the rasteriser. |
| [`rig.py`](rig.py) | camera mounts as measured on the airframe (azimuth from forward, depression below horizontal, roll, position in FLU), and the vehicle path: an ideal lawnmower over a rectangle, a mission file's waypoints flown at constant speed, or poses sampled from a flight log (`flown`). |
| [`flown_path.py`](flown_path.py) | a flight log's Auto span -> the poses the FC estimated, sampled at the capture rate: `POS` position as metres about the mission's home (the scene origin) and height above home, `ATT` attitude, the EKF taken as exact. Writes a CSV whose header records the log's SHA-256, the window and why it ends where it does. |
| [`render.py`](render.py) | config -> `images/`, `geo.txt`, `truth.json`. `--scale` renders at a fraction of sensor resolution for quick local checks. Pixels come from `raster.py`. |
| [`raster.py`](raster.py) | the renderer: the scene's triangles drawn on the GPU with OpenGL through headless EGL, into five pinhole cube faces at the lens centre's pixel density, then a full-screen pass that resamples them through the Kannala-Brandt lens with trilinear, anisotropic filtering. The fragment shader evaluates `scene.py`'s texture and stars. Without a GPU, EGL falls back to Mesa's llvmpipe, which is how CI runs it. |
| [`manifest.py`](manifest.py) | writes `manifest.json` and `README.md` into each render: coordinator commit and any uncommitted changes to mapping_sim or the config, the command, the config (path, SHA-256, contents), package versions, host, times, and the SHA-256 of every file. A render is kept as an input and any number of downstream runs read it. |
| [`score.py`](score.py) | OpenSfM `reconstruction.json` + `truth.json` -> per-image position and rotation error, images not reconstructed, sparse-point distance to the true surfaces, recovered vs rendered intrinsics. Fits a similarity transform first and reports it. |
| [`opensfm_rig.py`](opensfm_rig.py) | a render -> OpenSfM's `rig_cameras.json` and `rig_assignments.json`: the mounts from the config the render recorded, one rig instance per capture. Placed in an OpenSfM project before it reconstructs, OpenSfM solves one pose per capture with the mounts shared, instead of one pose per image. |
| [`opensfm_data.py`](opensfm_data.py) | readers for what OpenSfM writes before reconstruction (features, matches, the binary `tracks.csv`) and `feature_truth`, which casts a feature's ray through the rendered camera to the surface point, solid, range and footprint it truly saw. Used by [`../mapping-sim-features.ipynb`](../mapping-sim-features.ipynb). |
| [`configs/as-built.yaml`](configs/as-built.yaml) | the four campods with B0310 lenses, ideal capture, one 45 m pass each way at 40 m (128 images). |
| [`configs/map-test-mission.yaml`](configs/map-test-mission.yaml) | the same rig flying [`ardupilot/map-test.waypoints`](../../ardupilot/map-test.waypoints), the mission flown on 2026-10-30 (`flights/rekon10/2026-10-30-auto`): waypoints 2-13 at 30 m, 3 m/s, 1 Hz, nose along each leg (1104 images), over a house in a yard ringed by trees: a 12 x 9 x 8 m box with a Siemens star on each wall and the roof, 10 m of open ground, then rings of 3 x 3 x 15 m boxes every 10 m out to 100 m. |
| [`configs/map-test-flown.yaml`](configs/map-test-flown.yaml) | the same rig and scene, flown along the FC's own pose estimates from that flight ([`configs/flown/2026-10-30-auto.csv`](configs/flown/2026-10-30-auto.csv)): each second of Auto, real heading, roll and pitch, cut before the thrust loss at 550.2 s that ended it (318 captures, 1272 images). |
| [`test_mapping_sim.py`](test_mapping_sim.py) | synthetic tests, run by `test.sh`. |

What the render does not model: rolling shutter, motion blur, vibration, defocus, noise, exposure,
lens shading, lighting. In the renderer the sensor effects come after the lens pass, on
the sensor's grid: rolling shutter and motion blur from several lens passes at sub-frame times,
defocus from a depth attachment, then noise and exposure.

## Running

Small checks run here (`--scale 0.125 --limit 4` takes seconds). At full resolution a render
takes about 0.3 s per image on lancer's Radeon 8060S, plus about 0.2 s of JPEG encoding on a CPU thread. ODM needs far more, so real runs are the
`mapping-sim` WorkflowTemplates in `tiles` (`tanka/environments/mapping-sim/`). On the NAS
`datasets` share, a render is kept in `mapping-sim/renders/<render>/` with its manifest, and each
processing of it (ODM to the end of OpenSfM, then score) is a separate `mapping-sim/runs/<run>/`
that reads the render's images read-only.
