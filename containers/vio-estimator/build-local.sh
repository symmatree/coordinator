#!/usr/bin/env bash
# Build coordinator-vio-estimator locally with buildah -- hermetic (the image pins
# Debian/Ceres-2.1, which the pinned vins source needs) and native to the host arch
# (x86 on the dev pod, arm on a Pi). Fast iteration on overlay/ edits: ~2 min cold,
# ~50 s after a source change (deps + clone stay cached).
#
# Storage lives in /tmp (local overlay, NOT the NFS home -- buildah can't chown there)
# via fuse-overlayfs, and is intentionally NOT persisted across pod restarts: the cache
# speeds up a work session and a restart clears it. Needs sudo for the container caps
# (the pod's bounding set has them; the login shell doesn't).
#
#   ./build-local.sh                       # -> localhost/coordinator-vio-estimator:local
#   ./build-local.sh myrepo/vins:test      # custom tag
#
# This only BUILDS (compile-checks your overlay edits). To run/smoke-test the result,
# see test.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SHA="$(sed -n 's/^VINS_FUSION_SHA=//p' "$HERE/upstream.lock")"
TAG="${1:-coordinator-vio-estimator:local}"
STORE="${BUILDAH_STORE:-/tmp/buildah-store}"

[ -n "$SHA" ] || {
	echo "build-local: no VINS_FUSION_SHA in upstream.lock" >&2
	exit 1
}
command -v fuse-overlayfs >/dev/null || {
	echo "build-local: fuse-overlayfs not found" >&2
	exit 1
}

echo "build-local: $TAG  (SHA ${SHA:0:12}, store $STORE)" >&2
sudo mkdir -p "$STORE"
exec sudo buildah --root "$STORE" \
	--storage-driver overlay --storage-opt overlay.mount_program=/usr/bin/fuse-overlayfs \
	bud --layers -t "$TAG" --build-arg VINS_FUSION_SHA="$SHA" "$HERE"
