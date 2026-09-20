#!/usr/bin/env bash
# Run the Python tests that have no container of their own.
#
# They were written as standalone scripts run by hand, each exiting non-zero on
# failure, so this runs them rather than introducing a framework they do not use.
# Each runs from its own directory because several resolve paths relative to
# themselves.
#
# docs/ci.md records where the rest of the suite runs.

set -uo pipefail
cd "$(dirname "$0")" || exit 2

TESTS=(
	analysis/test_analysis_modules.py
	bin/test_coord_version.py
	bin/test_coord_sessions.py
	harness/test_input_replayer.py
	harness/test_router_stack.py
	harness/test_router_telem.py
)

pass=0
declare -a failed=()

for t in "${TESTS[@]}"; do
	printf '\n=== %s\n' "$t"
	if (cd "$(dirname "$t")" && python3 "$(basename "$t")"); then
		pass=$((pass + 1))
	else
		failed+=("$t")
	fi
done

printf '\n%s\n' "----------------------------------------"
if [[ ${#failed[@]} -eq 0 ]]; then
	printf '%d/%d passed\n' "$pass" "${#TESTS[@]}"
	exit 0
fi
printf '%d/%d passed, %d failed:\n' "$pass" "${#TESTS[@]}" "${#failed[@]}"
printf '  %s\n' "${failed[@]}"
exit 1
