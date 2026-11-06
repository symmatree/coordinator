"""Sample a flight's estimated poses at the capture rate, for the `flown` trajectory.

    python3 analysis/mapping_sim/flown_path.py <log.bin> <mission.waypoints> <out.csv> [--hz 1]

Takes the span the FC was in Auto (MODE 3), cut at `--end` if given, and samples it every 1/hz
seconds of FC time from the moment Auto began: position from `POS` (latitude and longitude as metres east and north of the
mission's home, which is the scene's origin; `RelHomeAlt` as height), interpolated linearly, and
attitude from `ATT`, interpolated on the rotation. The EKF is taken as exact: these are the poses
the FC believed it flew. `TimeUS` runs a few hundred ppm fast against GPS
(`docs/flight-data-interpretation.md`), which over a mission moves the samples by tenths of a
second along a path flown at a few metres per second.

The CSV starts with `#` lines recording the log's path and SHA-256, the window and the mission,
then `time_s,east_m,north_m,up_m,roll_deg,pitch_deg,yaw_deg` with the attitude in ArduPilot's
convention (FRD body in NED, yaw clockwise from north).
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ardupilot_log import parse_log  # noqa: E402
from rig import REPO_ROOT, east_north, mission_home  # noqa: E402

MODE_AUTO = 3


def auto_window(mode) -> tuple[float, float]:
    """The first Auto span: from entering Auto to the next mode change (or the log's end)."""
    rows = mode.sort_values("t_s")
    starts = rows.index[rows.ModeNum == MODE_AUTO]
    assert len(starts), "the log never enters Auto"
    t0 = rows.t_s[starts[0]]
    later = rows[(rows.t_s > t0) & (rows.ModeNum != MODE_AUTO)]
    return t0, (later.t_s.iloc[0] if len(later) else np.inf)


def sample(F: dict, hz: float, lat0: float, lon0: float, end_s: float = np.inf) -> np.ndarray:
    t0, t1 = auto_window(F["MODE"])
    pos, att = F["POS"], F["ATT"]
    t1 = min(t1, end_s, pos.t_s.max(), att.t_s.max())
    t = np.arange(t0, t1 + 1e-9, 1.0 / hz)
    lat = np.interp(t, pos.t_s, pos.Lat)
    lon = np.interp(t, pos.t_s, pos.Lng)
    up = np.interp(t, pos.t_s, pos.RelHomeAlt)
    e, n = east_north(lat, lon, lat0, lon0)
    rot = Rotation.from_euler("ZYX", att[["Yaw", "Pitch", "Roll"]].to_numpy(), degrees=True)
    ypr = Slerp(att.t_s.to_numpy(), rot)(t).as_euler("ZYX", degrees=True)
    return np.column_stack([t - t0, e, n, up, ypr[:, 2], ypr[:, 1], ypr[:, 0] % 360.0]), (t0, t1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=pathlib.Path)
    ap.add_argument("mission", type=pathlib.Path)
    ap.add_argument("out", type=pathlib.Path)
    ap.add_argument("--hz", type=float, default=1.0)
    ap.add_argument("--end", type=float, default=np.inf, help="stop here (s after the log's first record) if Auto runs past it")
    ap.add_argument("--why-end", default="", help="the reason for --end, written into the header")
    a = ap.parse_args()
    F, _, _ = parse_log(a.log, ["MODE", "POS", "ATT"])
    lat0, lon0 = mission_home(a.mission)
    rows, (t0, t1) = sample(F, a.hz, lat0, lon0, a.end)
    sha = hashlib.sha256(a.log.read_bytes()).hexdigest()
    mission = a.mission.resolve()
    mission = mission.relative_to(REPO_ROOT) if mission.is_relative_to(REPO_ROOT) else mission
    lines = [
        f"# log: {a.log.name} (sha256 {sha})",
        f"# Auto from {t0:.3f} s to {t1:.3f} s after the log's first record, sampled at {a.hz:g} Hz of FC time",
        *([f"# ends at {a.end:g} s: {a.why_end}"] if np.isfinite(a.end) else []),
        f"# scene origin: {mission} home ({lat0}, {lon0}); up is POS.RelHomeAlt",
        "time_s,east_m,north_m,up_m,roll_deg,pitch_deg,yaw_deg",
    ]
    lines += [",".join(f"{v:.4f}" if k else f"{v:.3f}" for k, v in enumerate(r)) for r in rows]
    a.out.write_text("\n".join(lines) + "\n")
    print(f"{len(rows)} poses -> {a.out}")


if __name__ == "__main__":
    main()
