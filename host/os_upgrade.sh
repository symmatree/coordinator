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
cd "$(dirname "$0")"

# Same two problems one_time.sh had, and worse here: this script exists to install
# packages, so a read-only /usr stops it dead, and a dist-upgrade is far more
# likely than a fresh install to hit a debconf prompt (maintainer scripts asking
# about changed conffiles). DEBIAN_FRONTEND must come AFTER sudo or env_reset
# drops it before apt sees it.
# shellcheck source=host/lib/usr-rw.sh
. "$(pwd)/lib/usr-rw.sh"
usr_rw_begin

sudo apt-get update &&
	sudo DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y &&
	sudo DEBIAN_FRONTEND=noninteractive apt-get autoremove -y

usr_rw_request_reboot

if [[ -f /var/run/reboot-required ]]; then
	echo "os_upgrade: /var/run/reboot-required set (kernel/firmware/modules, or the /usr hatch)." >&2
	echo "os_upgrade: reboot, then run ./host/os_upgrade.sh again until it exits clean." >&2
	exit 1
fi

echo "os_upgrade: complete (no pending kernel/firmware reboot)."
