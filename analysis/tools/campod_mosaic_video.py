#!/usr/bin/env python3
"""The four campods' stills from one flight as a 2x2 4K video, one still-set per step.

Each pod looks out from its corner at 45 degrees and down, landscape, with nadir at the
bottom edge of its frame (docs/rekon10/sensor-raft.md, docs/campod.md). Laid out as seen
from above with the nose up: NW top-left and NE top-right as they come, SW bottom-left and
SE bottom-right rotated 180 degrees so their outward edge is also away from the centre.
Nadir edges then meet along the middle, and ground features continue across tiles.

Stills are grouped across pods by exposure second (sidecar `exposure_wall_ns`); a pod with
no still in a second leaves its tile black. Each step is labelled with its UTC second and,
given the flight's DataFlash log, the FC's altitude above home (CTUN.Alt).

Usage:
  campod_mosaic_video.py <flight_dir> [--fc-bin <log.bin>] [--seconds 2]
      [--session campod-ne=<id> ...] [--out <file.mp4>]

Sessions default to, per pod, the one under captures/<pod>/ with the most stills; name one
with --session when a flight holds several. The output defaults to
<flight_dir>/derived/campod-mosaic.mp4. Needs ffmpeg on PATH, Pillow, and for --fc-bin the
analysis deps (pymavlink, pandas) with analysis/ on PYTHONPATH.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PODS = ("nw", "ne", "sw", "se")
TILE = (1920, 1080)
ORIGIN = {"nw": (0, 0), "ne": (1920, 0), "sw": (0, 1080), "se": (1920, 1080)}
ROTATE_180 = {"sw", "se"}


def pick_sessions(flight: Path, chosen: dict) -> dict:
    out = {}
    for pod in PODS:
        node = f"campod-{pod}"
        root = flight / "captures" / node
        if node in chosen:
            out[pod] = root / chosen[node]
            continue
        sessions = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith("@")] if root.is_dir() else []
        counted = [(len(list(d.glob(f"{node}_*.jpg"))), d) for d in sessions]
        counted = [c for c in counted if c[0]]
        if counted:
            out[pod] = max(counted)[1]
    return out


def stills_by_second(session: Path) -> dict:
    out = {}
    for sidecar in sorted(session.glob("campod-*.json")):
        meta = json.loads(sidecar.read_text())
        jpg = sidecar.with_suffix(".jpg")
        if meta.get("kind") == "still" and jpg.exists():
            out[round(meta["exposure_wall_ns"] / 1e9)] = jpg
    return out


def altitude_lookup(fc_bin: Path):
    """UTC seconds -> CTUN.Alt, through the log's GPS week/ms."""
    import numpy as np
    from ardupilot_log import parse_log

    F, _, _ = parse_log(fc_bin, ["GPS", "CTUN"])
    gps = F["GPS"]
    gps = gps[(gps.I == 0) & (gps.GWk > 0)]
    epoch = datetime(1980, 1, 6, tzinfo=timezone.utc)
    utc = [(epoch + timedelta(weeks=int(w), milliseconds=int(ms)) - timedelta(seconds=18)).timestamp()
           for w, ms in zip(gps.GWk, gps.GMS)]
    offset = float(np.median(np.array(utc) - gps.t_s.to_numpy()))
    ctun = F["CTUN"].sort_values("t_s")
    return lambda t: float(np.interp(t - offset, ctun.t_s, ctun.Alt))


def font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("flight_dir", type=Path)
    ap.add_argument("--fc-bin", type=Path)
    ap.add_argument("--seconds", type=float, default=2.0, help="seconds on screen per still-set")
    ap.add_argument("--session", action="append", default=[], help="campod-<pod>=<session id>")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    chosen = dict(s.split("=", 1) for s in args.session)
    sessions = pick_sessions(args.flight_dir, chosen)
    if not sessions:
        sys.exit(f"no campod stills under {args.flight_dir / 'captures'}")
    stills = {pod: stills_by_second(s) for pod, s in sessions.items()}
    seconds = sorted(set().union(*(s.keys() for s in stills.values())))
    alt = altitude_lookup(args.fc_bin) if args.fc_bin else None
    out = args.out or args.flight_dir / "derived" / "campod-mosaic.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    for pod, s in sessions.items():
        print(f"campod-{pod}: {s.name}, {len(stills[pod])} stills")

    big, small = font(44), font(36)
    with tempfile.TemporaryDirectory() as tmp:
        for k, sec in enumerate(seconds):
            canvas = Image.new("RGB", (TILE[0] * 2, TILE[1] * 2))
            draw = ImageDraw.Draw(canvas)
            for pod in PODS:
                x, y = ORIGIN[pod]
                jpg = stills.get(pod, {}).get(sec)
                if jpg:
                    im = Image.open(jpg).resize(TILE, Image.LANCZOS)
                    canvas.paste(im.rotate(180) if pod in ROTATE_180 else im, (x, y))
                label_y = y + 20 if pod in ("nw", "ne") else y + TILE[1] - 70
                draw.text((x + 24, label_y), f"campod-{pod}", font=small, fill=(255, 255, 255),
                          stroke_width=3, stroke_fill=(0, 0, 0))
            label = datetime.fromtimestamp(sec, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if alt:
                label += f"   alt {alt(sec):.1f} m"
            label += f"   {k + 1}/{len(seconds)}"
            draw.text((TILE[0], TILE[1]), label, font=big, anchor="mm", fill=(255, 255, 0),
                      stroke_width=4, stroke_fill=(0, 0, 0))
            canvas.save(Path(tmp) / f"f_{k:05d}.png")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"1/{args.seconds}",
                        "-i", str(Path(tmp) / "f_%05d.png"), "-vf", "fps=30,format=yuv420p",
                        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-movflags", "+faststart",
                        str(out)], check=True)
    print(f"{out}: {len(seconds)} still-sets, {len(seconds) * args.seconds:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
