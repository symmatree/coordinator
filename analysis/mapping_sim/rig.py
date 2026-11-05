"""Camera rig on the vehicle, and the vehicle's path through the scene.

Body frame is FLU (x forward, y left, z up), origin at the vehicle's reference point. A camera mount
is given the way one would measure it on the airframe:

- `azimuth_deg`: horizontal look direction, clockwise from forward seen from above (45 = front-right);
- `depression_deg`: optical axis below horizontal (90 = straight down);
- `roll_deg`: rotation of the sensor about the optical axis; 0 puts the sensor's long (width)
  axis horizontal, image right to the right of the look direction;
- `position_m`: the camera's optical centre in the body frame.

Camera frame is OpenCV (x image right, y image down, z optical axis). `R_bc` maps camera-frame
vectors to body-frame vectors.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import numpy as np

from lens import KannalaBrandt


def rot_z(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def mount_rotation(azimuth_deg: float, depression_deg: float, roll_deg: float = 0.0) -> np.ndarray:
    a, d, r = np.radians([azimuth_deg, depression_deg, roll_deg])
    z_c = np.array([np.cos(d) * np.cos(a), -np.cos(d) * np.sin(a), -np.sin(d)])
    x_c = np.array([-np.sin(a), -np.cos(a), 0.0])
    y_c = np.cross(z_c, x_c)
    R = np.stack([x_c, y_c, z_c], axis=1)
    # Roll about the optical axis, positive turning image right toward image down.
    c, s = np.cos(r), np.sin(r)
    return R @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


@dataclass
class Camera:
    name: str
    lens: KannalaBrandt
    R_bc: np.ndarray
    t_bc: np.ndarray


def rig_from_config(cfg: dict) -> list[Camera]:
    lenses = {name: KannalaBrandt.from_config(c) for name, c in cfg["lenses"].items()}
    cams = []
    for c in cfg["cameras"]:
        cams.append(
            Camera(
                name=c["name"],
                lens=lenses[c["lens"]],
                R_bc=mount_rotation(c["azimuth_deg"], c["depression_deg"], c.get("roll_deg", 0.0)),
                t_bc=np.array(c.get("position_m", (0.0, 0.0, 0.0)), dtype=float),
            )
        )
    return cams


@dataclass
class Pose:
    """Vehicle pose at one capture: body -> world rotation and body origin in world."""

    time_s: float
    R_wb: np.ndarray
    p_wb: np.ndarray


def lawnmower(cfg: dict) -> list[Pose]:
    """Parallel lines across a rectangle, flown at constant height and speed with the nose along
    the direction of travel, capturing at a fixed rate. Turnarounds take no time and capture
    nothing: this is an ideal path, not a flight plan."""
    x0, y0, x1, y1 = cfg["area_m"]
    z = float(cfg["height_m"])
    spacing = float(cfg["line_spacing_m"])
    speed = float(cfg["speed_mps"])
    step = speed / float(cfg["capture_hz"])
    heading = np.radians(float(cfg.get("line_heading_deg", 0.0)))  # 0 = lines run north-south
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    along = np.array([np.sin(heading), np.cos(heading)])
    across = np.array([np.cos(heading), -np.sin(heading)])
    half_len = 0.5 * abs(np.dot([x1 - x0, y1 - y0], np.abs(along)))
    half_wid = 0.5 * abs(np.dot([x1 - x0, y1 - y0], np.abs(across)))
    offsets = np.arange(-half_wid, half_wid + 1e-9, spacing)
    poses, t = [], 0.0
    for i, off in enumerate(offsets):
        sign = 1 if i % 2 == 0 else -1
        direction = along * sign
        yaw = np.arctan2(direction[1], direction[0])  # world yaw of body x, CCW from east
        for s in np.arange(-half_len, half_len + 1e-9, step):
            xy = np.array([cx, cy]) + across * off + direction * s
            poses.append(Pose(t, rot_z(yaw), np.array([xy[0], xy[1], z])))
            t += 1.0 / float(cfg["capture_hz"])
    return poses


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
EARTH_RADIUS_M = 6378137.0
MAV_CMD_NAV_WAYPOINT = 16
MAV_FRAME_GLOBAL_RELATIVE_ALT = 3


def mission_home(path: pathlib.Path) -> tuple[float, float]:
    """A QGC WPL 110 mission's home (item 0) latitude and longitude: the scene's origin."""
    row = path.read_text().splitlines()[1].split("\t")
    return float(row[8]), float(row[9])


def east_north(lat: np.ndarray, lon: np.ndarray, lat0: float, lon0: float) -> tuple[np.ndarray, np.ndarray]:
    """Equirectangular metres about (lat0, lon0), exact to millimetres over a few hundred metres."""
    e = np.radians(np.asarray(lon) - lon0) * EARTH_RADIUS_M * np.cos(np.radians(lat0))
    n = np.radians(np.asarray(lat) - lat0) * EARTH_RADIUS_M
    return e, n


def mission_waypoints(path: pathlib.Path) -> np.ndarray:
    """A QGC WPL 110 mission's NAV_WAYPOINT items as local ENU metres about its home (item 0),
    height above home."""
    rows = [line.split("\t") for line in path.read_text().splitlines()[1:] if line.strip()]
    lat0, lon0 = mission_home(path)
    out = []
    for r in rows[1:]:
        if int(r[3]) != MAV_CMD_NAV_WAYPOINT:
            continue
        assert int(r[2]) == MAV_FRAME_GLOBAL_RELATIVE_ALT, f"waypoint {r[0]}: frame {r[2]}"
        e, n = east_north(float(r[8]), float(r[9]), lat0, lon0)
        out.append([e, n, float(r[10])])
    return np.array(out)


def mission(cfg: dict) -> list[Pose]:
    """A mission's waypoints in order, from the first, flown at constant speed with the nose
    along the current leg and captures at a fixed rate by distance travelled; corners take no
    time. `mission` is a path in this repo."""
    wps = mission_waypoints(REPO_ROOT / cfg["mission"])
    step = float(cfg["speed_mps"]) / float(cfg["capture_hz"])
    legs = np.diff(wps, axis=0)
    lengths = np.linalg.norm(legs, axis=1)
    ends = np.concatenate([[0.0], np.cumsum(lengths)])
    poses = []
    for i, s in enumerate(np.arange(0.0, ends[-1] + 1e-9, step)):
        leg = min(np.searchsorted(ends, s, side="right") - 1, len(legs) - 1)
        p = wps[leg] + legs[leg] * (s - ends[leg]) / lengths[leg]
        yaw = np.arctan2(legs[leg][1], legs[leg][0])  # world yaw of body x, CCW from east
        poses.append(Pose(i / float(cfg["capture_hz"]), rot_z(yaw), p))
    return poses


# ArduPilot's attitude is the FRD body in NED; the scene is FLU in ENU.
NED_TO_ENU = np.array([[0.0, 1, 0], [1, 0, 0], [0, 0, -1]])
FRD_TO_FLU = np.diag([1.0, -1, -1])


def attitude_flu_enu(roll_deg: float, pitch_deg: float, yaw_deg: float) -> np.ndarray:
    """ArduPilot roll, pitch, yaw (ZYX Euler, FRD body in NED, yaw clockwise from north) -> R_wb."""
    r, p, y = np.radians([roll_deg, pitch_deg, yaw_deg])
    cr, sr, cp, sp, cy, sy = np.cos(r), np.sin(r), np.cos(p), np.sin(p), np.cos(y), np.sin(y)
    R_ned_frd = np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ]
    )
    return NED_TO_ENU @ R_ned_frd @ FRD_TO_FLU


def flown(cfg: dict) -> list[Pose]:
    """Poses sampled from a flight log by `flown_path.py`: position in the scene's ENU frame, height
    above the FC's home, and the attitude as the FC estimated it. `poses` is a path in this repo."""
    rows = [line.split(",") for line in (REPO_ROOT / cfg["poses"]).read_text().splitlines() if line and not line.startswith("#")]
    header, rows = rows[0], rows[1:]
    col = {k: header.index(k) for k in ("time_s", "east_m", "north_m", "up_m", "roll_deg", "pitch_deg", "yaw_deg")}
    out = []
    for r in rows:
        v = {k: float(r[i]) for k, i in col.items()}
        out.append(Pose(v["time_s"], attitude_flu_enu(v["roll_deg"], v["pitch_deg"], v["yaw_deg"]), np.array([v["east_m"], v["north_m"], v["up_m"]])))
    return out


TRAJECTORIES = {"lawnmower": lawnmower, "mission": mission, "flown": flown}


def trajectory_from_config(cfg: dict) -> list[Pose]:
    cfg = dict(cfg)
    return TRAJECTORIES[cfg.pop("kind")](cfg)
