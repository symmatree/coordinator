#!/bin/sh
set -eu

# PYTHONUNBUFFERED keeps router stdout/stderr line-buffered under `docker logs`
# (the empty-logs trap the vio containers hit -- don't repeat it here).
export PYTHONUNBUFFERED=1

exec python3 /opt/coordinator/router.py "$@"
