#!/usr/bin/env bash
# Run one playbook against one node, from a published artifact, in this pod.
#
#   run-playbook.sh <playbook> <host> <device_role>
#
# The body of an Argo Workflow step (coordinator#504). It exists so that a converge is not a
# subprocess of the fleet-control server: a pod roll killed any run in flight, and every
# concurrent run was a ~60 MB ansible interpreter inside one 256 MB cgroup.
#
# WHAT IT DOES NOT DO. It does not choose a version, resolve an artifact, or decide which
# node to converge. Those are the submitter's, and arrive as arguments and environment, so
# one place knows how to talk to GitHub rather than two.
#
# THE OUTPUT IS WHAT ANSIBLE PRINTS. No `-j`, no renderer. The old server parsed runner's
# JSON event stream to print its own `TASK ...` lines; that was never asked for, and the log
# should be what you get running the play yourself, tuned at the playbook. Alloy collects
# this pod's stdout into Loki, so that is the durable record.
#
# Required environment:
#   PLAYBOOK_ARTIFACT_URL  the artifact zip to fetch (GitHub Actions API)
#   PLAYBOOK_REVISION      the commit the artifact must carry
#   FLEET_GITHUB_TOKEN     credential for that fetch -- listing is public, download is not
#   FLEET_SSH_KEY          path to the mounted private key
#   FLEET_SSH_PUBLIC_KEY   its public half, for the play to install in authorized_keys
#   FLEET_KNOWN_HOSTS      path to the shared recorded host keys
# Optional:
#   FLEET_SSH_USER         login account on the device (default pi)
#   FLEET_SSH_TIMEOUT_SEC  ansible's connect timeout (default 90)
set -euo pipefail

playbook=${1:?usage: run-playbook.sh <playbook> <host> <device_role>}
host=${2:?usage: run-playbook.sh <playbook> <host> <device_role>}
device_role=${3:?usage: run-playbook.sh <playbook> <host> <device_role>}

: "${PLAYBOOK_ARTIFACT_URL:?PLAYBOOK_ARTIFACT_URL is required}"
: "${PLAYBOOK_REVISION:?PLAYBOOK_REVISION is required}"
: "${FLEET_GITHUB_TOKEN:?FLEET_GITHUB_TOKEN is required -- the artifact download needs it}"
: "${FLEET_SSH_KEY:?FLEET_SSH_KEY is required}"
: "${FLEET_SSH_PUBLIC_KEY:?FLEET_SSH_PUBLIC_KEY is required}"
: "${FLEET_KNOWN_HOSTS:?FLEET_KNOWN_HOSTS is required}"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "[step] fetching the playbook artifact for ${PLAYBOOK_REVISION}"
curl -fsSL --retry 3 --retry-delay 2 \
	-H "authorization: Bearer ${FLEET_GITHUB_TOKEN}" \
	-H "accept: application/vnd.github+json" \
	-o "$work/artifact.zip" \
	"$PLAYBOOK_ARTIFACT_URL"

# The zip holds the tarball; the tarball holds `ansible/`.
unzip -q "$work/artifact.zip" -d "$work/zip"
tarball=$(find "$work/zip" -name 'ansible-*.tar.gz' -print -quit)
[[ -n $tarball ]] || {
	echo "[step] no ansible-*.tar.gz inside the artifact" >&2
	exit 1
}
tar -C "$work" -xzf "$tarball"

# WHAT WE GOT, NOT WHAT WE ASKED FOR. The point of versioning the artifact is that a run can
# say which playbook it ran, and a run reporting the version it REQUESTED has only restated
# its own input. So this reads the archive's own VERSION and refuses a mismatch: the artifact
# is fetched by an id resolved elsewhere, and an id that resolves to the wrong commit is
# exactly the failure that would otherwise be silent and permanent in the record.
# shellcheck disable=SC1091
. "$work/ansible/VERSION"
got=${ORG_OPENCONTAINERS_IMAGE_REVISION:-}
if [[ $got != "$PLAYBOOK_REVISION" ]]; then
	echo "[step] artifact carries ${got:-<none>}, expected ${PLAYBOOK_REVISION}" >&2
	exit 1
fi
# The line the run record takes the version from, and a reader reads.
echo "[step] playbook ${got} (${FLEET_SOURCE_REF:-<no ref>})"

# HOST KEYS ARE CHECKED, as they were when the server ran the play. `accept-new` records an
# unknown host and still refuses a CHANGED one -- which is only true if the recorded keys
# persist, hence a shared FLEET_KNOWN_HOSTS rather than a fresh file per step. ControlMaster
# because a converge makes many connections to one slow box.
export ANSIBLE_HOST_KEY_CHECKING=True
export ANSIBLE_SSH_ARGS="-o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${FLEET_KNOWN_HOSTS}"
# A Zero under a converge misses ansible's 10 s default; 90 is the value the handshake
# failures on campod-se argued for.
export ANSIBLE_TIMEOUT=${FLEET_SSH_TIMEOUT_SEC:-90}

# `--private-key` is deliberately not used: the inventory variable is the supported route and
# is what the server passed, so the two invocations stay the same one.
#
# fleet_ssh_public_key IS PASSED IN, from the same 1Password item that holds the private
# half. Terraform generates the keypair and writes both fields (tf/nodes/fleet-ssh-key.tf in
# tiles), so the declared public key and the mounted private key are two fields of one
# object, written together by one writer.
#
# This used to be derived at run time, on the reasoning that a declared copy was a second
# store that could drift -- and it had drifted. That diagnosis was wrong. The disagreement
# came from TWO 1Password items sharing the title `fleet-ssh-key-managed`, written by two
# terraform workspaces into one vault, so a name lookup was ambiguous. One writer, gated to
# prod, fixed it at the cause. Deriving at run time was a workaround for a collision, not a
# better way to know a public key.
extra=$(
	python3 - "$device_role" "${FLEET_SSH_USER:-pi}" "$FLEET_SSH_KEY" "$FLEET_SSH_PUBLIC_KEY" <<'PY'
import json, sys
role, user, key, pub = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
print(json.dumps({
    "ansible_user": user,
    "ansible_ssh_private_key_file": key,
    "fleet_ssh_public_key": pub.strip(),
    "device_role": role,
}))
PY
)

echo "[step] ${playbook} on ${host} as ${device_role}"
cd "$work/ansible"
exec ansible-playbook "$playbook" -i "${host}," -e "$extra"
