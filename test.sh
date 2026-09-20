#!/usr/bin/env bash
# Run the repo's Python tests that belong on a workstation.
#
# They were written as standalone scripts run by hand, each exiting non-zero on
# failure, so this runs them rather than introducing a framework they do not use.
# Each runs from its own directory because several resolve paths relative to
# themselves.
#
# `analysis/` and `harness/` are workstation and bench tooling -- nothing ships
# them to a device -- and `bin/coord` is the host CLI that drives compose, so
# none of the three has a container to be tested inside. What they do have is
# the JupyterHub notebook image, which is where this code is actually run; CI
# runs this script in that image, so the dependencies below come from the same
# place a human's do. See .github/workflows/tests.yaml.

set -uo pipefail
cd "$(dirname "$0")" || exit 2

# Third-party imports these need, all present in the notebook image:
#   pillow          analysis/test_analysis_modules.py
#   numpy, scipy    analysis/test_analysis_modules.py
#   pymavlink       harness/test_router_telem.py, harness/test_router_stack.py
#                   (via fake_fc)
# The bin/ and harness/test_input_replayer.py tests are stdlib only.
TESTS=(
	analysis/test_analysis_modules.py
	bin/test_coord_version.py
	bin/test_coord_sessions.py
	harness/test_input_replayer.py
	harness/test_router_stack.py
	harness/test_router_telem.py
)

# Deliberately NOT here: every test that has a container of its own runs at that
# image's build time, in the environment it targets. The build failing is the
# test failing, so the coverage exists and is enforced -- and running these on a
# workstation would test the wrong machine.
#
#   containers/coordinator-mavlink/test_router.py   Dockerfile:37
#   containers/sh1106-display/test_display.py       Dockerfile:38
#   containers/campod-camera/test_capture_wait.py   Dockerfile:123
#   containers/campod-camera/accel  (go vet + go test)  Dockerfile:36
#
# The campod-camera invocation additionally passes --require-manifest, which
# checks the baked /etc/container-image; that assertion only exists in the
# image, so the copy that used to run here was the weaker one.

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
