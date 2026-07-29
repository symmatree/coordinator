#!/usr/bin/env bash
# Refresh the pinned base-image digests in containers/*/Dockerfile.
#
# This is the "shared pin" mechanism for the layer-cache fix (#145) WITHOUT the Renovate
# app: run it to bump every `FROM <image>:<tag>@sha256:...` pin (and pod-camera's
# `ARG BASE_IMAGE=`) to the current digest of its tag. A base image moving thus becomes a
# deliberate, reviewable commit -- not an implicit tag drift that re-pulls the whole image
# on the Pi. Run it by hand, or on a schedule via .github/workflows/update-base-digests.yaml.
#
# Refs are auto-discovered from the Dockerfiles (allowlisted registries / official images),
# so adding a new base -- e.g. the internal ghcr.io/.../coordinator-vio-*-base once an app
# image consumes it -- needs no edit here.
#
# Requires `docker buildx` (imagetools is a registry client; no running daemon needed).
set -euo pipefail
cd "$(dirname "$0")/.." # repo root

mapfile -t dockerfiles < <(find containers -name Dockerfile | sort)

# Collect candidate image refs from FROM and ARG BASE_IMAGE= lines, strip any existing
# @digest and trailing ` AS <stage>`, and keep only real images (official allowlist or a
# registry path with a '/'). Skips build-stage names and ${VARIABLE} refs.
declare -A refs=()
for f in "${dockerfiles[@]}"; do
	while IFS= read -r ref; do
		ref="${ref%% *}"                # drop ` AS builder`
		ref="${ref%@sha256:*}"          # drop existing @digest
		[[ $ref == *'$'* ]] && continue # ${VAR}
		if [[ $ref == */* || $ref =~ ^(debian|ubuntu|alpine|python):.+$ ]]; then
			refs["$ref"]=1
		fi
	done < <(grep -hoE '^(FROM |ARG BASE_IMAGE=)[^ ]+( AS [A-Za-z0-9_-]+)?' "$f" |
		sed -E 's/^FROM //; s/^ARG BASE_IMAGE=//')
done

changed=0
for ref in "${!refs[@]}"; do
	digest="$(docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}' 2>/dev/null || true)"
	if [[ $digest != sha256:* ]]; then
		echo "!! could not resolve a digest for ${ref} -- skipping" >&2
		continue
	fi
	echo "${ref} -> ${digest}"
	for f in "${dockerfiles[@]}"; do
		# Replace `ref` or `ref@sha256:<64hex>` with `ref@digest`, anchored so the tag is whole
		# (preceded by start/space/'=', followed by '@', space, or end-of-line).
		before="$(cat "$f")"
		perl -0pi -e "s/(^|[ =])\Q${ref}\E(?:\@sha256:[0-9a-f]{64})?(?=[ \n]|\$)/\${1}${ref}\@${digest}/mg" "$f"
		[[ "$(cat "$f")" != "$before" ]] && changed=1
	done
done

if [[ $changed -eq 1 ]]; then
	echo "base-image digest pins updated."
else
	echo "base-image digest pins already current."
fi
