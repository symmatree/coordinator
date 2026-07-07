#!/usr/bin/env bash
# Smoke-test a built coordinator-vio-estimator image: does the binary boot, bind its
# input sockets, and turn a recorded input fixture into finite, plausible pose?
#
# Tiers:
#   smoke (default)  tier 0 boot+bind, tier 1 replay a 10s fixture -> pose is finite,
#                    quaternion ~unit. Fast (~15s), hardware-free, catches real breakage.
#   full             reserved: + golden-trajectory regression. Needs the deterministic
#                    offline harness (reproducible output); NOT implemented yet.
#
# Runs the image via a container runtime ($RUNTIME, default podman). CI has one; the bare
# dev pod does not -- there, use build-local.sh for a compile-check and let CI run the
# smoke, or `apt install podman`.
#
#   RUNTIME=podman IMAGE=coordinator-vio-estimator:local ./test.sh          # smoke
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
RUNTIME="${RUNTIME:-podman}"
IMAGE="${IMAGE:-coordinator-vio-estimator:local}"
TIER="${1:-smoke}"
FIX="$HERE/testdata/smoke.feat"
CFG="$REPO/host/ansible/roles/coordinator/files/oak_d.yaml"

command -v "$RUNTIME" >/dev/null || {
	echo "test: no container runtime '$RUNTIME' (set RUNTIME= or install podman)"
	exit 2
}
[ -f "$FIX" ] || {
	echo "test: missing fixture $FIX"
	exit 2
}
[ -f "$CFG" ] || {
	echo "test: missing config $CFG"
	exit 2
}

WORK="$(mktemp -d)"
IPC="$WORK/ipc"
mkdir -p "$IPC"
cid=""
tap=""
cleanup() {
	[ -n "$tap" ] && kill "$tap" 2>/dev/null
	[ -n "$cid" ] && "$RUNTIME" rm -f "$cid" >/dev/null 2>&1
	rm -rf "$WORK"
}
trap cleanup EXIT

# Rootful docker runs the container as root, so it would create the AF_UNIX input
# sockets root-owned and the host-side replayer (runner user) couldn't sendto them.
# Run as the invoking uid so sockets are host-user-owned. Rootless podman already maps
# container-root -> host user, so it needs no --user.
userflag=()
[ "$RUNTIME" = docker ] && userflag=(--user "$(id -u):$(id -g)")

echo "== tier 0: boot + bind sockets (image: $IMAGE) =="
cid="$("$RUNTIME" run -d --rm "${userflag[@]}" -v "$IPC:/tmp" -v "$CFG:/config/oak_d.yaml:ro" "$IMAGE")" ||
	{
		echo "FAIL: could not start estimator container"
		exit 1
	}
for _ in $(seq 1 150); do
	[ -S "$IPC/chobits_imu" ] && [ -S "$IPC/chobits_features" ] && break
	sleep 0.1
done
if [ ! -S "$IPC/chobits_imu" ] || [ ! -S "$IPC/chobits_features" ]; then
	echo "FAIL: estimator did not bind input sockets"
	"$RUNTIME" logs "$cid" 2>&1 | tail -20
	exit 1
fi
echo "  ok: bound chobits_imu + chobits_features"

echo "== tier 1: replay smoke fixture, assert pose finite =="
COORDINATOR_IPC_DIR="$IPC" python3 "$REPO/bin/vio-pose-tap" --socket "$IPC/chobits_server" --out "$WORK/pose.csv" --quiet &
tap=$!
sleep 0.5
COORDINATOR_IPC_DIR="$IPC" python3 "$REPO/harness/input_replayer.py" "$FIX"
sleep 2
kill "$tap" 2>/dev/null
tap=""

python3 - "$WORK/pose.csv" <<'PY'
import sys, csv, math, statistics
rows = list(csv.DictReader(open(sys.argv[1])))
assert len(rows) >= 10, f"only {len(rows)} pose samples (estimator emitted ~nothing)"
cols = ("qw","qx","qy","qz","px","py","pz","vx","vy","vz")
for r in rows:
    assert all(math.isfinite(float(r[k])) for k in cols), "non-finite pose value"
qn = statistics.mean(math.hypot(float(r['qw']), float(r['qx']), float(r['qy']), float(r['qz'])) for r in rows)
assert 0.9 < qn < 1.1, f"quaternion norm off ({qn:.3f}) -- estimator output not a valid pose"
print(f"  ok: {len(rows)} pose samples, all finite, quat norm ~{qn:.3f}")
PY
rc=$?
[ $rc -ne 0 ] && {
	echo "SMOKE FAIL"
	exit 1
}

# The offline runner (vio-offline-runner) regenerates pose deterministically and writes a
# provenance sidecar. Run the same fixture into two dirs and assert byte-identical output
# (determinism) + a valid sidecar. This exercises python3-in-image + vins_fusion_offline.
echo "== offline runner: deterministic regen + provenance sidecar =="
mkdir -p "$WORK/a" "$WORK/b"
cp "$FIX" "$WORK/a/smoke.feat"
cp "$FIX" "$WORK/b/smoke.feat"
for d in a b; do
	"$RUNTIME" run --rm "${userflag[@]}" \
		-v "$WORK/$d:/data" -v "$CFG:/config/oak_d.yaml:ro" \
		--entrypoint /opt/coordinator/bin/vio-offline-runner \
		-e COORDINATOR_SHA=smoketest \
		"$IMAGE" /data/smoke.feat >/dev/null || {
		echo "FAIL: offline runner exited nonzero ($d)"
		exit 1
	}
done

if ! python3 - "$WORK/a" "$WORK/b" <<'PY'
import csv, hashlib, json, math, sys
a, b = sys.argv[1], sys.argv[2]
csv_a, side_a = f"{a}/smoke.vinspose.csv", f"{a}/smoke.vinspose.polisher.json"
def sha(p): return hashlib.sha256(open(p, "rb").read()).hexdigest()
ha, hb = sha(csv_a), sha(f"{b}/smoke.vinspose.csv")
assert ha == hb, f"non-deterministic: pose CSVs differ ({ha[:12]} != {hb[:12]})"
rows = list(csv.DictReader(open(csv_a)))
assert len(rows) >= 10, f"only {len(rows)} poses"
cols = ("qw","qx","qy","qz","px","py","pz","vx","vy","vz")
assert all(math.isfinite(float(r[k])) for r in rows for k in cols), "non-finite pose"
d = json.load(open(side_a))
assert d["instrument"]["sha"] == "smoketest", "sidecar missing estimator sha"
assert d["object"][0]["sha256"] and d["result"][0]["sha256"], "sidecar missing hashes"
assert d["result"][0]["sha256"] == ha, "sidecar result sha != actual CSV sha"
print(f"  ok: deterministic ({len(rows)} poses, CSV sha {ha[:12]}), sidecar valid")
PY
then
	echo "SMOKE FAIL (offline runner)"
	exit 1
fi

if [ "$TIER" = full ]; then
	echo "== tier 2 (golden regression): NOT IMPLEMENTED -- needs the deterministic offline harness =="
fi
echo "SMOKE PASS"
