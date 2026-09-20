#!/usr/bin/env bash
# Checks how bin/coord picks the processes to signal, against a fake /proc.
# Run by hand: bash bin/test_coord_stop.sh
set -uo pipefail
cd "$(dirname "$0")" || exit 2

fail=0
ok() { echo "ok   $1"; }
bad() {
	echo "FAIL $1  ${2:-}"
	fail=$((fail + 1))
}
want() { # description, needle -- present in $got
	if grep -qw "$2" <<<"$got"; then ok "$1"; else bad "$1" "missing $2 from '$got'"; fi
}
reject() { # description, needle -- absent from $got
	if grep -qw "$2" <<<"$got"; then bad "$1" "leaked $2"; else ok "$1"; fi
}

root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT

mk() { # pid, comm, children
	mkdir -p "$root/$1/task/$1"
	echo "$2" >"$root/$1/comm"
	if [[ -n ${3:-} ]]; then echo "$3" >"$root/$1/task/$1/children"; fi
}

mk 1234 "sshd" ""               # ordinary host process
mk 5000 "dumb-init" "5001 5002" # container init, two children
mk 6000 "dumb-init" ""          # container init with no child yet
mk 5001 "python3" ""            # the binary itself
mk 7000 "dumb-init-ish" "7001"  # near-miss name must not match

pick() { bash -c 'source <(sed -n "/^COORD_PROC=/,/^}/p" ./coord); container_inits'; }

got=$(COORD_PROC="$root" pick | tr '\n' ' ' | tr -s ' ')
echo "     selected: $got"
want "finds a container init" 5000
want "finds every container init, not just the first" 6000
reject "does not match a near-miss comm" 7000
reject "ignores ordinary host processes" 1234
reject "does not descend to the child; dumb-init proxies for it" 5001

# Real /proc: this box runs no containers, so nothing should be selected.
real=$(pick | tr -d '[:space:]')
if [[ -z $real ]]; then ok "selects nothing on a host with no containers"; else
	bad "selects nothing on a host with no containers" "got '$real'"
fi

echo
if ((fail == 0)); then
	echo "test_coord_stop: all checks passed"
else
	echo "$fail failed"
	exit 1
fi
