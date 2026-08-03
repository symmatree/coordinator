#!/usr/bin/env bash
# Flash the patched ExpressLRS TX backpack over its WiFi web UI.
#
# Two-phase (single client, backpack only reachable on its own AP, no dual-homed jump box):
#   1) on house WiFi:   ./flash.sh fetch
#   2) switch your client to the "ExpressLRS TX Backpack" AP, then run the line it printed:
#      ./flash.sh flash 10.0.0.1 /tmp/backpack-firmware/<file>.bin
#
# One-shot (only if you can reach both at once -- backpack on house WiFi, or a dual-homed box):
#   ./flash.sh auto 10.0.0.1
#
# `fetch` needs internet + gh and never touches the backpack; `flash` never touches the
# internet. The backpack's own /update target-check gates a wrong image (returns "mismatch"
# and does NOT flash) -- this script surfaces that and never auto-forces.
set -euo pipefail

FWDIR="/tmp/backpack-firmware" # persistent: survives the network switch, not auto-cleaned
BINPATH=""

do_download() {
	mkdir -p "$FWDIR"
	rm -f "$FWDIR"/*.bin
	echo ">> fetching latest backpack-firmware from main (needs internet + gh)..." >&2
	local rid
	rid="$(gh run list -R symmatree/coordinator --workflow build-backpack.yaml \
		--branch main --status success --limit 1 --json databaseId --jq '.[0].databaseId')"
	[[ -n $rid ]] || {
		echo "!! no successful build-backpack run found" >&2
		exit 1
	}
	gh run download -R symmatree/coordinator "$rid" -n backpack-firmware -D "$FWDIR" >&2
	BINPATH="$(find "$FWDIR" -maxdepth 1 -name '*.bin' -print -quit)"
	[[ -f $BINPATH ]] || {
		echo "!! no .bin downloaded" >&2
		exit 1
	}
	echo ">> saved: $BINPATH  (sha256 $(sha256sum "$BINPATH" | cut -d' ' -f1))" >&2
}

do_flash() {
	local ip="$1" bin="$2" base="http://$1"
	[[ -f $bin ]] || {
		echo "!! firmware not found: '$bin'"
		exit 1
	}
	echo ">> firmware: $bin ($(sha256sum "$bin" | cut -d' ' -f1))"
	echo ">> verifying $ip is an ELRS backpack in WiFi mode..."
	local cfg
	cfg="$(curl -fsS -m 5 "$base/config")" || {
		echo "!! $base/config did not respond -- not reachable, or not in WiFi mode"
		exit 1
	}
	python3 - "$cfg" <<'PY' || {
import sys, json
c = json.loads(sys.argv[1])["config"]      # ELRS backpack /config signature
assert "mode" in c
print(f"   ok: product={c.get('product_name') or '?'}  mode={c['mode']}  home-ssid={c.get('ssid') or '(unset)'}")
PY
		echo "!! $ip responded but isn't an ELRS backpack -- aborting"
		exit 1
	}
	echo ">> flashing -- do NOT power off until the LED comes back..."
	local resp
	resp="$(curl -fsS -m 180 -F "file=@$bin" "$base/update")" || {
		echo "!! upload/connection failed"
		exit 1
	}
	echo "   response: $resp"
	case "$resp" in
	*'"status": "ok"'*) echo ">> OK -- update accepted; backpack rebooting." ;;
	*'"status": "mismatch"'*)
		echo "!! MISMATCH -- wrong target, NOT flashed (bake the boxer target into the build; don't force)."
		exit 1
		;;
	*'"status": "error"'*)
		echo "!! updater error (see response above)."
		exit 1
		;;
	*)
		echo "!! unexpected response -- treat as failed."
		exit 1
		;;
	esac
}

case "${1:-}" in
fetch)
	do_download
	echo ">> now switch your client to the backpack AP, then run:"
	echo "     $0 flash 10.0.0.1 $BINPATH"
	;;
flash)
	[[ $# -eq 3 ]] || {
		echo "usage: $0 flash <IP> <FIRMWARE.bin>" >&2
		exit 2
	}
	do_flash "$2" "$3"
	;;
auto)
	do_download
	do_flash "${2:-10.0.0.1}" "$BINPATH"
	;;
*)
	echo "usage: $0 {fetch | flash <IP> <BIN> | auto [IP]}" >&2
	exit 2
	;;
esac
