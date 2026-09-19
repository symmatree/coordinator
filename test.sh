#!/usr/bin/env bash
# Run the repo's Python tests.
#
# They were written as standalone scripts run by hand, each exiting non-zero on
# failure, so this runs them rather than introducing a framework they do not use.
# Each runs from its own directory because several resolve paths relative to
# themselves.
#
# Not wired to CI yet -- deliberately, that is a separate change.

set -uo pipefail
cd "$(dirname "$0")" || exit 2

# Run everywhere.
TESTS=(
	analysis/test_analysis_modules.py
	bin/test_coord_version.py
	bin/test_coord_sessions.py
	containers/campod-camera/test_capture_wait.py
	containers/sh1106-display/test_display.py
	harness/test_input_replayer.py
	harness/test_router_stack.py
	harness/test_router_telem.py
)

# Run at image build time instead, in the environment they target, and NOT here.
#
# This is not a way of hiding a red test: each is already a build gate, so the
# coverage exists and is enforced -- running them outside their container tests
# the wrong machine.
#
#   containers/coordinator-mavlink/test_router.py
#     Dockerfile:37. Spawns router.py and talks to it over a unix socket. Passes
#     on trixie/py3.13 in the image; fails on a dev box with a different Python.
#
# Partially covered here: containers/campod-camera/test_capture_wait.py is in
# the list above, but its Dockerfile invocation adds --require-manifest, which
# checks the baked /etc/container-image that only exists inside the image.

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
