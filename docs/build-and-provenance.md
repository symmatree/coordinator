# What builds what, and how an artifact says where it came from

Two things are built from source and end up on a device: **container images** and the
**disk image**. Both are built by GitHub Actions, and both carry enough metadata to answer
"which commit is this, and is it still current" without anyone writing it down by hand.

This doc exists because the label vocabulary looks like a free choice and is not. If you are
about to add or rename one, read [Load-bearing labels](#load-bearing-labels) first.

## Container images

`.github/workflows/build-<name>.yaml`, one per image, all the same shape:

1. `docker/metadata-action@v5` computes the tags and the standard OCI labels.
2. `docker/build-push-action@v6` builds for `linux/arm64` and pushes to GHCR, passing the
   metadata action's `labels` output plus our own.

**Tags** come from the metadata action's `tags:` block:

| tag | when | what it means |
|---|---|---|
| `main` | default branch only | the moving tag every device tracks |
| `sha-<short>` | always | one immutable build |
| `<branch>` | branch push | that branch's newest build |
| `<tag>` | tag push | a released build |

`main` is the one that matters operationally: `stacks/*/compose.yaml` reference
`ghcr.io/symmatree/coordinator-<name>:main`, so a device gets whatever `main` points at when
it pulls. Nothing is digest-pinned on the device side -- see
[deployment-model.md](deployment-model.md).

## The disk image

Built in `dotfiles-symm`, not here: `.github/workflows/build-pi-image.yaml` runs
`pi-image/build-image.sh` once per role and uploads the `.img` as a workflow artifact.

Two consequences worth knowing:

- **Artifacts expire** (`retention-days` in that workflow). An image older than the window
  cannot be re-fetched; rebuilding it means checking out the git sha and running the build
  again, which reproduces the *source tree* rather than the bytes -- the build `apt-get`s
  against live archives with no snapshot pin.
- **Downloading one needs a credential.** Listing runs and artifacts is public for a public
  repo; `GET /actions/artifacts/:id/zip` answers `401` without a token. This is why
  `containers/fleet-control` needs `FLEET_GITHUB_TOKEN` for image fetches and nothing else.

The build writes `/etc/fleet-image` into the rootfs, which is the disk image's equivalent of a
container's labels.

## Load-bearing labels

Four keys are read by software. Everything else an artifact carries is reported and displayed
but never acted on -- see [#326](https://github.com/symmatree/coordinator/issues/326).

| key | source | meaning |
|---|---|---|
| `ORG_OPENCONTAINERS_IMAGE_SOURCE` | OCI | URL of the repo it was built from |
| `ORG_OPENCONTAINERS_IMAGE_REVISION` | OCI | the commit sha |
| `ORG_OPENCONTAINERS_IMAGE_VERSION` | OCI | version of the packaged software |
| `FLEET_SOURCE_REF` | **ours** | the fully-qualified git ref, e.g. `refs/heads/main` |

Labels are named in OCI form (`org.opencontainers.image.revision`, `fleet.source.ref`); the
probe uppercases them and turns non-alphanumerics into underscores, so the label and the key
are the same fact in two spellings.

### Why `FLEET_SOURCE_REF` is ours and not `org.opencontainers.image.ref.name`

We used `ref.name` first. It is the wrong key, and the spec says so:

> **`org.opencontainers.image.ref.name`** Name of the reference for a target (string).
> SHOULD only be considered valid when on descriptors on `index.json` within image layout.

-- [OCI image-spec annotations](https://github.com/opencontainers/image-spec/blob/main/annotations.md)

That is a **scoping restriction**. `ref.name` names a reference inside an OCI image layout --
the thing that points at a manifest in a layout directory. It is not a source-control
annotation and says nothing about branches. It reads like "the name of a ref", which is how we
got it wrong.

`version` is defined as *"version of the packaged software"*, may match a repo label or tag,
and may be semver. `docker/metadata-action` fills it from the branch on a branch build, so it
carries `main` today -- but that is a coincidence of how branch builds are versioned, not a
promise. The day anything is released as `1.2.3`, `version` becomes `1.2.3` and stops naming a
ref at all.

**So OCI has no annotation defined as "the ref this was built from", and we need one** -- it is
what "is this commit still the head of what it tracks" resolves against. Rather than borrow an
OCI key outside its meaning, we declare our own under our own prefix.

### Why it is fully qualified

`refs/heads/main` and `refs/tags/v1.2.3`, not `main` and `v1.2.3`:

- **Branch or tag is in the string**, so nothing sniffs at a bare name to decide which it is.
- **A tag's name need not match its version.** A release is `version=1.2.3` and
  `refs/tags/v1.2.3`; neither can be derived from the other without guessing about a `v`.
- **GitHub's API takes it verbatim.** `GET /repos/{owner}/{repo}/commits/refs/heads/main` and
  `.../commits/refs/tags/v2.1.0` both resolve, so nothing has to be reassembled.

`${{ github.ref }}` is already exactly this string, in both build systems.

## Related

- [#326](https://github.com/symmatree/coordinator/issues/326) -- the vocabulary and what reads it
- [deployment-model.md](deployment-model.md) -- why devices track a moving tag
- `bin/coord-version` -- the probe that reports these on a device
- `containers/fleet-control` -- what compares them against the ref
