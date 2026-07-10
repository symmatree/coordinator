"""Index and visualize coordinator-side capture sessions (disparity + stills).

The on-vehicle capture writes one JSON sidecar per frame next to a `.png`
(disparity, 640x400, 8-bit) or `.jpg` (still, 12 MP RGB), under

    <flight>/captures/<device>/<sessionTZ>/<device>_<seq>_<tsZ>.{json,png,jpg}

Each sidecar carries timing on three clocks -- `wall_clock_unix`, `monotonic_ns`
(the coordinator's CLOCK_MONOTONIC, the one that TIMESYNC-aligns to the FC; see
`analysis/vio-quality-experiments.md` E13), and the OAK-D `sensor_timestamp_ns` --
plus `kind`, `device_seq`, `width`/`height`, and (stills) `exposure_us`/`iso`.

This module only reads captures; it has no OAK-D / hardware dependency. Joining a
session to the FC EKF/GPS trajectory (via `monotonic_ns`) is a downstream step that
needs the decoded flight `.bin` -- see `index_session` output and #64.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import tempfile

import numpy as np
import pandas as pd

DISPARITY_MAX = 95  # observed 8-bit disparity ceiling for the OAK-D config on rekon10


def index_session(session_dir: str) -> pd.DataFrame:
    """Parse every JSON sidecar in one session dir into a frame table (time-sorted)."""
    rows = []
    for jf in glob.glob(os.path.join(session_dir, "*.json")):
        try:
            d = json.load(open(jf))
        except Exception:
            continue
        img = os.path.join(session_dir, d.get("file", ""))
        d["json_path"] = jf
        d["img_path"] = img if os.path.exists(img) else None
        rows.append(d)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["session"] = os.path.basename(session_dir.rstrip("/"))
    df["t_wall"] = pd.to_datetime(df["wall_clock_unix"], unit="s", utc=True)
    return df.sort_values("monotonic_ns").reset_index(drop=True)


def index_flight(flight_dir: str) -> pd.DataFrame:
    """Index every device/session under `<flight>/captures/`."""
    frames = []
    for sess in glob.glob(os.path.join(flight_dir, "captures", "*", "*")):
        if os.path.isdir(sess):
            df = index_session(sess)
            if not df.empty:
                df["device"] = os.path.basename(os.path.dirname(sess))
                frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("monotonic_ns").reset_index(drop=True)


def summarize(df: pd.DataFrame) -> dict:
    """Cadence / coverage / exposure summary for one session (or the whole flight)."""
    if df.empty:
        return {"frames": 0}
    out = {"frames": int(len(df)), "kinds": df["kind"].value_counts().to_dict()}
    span = (df["monotonic_ns"].max() - df["monotonic_ns"].min()) / 1e9
    out["span_s"] = round(float(span), 1)
    for kind, g in df.groupby("kind"):
        t = np.sort(g["monotonic_ns"].to_numpy()) / 1e9
        if len(t) > 1:
            dt = np.diff(t)
            out[f"{kind}_hz_median"] = round(float(1.0 / np.median(dt)), 2)
            out[f"{kind}_gap_max_s"] = round(float(dt.max()), 1)
    if "iso" in df:
        iso = df["iso"].dropna()
        if len(iso):
            out["iso_range"] = [int(iso.min()), int(iso.max())]
    return out


def load_disparity(path: str) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(path))


def colorize_disparity(arr: np.ndarray, vmax: float = DISPARITY_MAX, cmap: str = "turbo") -> np.ndarray:
    """8-bit disparity -> RGB uint8; 0 (no-match) rendered black."""
    import matplotlib.cm as cm

    norm = np.clip(arr.astype(np.float32) / max(vmax, 1), 0, 1)
    rgb = (cm.get_cmap(cmap)(norm)[..., :3] * 255).astype(np.uint8)
    rgb[arr == 0] = 0
    return rgb


def animate_disparity(df: pd.DataFrame, out_path: str, fps: int = 15,
                      vmax: float = DISPARITY_MAX, label: bool = True) -> str:
    """Render the disparity frames (time-ordered) to an mp4 via system ffmpeg.

    Zero extra deps: colorized frames are written to a temp dir and encoded with
    /usr/bin/ffmpeg. Returns `out_path`.
    """
    from PIL import Image, ImageDraw

    disp = df[(df["kind"] == "disparity") & df["img_path"].notna()].sort_values("monotonic_ns")
    if disp.empty:
        raise ValueError("no disparity frames in df")
    t0 = disp["monotonic_ns"].iloc[0]
    with tempfile.TemporaryDirectory() as tmp:
        for i, (_, row) in enumerate(disp.iterrows()):
            rgb = colorize_disparity(load_disparity(row["img_path"]), vmax=vmax)
            im = Image.fromarray(rgb)
            if label:
                dr = ImageDraw.Draw(im)
                secs = (row["monotonic_ns"] - t0) / 1e9
                dr.text((6, 6), f"seq {int(row['seq'])}  t+{secs:6.1f}s  "
                                f"{row['t_wall'].strftime('%H:%M:%S')}Z", fill=(255, 255, 255))
            im.save(os.path.join(tmp, f"f{i:05d}.png"))
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
             "-i", os.path.join(tmp, "f%05d.png"), "-pix_fmt", "yuv420p", out_path],
            check=True,
        )
    return out_path
