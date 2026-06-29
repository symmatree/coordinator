#!/usr/bin/env bash
# pod-camera entrypoint: ensure the capture dir exists, then exec the loop.
set -euo pipefail

CAPTURE_DIR="${POD_CAPTURE_DIR:-/captures}"
mkdir -p "${CAPTURE_DIR}"

exec python3 /opt/pod/capture.py
