# Shared /usr read-write hatch for the Rekon host scripts.
#
# SOURCED, never executed -- hence no shebang and no +x. shellcheck is told the
# dialect explicitly because it cannot infer it without one.
# shellcheck shell=bash
#
# /usr ships READ-ONLY on the fleet image. That is right for essentially the whole
# life of a card: the device runs off the avionics 5 V rail where every shutdown is
# a yank, and an unwritable /usr cannot be corrupted mid-cut. Installing packages
# is the rare exception -- first bring-up, a role gaining a package, a deliberate
# OS upgrade -- and deployment-model.md calls the answer a "remount,rw /usr wrapper
# for maintenance". This is that wrapper, in one place because two scripts need it.
#
# Why bash and not an ansible task: on a first run this has to happen BEFORE
# `apt-get install ansible`, so ansible cannot be the thing that makes its own
# installation possible. (If the control node ever moves off the device, that
# constraint goes away -- see #253.)

# Remount /usr rw if, and only if, it is currently its own read-only mount. A dev
# box or any non-image install has nothing to do here.
usr_rw_begin() {
	USR_REMOUNTED=false
	if findmnt -n -o OPTIONS /usr 2>/dev/null | grep -qE '(^|,)ro(,|$)'; then
		echo "${0##*/}: /usr is read-only; remounting rw for this run."
		sudo mount -o remount,rw /usr
		USR_REMOUNTED=true
	fi
}

# Ask for a reboot rather than attempting to put /usr back.
#
# `mount -o remount,ro /usr` CANNOT succeed on a running system -- measured on the
# coordinator, not assumed:
#
#     mount: /usr: mount point is busy.
#     remount,ro exit=32
#
# and /usr stayed rw with writes still succeeding. A reboot restores it cleanly
# (verified: /usr comes back ro,noatime,...,subvol=/@usr and refuses writes), so
# the honest move is to set the marker the caller already watches for. one_time.sh
# then exits non-zero with "reboot, then run again", which is a signal both a human
# and #236's service already act on. On the re-run every package is present, apt
# does nothing, and this hatch never opens.
usr_rw_request_reboot() {
	if [[ ${USR_REMOUNTED:-false} == true ]]; then
		sudo touch /var/run/reboot-required
		echo "${0##*/}: /usr was remounted rw and cannot be set back while running;"
		echo "${0##*/}: flagged reboot-required so a reboot restores it."
	fi
}
