#!/usr/bin/env bash
# One-time Rekon host bootstrap: install Ansible + minimal deps, then run the shared playbook
# to converge config. Run from a coordinator checkout after flash + first boot.
#   ./host/one_time.sh              # coordinator (Pi 4B), default
#   ./host/one_time.sh coordinator  # same, explicit
#   ./host/one_time.sh pod          # pod (Pi Zero 2 W)
# This is a CONFIG deploy -- it does NOT `apt dist-upgrade` (that dragged a full OS upgrade +
# reboot into every deploy). The OS version is a property of the flashed image (#96); to move
# it forward in place, run ./host/os_upgrade.sh deliberately (#48). Ansible may still reboot for
# a kernel/firmware/module change it installs -- repeat until this completes without a reboot.
# Docs: docs/host-setup.md (coordinator), docs/pi-zero-host-setup.md (pod).
set -euo pipefail
cd "$(dirname "$0")"
SAVE_DIR=$(pwd)

DEVICE_ROLE="${1:-coordinator}"
case "${DEVICE_ROLE}" in
coordinator | pod) ;;
*)
	echo "one_time: unknown device role '${DEVICE_ROLE}' (expected: coordinator | pod)" >&2
	exit 2
	;;
esac

sudo apt-get update &&
	DEBIAN_FRONTEND=noninteractive sudo apt-get install -y \
		--no-install-recommends \
		ansible \
		ca-certificates \
		curl \
		git \
		sudo

ansible-playbook -v "$SAVE_DIR/ansible/site.yaml" \
	-i "localhost," --connection=local \
	-e "device_role=${DEVICE_ROLE}" \
	-e sync_repo=true

if [[ -f /var/run/reboot-required ]]; then
	echo "one_time: /var/run/reboot-required still set (kernel/firmware/modules)." >&2
	echo "one_time: run ./host/one_time.sh ${DEVICE_ROLE} again after the host is back." >&2
	exit 1
fi

echo "one_time: complete (${DEVICE_ROLE}, no pending kernel/firmware reboot)."
