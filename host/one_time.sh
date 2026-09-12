#!/usr/bin/env bash
# One-time Rekon host bootstrap: install Ansible + minimal deps, then run the shared playbook
# to converge config. Run from a coordinator checkout after flash + first boot.
#   ./host/one_time.sh              # coordinator (Pi 4B), default
#   ./host/one_time.sh coordinator  # same, explicit
#   ./host/one_time.sh campod       # campod (Pi Zero 2 W)
# This is a CONFIG deploy -- it does NOT `apt dist-upgrade` (that dragged a full OS upgrade +
# reboot into every deploy). The OS version is a property of the flashed image (#96); to move
# it forward in place, run ./host/os_upgrade.sh deliberately (#48). Ansible may still reboot for
# a kernel/firmware/module change it installs -- repeat until this completes without a reboot.
# Docs: docs/host-setup.md (coordinator), docs/campod.md (campod).
set -euo pipefail
cd "$(dirname "$0")"
SAVE_DIR=$(pwd)

DEVICE_ROLE="${1:-coordinator}"
case "${DEVICE_ROLE}" in
coordinator | campod) ;;
*)
	echo "one_time: unknown device role '${DEVICE_ROLE}' (expected: coordinator | campod)" >&2
	exit 2
	;;
esac

# /usr is read-only on the fleet image and the apt calls below write to it.
# Shared with os_upgrade.sh, which has the same need. See host/lib/usr-rw.sh.
# shellcheck source=host/lib/usr-rw.sh
. "${SAVE_DIR}/lib/usr-rw.sh"
usr_rw_begin

# DEBIAN_FRONTEND must come AFTER sudo. Written before sudo it is set for sudo's
# own environment, and sudo's default env_reset drops it, so apt never sees it --
# which is why a headless run printed the debconf Dialog/Readline/Teletype
# fallback chain and "dpkg-preconfigure: unable to re-open stdin:". Harmless in
# itself, but it is exactly the noise that makes a non-interactive push look
# half-broken, which matters for #236.
#
# The eight Dockerfiles under containers/ all set this correctly as an ARG; this
# script was the only place in the tree that got it wrong. No TZ= is needed here
# (unlike a fresh container rootfs, where tzdata's area prompt does block): the
# image has tzdata configured already -- America/New_York, /etc/localtime linked.
sudo apt-get update &&
	sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
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

# If the hatch was opened, ask for the reboot that closes it again.
usr_rw_request_reboot

if [[ -f /var/run/reboot-required ]]; then
	echo "one_time: /var/run/reboot-required set (kernel/firmware/modules, or the /usr hatch)." >&2
	echo "one_time: run ./host/one_time.sh ${DEVICE_ROLE} again after the host is back." >&2
	exit 1
fi

echo "one_time: complete (${DEVICE_ROLE}, no pending kernel/firmware reboot)."
