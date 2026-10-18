# fleet-control: converges the rekon10 fleet, by submitting a workflow that runs the play.
# Runs in the cluster, on linux/amd64.
#
# Local build:
#   docker build -t fleet-control:local -f containers/fleet-control/Dockerfile .
#
# The build context is the REPO ROOT rather than this directory, for the step scripts below.
#
# Carries ansible-core because the workflow step runs in this image. It does not carry the
# playbooks: the step fetches the published artifact at run time.

FROM node:24-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553 AS build
WORKDIR /app
COPY containers/fleet-control/package.json containers/fleet-control/package-lock.json ./
RUN npm ci
COPY containers/fleet-control/tsconfig.json ./
COPY containers/fleet-control/src ./src
RUN npm run build && npm prune --omit=dev

FROM node:24-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553
ENV NODE_ENV=production
WORKDIR /app

# ansible-core and ansible-runner, plus the ssh client ansible shells out to. pipx-less venv
# because Debian marks the system python externally-managed.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv openssh-client curl ca-certificates unzip \
 && python3 -m venv /opt/ansible \
 && /opt/ansible/bin/pip install --no-cache-dir ansible-core==2.18.6 ansible-runner==2.4.3 \
 && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/ansible/bin:${PATH}"

# kubectl, for the ground-side artifacts that live in the cluster rather than on a vehicle
# (src/cluster.ts). Shelling out for the same reason ansible and ssh are shelled out to: one
# credential path, and the automated steps are the documented manual ones. In-cluster it picks up
# the pod's ServiceAccount, which can read pods and exec in `mavproxy` and `ntrip` and nothing
# else (tiles#793). Pinned and checksummed -- it is fetched from the network at build time.
ARG KUBECTL_VERSION=v1.32.9
RUN set -eux; \
      arch="$(dpkg --print-architecture)"; \
      curl -fsSLo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${arch}/kubectl"; \
      curl -fsSLo /tmp/kubectl.sha256 \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${arch}/kubectl.sha256"; \
      echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c -; \
      rm /tmp/kubectl.sha256; \
      chmod 0755 /usr/local/bin/kubectl; \
      kubectl version --client=true

# Its own provenance, in the #326 manifest format the devices use, because a process cannot
# read its own image labels. GIT_SHA and GIT_REF come from .github/actions/build-container;
# both are empty for a local build, which the service reports as such rather than pretending.
ARG GIT_SHA=""
ARG GIT_REF=""
RUN printf '%s\n' \
      "# fleet-control build manifest, written by its Dockerfile." \
      "ORG_OPENCONTAINERS_IMAGE_SOURCE=\"https://github.com/symmatree/coordinator\"" \
      "ORG_OPENCONTAINERS_IMAGE_REVISION=\"${GIT_SHA}\"" \
      "FLEET_SOURCE_REF=\"${GIT_REF}\"" \
      "FLEET_UNIT=\"fleet-control\"" \
      > /etc/container-image

COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY containers/fleet-control/ui ./ui
# The Argo Workflow step's body (#504). In this image because it needs the same ansible and
# the same ssh client the server has, and because the step is this service's own code -- a
# change to it rolls this pod, which does NOT disturb a workflow already running, since that
# runs in a pod of its own. That is the whole point of the move.
COPY containers/fleet-control/step/run-playbook.sh /usr/local/bin/run-playbook.sh

# The bench simulated-flight cycle's step body (coordinator#541). Same reason as above: it
# needs this image's python and ssh client, and it drives this service's own API. The
# procedure it implements is declared as the `bench-simflight` WorkflowTemplate in tiles;
# this is the code those steps run, so the two version together.
COPY harness/bench_cycle.py /usr/local/bin/bench-cycle
COPY containers/fleet-control/package.json ./

USER node
EXPOSE 8080
CMD ["node", "dist/index.js"]
