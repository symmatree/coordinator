#!/usr/bin/env bash
# One-time coordinator Pi host bootstrap: apt packages, then Ansible playbook.
# Run from a coordinator checkout after flash + first boot (see docs/host-setup.md).
set -euo pipefail
cd "$(dirname "$0")"
SAVE_DIR=$(pwd)

sudo apt-get update &&
	sudo apt-get dist-upgrade -y &&
	DEBIAN_FRONTEND=noninteractive sudo apt-get install -y \
		--no-install-recommends \
		ansible \
		ca-certificates \
		curl \
		git \
		sudo

ansible-playbook -v "$SAVE_DIR/ansible/install-coordinator.yaml" \
	-i "localhost," --connection=local \
	-e coordinator_sync_repo=true
