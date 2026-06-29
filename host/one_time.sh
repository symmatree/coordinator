#!/usr/bin/env bash
# One-time Rekon host bootstrap: apt packages, then the shared Ansible playbook.
# Run from a coordinator checkout after flash + first boot.
#   ./host/one_time.sh              # coordinator (Pi 4B), default
#   ./host/one_time.sh coordinator  # same, explicit
#   ./host/one_time.sh pod          # pod (Pi Zero 2 W)
# Repeat until the script completes without a kernel/firmware reboot.
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
	sudo apt-get dist-upgrade -y &&
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
