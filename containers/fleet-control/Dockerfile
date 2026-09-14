# fleet-control: sets up and updates the rekon10 fleet over SSH. Runs in the cluster, on
# linux/amd64.
#
# Local build:
#   docker build -t fleet-control:local containers/fleet-control

FROM node:24-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src ./src
RUN npm run build && npm prune --omit=dev

FROM node:24-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553
ENV NODE_ENV=production
WORKDIR /app
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY ui ./ui
COPY package.json ./

USER node
EXPOSE 8080
CMD ["node", "dist/index.js"]
