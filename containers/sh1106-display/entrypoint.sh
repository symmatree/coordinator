#!/usr/bin/env bash
# sh1106-display entrypoint: run the status-display loop.
set -euo pipefail

exec python3 /opt/display/display.py
