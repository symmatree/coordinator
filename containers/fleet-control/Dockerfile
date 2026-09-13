# fleet-control: ground-station control surface for the rekon10 fleet.
#
# UNLIKE every other image in this repo, this one runs on the CLUSTER, not on the vehicle --
# so it is linux/amd64 (the tiles Talos nodes are amd64: tf/nodes/talos-iso.tf builds
# metal-amd64.iso), not the linux/arm64 the Pi images use. It lives here rather than in tiles
# because what it automates is this repo's operator workflow -- `host/one_time.sh`, `bin/coord`,
# the /opt/stacks symlink deploy -- and those change together.
#
# Local build:
#   docker build -t fleet-control:local containers/fleet-control

FROM node:22-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS build
WORKDIR /app
# Dependencies first so an app-only change does not re-resolve the tree (#145: the point of
# pinning and layer ordering is that the registry pull is small when only our code moved).
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src ./src
RUN npm run build && npm prune --omit=dev

FROM node:22-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5
ENV NODE_ENV=production
WORKDIR /app

# git is not needed here -- every git command this service runs happens ON the node, over SSH.
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY ui ./ui
COPY package.json ./
# A default inventory so the image runs standalone; the deployment mounts the real one over
# it at /config/inventory.json (FLEET_INVENTORY).
COPY inventory.json /config/inventory.json

# Runs unprivileged. The only credential it holds is the SSH key mounted at FLEET_SSH_KEY.
USER node
EXPOSE 8080
# /state must be a writable volume for host-key trust to survive a restart; the service logs a
# warning and reports `hostKeys.ephemeral` on /status if it is not.
ENV FLEET_HOSTKEYS=/state/hostkeys.json
CMD ["node", "dist/index.js"]
