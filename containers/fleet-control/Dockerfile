# fleet-control: converges the rekon10 fleet by running the Ansible playbook against it.
# Runs in the cluster, on linux/amd64.
#
# Local build:
#   docker build -t fleet-control:local -f containers/fleet-control/Dockerfile .
#
# NOTE the build context is the REPO ROOT, not this directory: the image carries
# host/ansible/** so the playbook ships with the service that runs it. Only the playbook comes
# from here; everything else it installs is copied from the target's own checkout (site.yaml).

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
      python3 python3-venv openssh-client \
 && python3 -m venv /opt/ansible \
 && /opt/ansible/bin/pip install --no-cache-dir ansible-core==2.18.6 ansible-runner==2.4.3 \
 && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/ansible/bin:${PATH}"

COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY containers/fleet-control/ui ./ui
COPY containers/fleet-control/package.json ./
COPY host/ansible /app/ansible

# The playbook runs local-connection on purpose so the same recipes work inside an image build
# (see site.yaml). Use that here: a syntax error fails CI rather than a provisioning run.
#
# ALL THREE the service can invoke, not just site.yaml. reimage.yaml and reboot.yaml were
# reachable from a button with nothing checking them, which is the case this guard exists for.
RUN ansible-playbook --syntax-check -i localhost, --connection=local \
      /app/ansible/site.yaml /app/ansible/reimage.yaml /app/ansible/reboot.yaml

USER node
EXPOSE 8080
CMD ["node", "dist/index.js"]
