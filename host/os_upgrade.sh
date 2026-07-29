#!/usr/bin/env bash
# Deliberate in-place OS upgrade for a Rekon host (apt dist-upgrade), split out of one_time.sh
# so a routine config deploy does NOT drag a full OS upgrade + reboot (#48). Run this only when
# you actually intend to move the OS forward.
#
# In the appliance model the OS version is normally a property of the flashed image (#96); a
# reflash is the primary way to update it. This script is the in-place alternative for a box you
# don't want to reflash yet. Repeat until it exits without a pending kernel/firmware reboot.
#
#   ./host/os_upgrade.sh
#
# Docs: docs/host-setup.md, docs/deployment-model.md.
set -euo pipefail

sudo apt-get update &&
	sudo apt-get dist-upgrade -y &&
	sudo apt-get autoremove -y

if [[ -f /var/run/reboot-required ]]; then
	echo "os_upgrade: /var/run/reboot-required set (kernel/firmware/modules)." >&2
	echo "os_upgrade: reboot, then run ./host/os_upgrade.sh again until it exits clean." >&2
	exit 1
fi

echo "os_upgrade: complete (no pending kernel/firmware reboot)."
