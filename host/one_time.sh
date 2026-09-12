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

# /usr ships READ-ONLY on the fleet image, and everything below writes to it: apt
# for ansible/git, then docker-ce via the playbook. deployment-model.md already
# specifies a "remount,rw /usr wrapper for maintenance" as part of the convergence
# tier -- this is that wrapper. Without it the first apt-get install dies with
# "Read-only file system" partway through an unpack, leaving dpkg half-installed.
#
# Only fires when /usr really is its own read-only mount, so a dev box or any
# non-image install is untouched.
USR_REMOUNTED=false
if findmnt -n -o OPTIONS /usr 2>/dev/null | grep -qE '(^|,)ro(,|$)'; then
	echo "one_time: /usr is read-only; remounting rw for this run."
	sudo mount -o remount,rw /usr
	USR_REMOUNTED=true
fi

# Restore the invariant however we exit, including the reboot-required exit 1
# below. If it cannot be restored we say so rather than leaving a box that is
# quietly writable: coordinator#202 records remount,ro returning "busy" when
# anything holds a writable fd, so this is a real possibility, not a hypothetical.
restore_usr_ro() {
	if [[ ${USR_REMOUNTED} == true ]]; then
		if sudo mount -o remount,ro /usr; then
			echo "one_time: /usr restored to read-only."
		else
			echo "one_time: WARNING /usr could not be restored to read-only and stays writable" >&2
			echo "one_time: until the next reboot (coordinator#202's 'busy' case)." >&2
		fi
	fi
}
trap restore_usr_ro EXIT

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

if [[ -f /var/run/reboot-required ]]; then
	echo "one_time: /var/run/reboot-required still set (kernel/firmware/modules)." >&2
	echo "one_time: run ./host/one_time.sh ${DEVICE_ROLE} again after the host is back." >&2
	exit 1
fi

echo "one_time: complete (${DEVICE_ROLE}, no pending kernel/firmware reboot)."
