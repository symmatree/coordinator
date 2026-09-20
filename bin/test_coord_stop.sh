#!/usr/bin/env bash
# Checks bin/coord's stop against real processes named dumb-init.
# Run by hand: bash bin/test_coord_stop.sh
#
# Uses a copy of /bin/sleep named dumb-init, because pkill -x matches comm --
# which comes from the executable name, not argv[0], so `exec -a` would not do.
set -uo pipefail
cd "$(dirname "$0")" || exit 2

fail=0
ok() { echo "ok   $1"; }
bad() {
	echo "FAIL $1  ${2:-}"
	fail=$((fail + 1))
}

if pgrep -x dumb-init >/dev/null 2>&1; then
	echo "SKIP: a real dumb-init is running here; this test would signal it"
	exit 0
fi

d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
cp /bin/sleep "$d/dumb-init"
cp /bin/sleep "$d/dumb-initish"

run_stop() { bash -c 'source <(sed -n "/^# Each container/,/^}/p" ./coord); stop_processes' 2>&1; }

out=$(run_stop)
if grep -q "no container init processes found" <<<"$out"; then
	ok "reports nothing to do when no container is running"
else bad "reports nothing to do when no container is running" "$out"; fi

"$d/dumb-init" 30 &
victim=$!
"$d/dumb-initish" 30 &
bystander=$!
sleep 0.3

out=$(run_stop)
sleep 0.5
if grep -q "SIGTERM dumb-init" <<<"$out"; then ok "reports what it signalled"; else
	bad "reports what it signalled" "$out"
fi
if kill -0 "$victim" 2>/dev/null; then bad "signals a process whose comm is dumb-init" "still alive"; else
	ok "signals a process whose comm is dumb-init"
fi
if kill -0 "$bystander" 2>/dev/null; then ok "leaves a near-miss comm alone"; else
	bad "leaves a near-miss comm alone" "killed dumb-initish too"
fi
kill "$bystander" 2>/dev/null

echo
if ((fail == 0)); then
	echo "test_coord_stop: all checks passed"
else
	echo "$fail failed"
	exit 1
fi
