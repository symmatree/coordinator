# overlay/ — whole-file shadows over the pinned upstream

A **bridge**, not a fork. To iterate on modified `VINS-Fusion` source without standing up a
fork or a patch system yet, drop **whole-file copies** here at their **exact path inside the
upstream repo**, under a subdir named for that repo. The Dockerfile clones the pinned upstream
(`upstream.lock` SHA), then `cp`s this tree over the checkout right before building.

```
overlay/
  VINS-Fusion/                          # = the cloned repo root
    vins_estimator/src/main.cpp         # shadow: replaces upstream's main.cpp
    vins_estimator/src/estimator/...    # add more shadows at their real paths
```

## How it works

The Dockerfile does (roughly):

```dockerfile
RUN git clone ... VINS-Fusion && cd VINS-Fusion && git checkout $SHA   # cached
COPY overlay/ /overlay/
RUN cd VINS-Fusion && cp -a /overlay/VINS-Fusion/. . && cmake ... && build
```

So a shadow file simply **overwrites** the same-named upstream file before the build. Nothing here
= a no-op (unchanged upstream build).

## Why whole files, not patches (for now)

- **Edit → build → test** with no patch-refresh dance; no `.rej` conflicts.
- Trivial to author and read: it's just the file.
- Tradeoff: a shadow **silently diverges** from upstream if upstream changes that file — fine while
  we pin the SHA, but see "what did we change" below. This is a deliberately temporary bridge; the
  real fork/patch decision is deferred.

## What did we change? (since a shadow hides the diff)

Diff a shadow against the pinned upstream file:

```sh
SHA=$(grep VINS_FUSION_SHA ../upstream.lock | cut -d= -f2)
curl -s "https://raw.githubusercontent.com/chobitsfan/VINS-Fusion/$SHA/vins_estimator/src/main.cpp" \
  | diff - VINS-Fusion/vins_estimator/src/main.cpp
```

## Build note (x86 for offline testing)

The build stage is `FROM debian:bookworm-slim`, which is multi-arch — building this Dockerfile on
an **x86** host yields an x86 `vins_fusion` (native, fast, no qemu), building on arm yields the Pi
binary. The overlay applies identically either way. (Same shadow files also work for a direct
native `cmake` build outside Docker, if faster iteration is wanted.)
