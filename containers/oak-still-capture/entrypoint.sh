#!/usr/bin/env bash
# oak-still-capture entrypoint: ensure the capture dir exists, then exec the loop.
set -euo pipefail

CAPTURE_DIR="${OAK_CAPTURE_DIR:-/captures}"
mkdir -p "${CAPTURE_DIR}"

exec python3 /opt/oak/capture.py
