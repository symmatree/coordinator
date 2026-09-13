# shellcheck shell=bash
# Read-only fleet node probe. Piped to `bash -s` on the node by src/probe.ts.
#
# WRITES NOTHING. Every command here is a read: this may run against a node that is mid-flight
# or that someone else is working on. Output is `key<TAB>value` lines so the parser needs no
# quoting rules; repeated keys (stack, container, failed_unit) are collected into a list.
set -u

emit() { printf '%s\t%s\n' "$1" "${2-}"; }

emit hostname "$(hostname)"
emit arch "$(uname -m)"
# shellcheck source=/dev/null  # /etc/os-release exists on the node, not on the linter
emit os_codename "$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME-}")"

# /etc/fleet-image is written at image build time and is immutable: it describes the card this
# node was flashed from, not its current state. It is the only reason a probe can say
# "campod-sw is on a stale image, reflash needed" rather than reporting success on a fleet that
# is not at head -- config converges over SSH, but the image only changes by rewriting the card.
if [ -r /etc/fleet-image ]; then
	while IFS='=' read -r k v; do
		case "$k" in
		IMAGE | ROLE | SOURCE | BASE) emit "fleet_$k" "$v" ;;
		esac
	done </etc/fleet-image
fi

# The checkout is load-bearing: /opt/stacks/<role> is a symlink into it, so `git pull` IS the
# config deploy (coordinator#48). Its absence presents confusingly -- `coord` reports *no stack*
# rather than *no checkout* -- which is why it is probed explicitly.
CO="$HOME/coordinator"
if [ -d "$CO/.git" ]; then
	emit checkout_present 1
	emit checkout_head "$(git -C "$CO" rev-parse --short HEAD 2>/dev/null)"
	if [ -n "$(git -C "$CO" status --porcelain 2>/dev/null)" ]; then
		emit checkout_dirty 1
	else
		emit checkout_dirty 0
	fi
else
	emit checkout_present 0
fi

if [ -d /opt/stacks ]; then
	for d in /opt/stacks/*/; do
		[ -d "$d" ] || continue
		emit stack "$(basename "$d")"
	done
fi

if command -v coord >/dev/null 2>&1; then emit coord_present 1; else emit coord_present 0; fi

if command -v docker >/dev/null 2>&1; then
	emit docker_present 1
	# `docker ps` failing here is usually not "docker is broken" but "this login has no docker
	# group yet" -- ansible adds the group, and membership only lands in a NEW session.
	if ps_out=$(docker ps --format '{{.Names}}\t{{.Status}}' 2>/dev/null); then
		emit docker_group 1
		while IFS= read -r line; do
			[ -n "$line" ] && emit container "$line"
		done <<<"$ps_out"
	else
		emit docker_group 0
	fi
else
	emit docker_present 0
	emit docker_group 0
fi

systemctl --failed --no-legend --plain 2>/dev/null | while read -r u _; do
	[ -n "$u" ] && emit failed_unit "$u"
done

if [ -f /var/run/reboot-required ]; then emit reboot_required 1; else emit reboot_required 0; fi

# The data subvolume must be the btrfs @data mount, not a directory on @var. If it is the
# latter, captures land somewhere with none of the power-loss properties the subvolume exists
# for -- silently, with no error (docs/campod.md cross-repo tripwire).
for m in /var/lib/coordinator /var/lib/campod; do
	if [ -d "$m" ]; then
		emit data_mount "$m=$(findmnt -no FSTYPE,OPTIONS "$m" 2>/dev/null | head -1)"
	fi
done
