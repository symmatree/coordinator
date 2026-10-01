# fleet-control: converges the rekon10 fleet by running the Ansible playbook against it.
# Runs in the cluster, on linux/amd64.
#
# Local build:
#   docker build -t fleet-control:local -f containers/fleet-control/Dockerfile .
#
# NOTE the build context is the REPO ROOT, not this directory: the image carries
# host/ansible/** so the playbook ships with the service that runs it. Only the playbook comes
# from here; everything else it installs is copied from the target's own checkout (site.yaml).

FROM node:24-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS build
WORKDIR /app
COPY containers/fleet-control/package.json containers/fleet-control/package-lock.json ./
RUN npm ci
COPY containers/fleet-control/tsconfig.json ./
COPY containers/fleet-control/src ./src
RUN npm run build && npm prune --omit=dev

FROM node:24-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6
ENV NODE_ENV=production
WORKDIR /app

# ansible-core and ansible-runner, plus the ssh client ansible shells out to. pipx-less venv
# because Debian marks the system python externally-managed.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv openssh-client curl ca-certificates \
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
COPY containers/fleet-control/package.json ./
COPY host/ansible /app/ansible

# The playbook runs local-connection on purpose so the same recipes work inside an image build
# (see site.yaml). Use that here: a syntax error fails CI rather than a provisioning run.
#
# BOTH the service can invoke, not just site.yaml. reimage.yaml was reachable from a button
# with nothing checking it, which is the case this guard exists for.
RUN ansible-playbook --syntax-check -i localhost, --connection=local \
      /app/ansible/site.yaml /app/ansible/reimage.yaml

USER node
EXPOSE 8080
CMD ["node", "dist/index.js"]
