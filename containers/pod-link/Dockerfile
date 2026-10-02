# coordinator-pod-link: carries capture intent from the coordinator to the campods.
#
# ONE IMAGE, BOTH ROLES. The coordinator runs it as publisher and each campod as
# subscriber, selected by POD_LINK_ROLE. Two images would be two things to keep in
# step for one wire protocol.
#
# Go, and static, because the campod is the constraint: 417 MiB with no swap,
# where a resident process costs its RSS once and an invoked binary costs a
# page-cache eviction every time.

ARG BASE_IMAGE=debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132

# --platform=$BUILDPLATFORM so the compile and the tests run natively on the
# builder rather than under QEMU; only the output is arm64. Same shape as the
# accel reader's stage in campod-camera.
FROM --platform=$BUILDPLATFORM golang:1.26-trixie@sha256:771f3162219f7a66bb6be3fea1ba31f418932946a99ad9eac26ffd555dfcf393 AS build
WORKDIR /src
COPY go.mod ./
# NOTE: no go.sum is committed, so this resolves and records dependencies at build
# time. That is weaker than a pinned sum -- it means the build is not reproducible
# against a moved upstream -- and it is here because the author had no Go toolchain
# to generate one. The first build on a machine with Go should commit the go.sum it
# produces and this line becomes `go mod download`.
RUN go mod tidy
COPY *.go ./
# Hardware-free and broker-free: the tests cover the gate's failure direction
# (absent and unreadable must mean paused, matching capture.py), the atomic write,
# role validation, and that status topics are per node. A broken gate fails the
# build rather than a flight.
RUN go vet ./... && go test ./...
ARG TARGETARCH
ARG GIT_SHA=""
RUN CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH} \
      go build -trimpath -ldflags="-s -w -X main.buildSHA=${GIT_SHA}" -o /out/pod-link .

FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive
# dumb-init because `coord stop` and the ansible quiesce both select container
# inits with `pkill -x dumb-init`. An image that skips it is an image the fleet's
# one stop mechanism cannot stop.
RUN apt-get update && apt-get install -y --no-install-recommends dumb-init \
  && rm -rf /var/lib/apt/lists/*

ARG GIT_SHA=""
ARG GIT_REF=""
RUN printf '%s\n' \
      "# pod-link build manifest, written by its Dockerfile." \
      "ORG_OPENCONTAINERS_IMAGE_SOURCE=\"https://github.com/symmatree/coordinator\"" \
      "ORG_OPENCONTAINERS_IMAGE_REVISION=\"${GIT_SHA}\"" \
      "FLEET_SOURCE_REF=\"${GIT_REF}\"" \
      "FLEET_UNIT=\"pod-link\"" \
      > /etc/container-image

COPY --from=build /out/pod-link /usr/local/bin/pod-link
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/usr/local/bin/pod-link"]
