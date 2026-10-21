"""Build-time: write the campod's libcamera tuning files, with rpi.sync overridden.

libcamera's sync algorithm takes its parameters from the sensor's tuning JSON, and the
stock imx708 files carry an empty `"rpi.sync": {}`, so the source defaults apply:
`ready_frame` 100 and `sync_period` 30 (src/ipa/rpi/controller/rpi/sync.cpp, Sync::read).
`ready_frame` is how many frames pass before SyncReady; `sync_period` is how often the
server broadcasts its timing. There is a SyncFrames control for the first, but the IPA
advertises its range as 100..100000, and nothing but the JSON sets the second.

The files are copies of the installed ones with only the rpi.sync block changed, written
under OUT_ROOT/rpi/vc4/ with the same names. The Dockerfile puts OUT_ROOT on
LIBCAMERA_IPA_CONFIG_PATH, which libcamera searches before its system directory, looking
up `<sensor model>.json` itself. That works whichever imx708 variant is fitted, and unlike
LIBCAMERA_RPI_TUNING_FILE it survives picamera2, whose constructor deletes that variable
when it is not given a `tuning=` argument.

A failure here fails the image build: an installed libcamera without these keys, or a
tuning file with no rpi.sync entry, would otherwise build an image that quietly ran on the
defaults.
"""

import json
import sys
from pathlib import Path

SYNC_PARAMS = {"ready_frame": 50, "sync_period": 5}

STOCK_DIR = Path("/usr/share/libcamera/ipa/rpi/vc4")
IPA_MODULE = Path("/usr/lib/aarch64-linux-gnu/libcamera/ipa/ipa_rpi_vc4.so")
OUT_ROOT = Path("/opt/campod/libcamera-ipa")


def override(tuning):
    """Return `tuning` with SYNC_PARAMS merged into its rpi.sync entry."""
    blocks = [algo for algo in tuning["algorithms"] if "rpi.sync" in algo]
    if len(blocks) != 1:
        raise SystemExit(f"sync_tuning: expected one rpi.sync entry, found {len(blocks)}")
    blocks[0]["rpi.sync"].update(SYNC_PARAMS)
    return tuning


def main(stock_dir=STOCK_DIR, ipa_module=IPA_MODULE, out_root=OUT_ROOT):
    # Sync::read() takes an absent key as its default, so a renamed key would be ignored
    # without complaint. The names are NUL-terminated string literals in the IPA module.
    module = ipa_module.read_bytes()
    for key in SYNC_PARAMS:
        if key.encode() + b"\0" not in module:
            raise SystemExit(f"sync_tuning: {ipa_module} has no '{key}' parameter")

    sources = sorted(stock_dir.glob("imx708*.json"))
    if not sources:
        raise SystemExit(f"sync_tuning: no imx708 tuning files in {stock_dir}")
    out_dir = out_root / "rpi" / "vc4"
    out_dir.mkdir(parents=True, exist_ok=True)
    for src in sources:
        tuning = override(json.loads(src.read_text()))
        (out_dir / src.name).write_text(json.dumps(tuning, indent=4) + "\n")
        print(f"sync_tuning: {out_dir / src.name}: rpi.sync {SYNC_PARAMS}")


if __name__ == "__main__":
    main(*(Path(a) for a in sys.argv[1:]))
