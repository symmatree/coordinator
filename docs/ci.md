# CI shape

## Two build workflows, filtered inside

`.github/workflows/build-containers.yaml` and `build-firmware.yaml` each build
one family of artifact, and neither has a **`paths:` filter**. That is
deliberate and is the reason both files are shaped the way they are.

A workflow skipped by path filtering never reports a check at all. A required
status check that never reports stays pending, and the pull request can never
merge -- so a path-filtered workflow cannot be a merge gate. GitHub says as
much: *"You should not use path or branch filtering to skip workflow runs if
the workflow is required to pass before merging."*

A **job** skipped by an `if:` conditional is different: it reports a conclusion
of `skipped`, which GitHub counts as a pass. So the filtering moved one level
down. Both workflows always run; each one's `changes` job decides what actually
needs building, and the build jobs skip themselves when the answer is nothing.

They are two workflows rather than one because a merge can require more than
one check, and containers and firmware failing are different news. `containers-ok`
skipped next to `firmware-ok` red says which half is broken without opening
anything.

## Where the component list lives

`.github/components.json` is the only place a component is declared, for both
families. `.github/actions/resolve-components` reads one family out of it and
derives two things:

- the filter spec handed to `dorny/paths-filter` -- JSON is valid YAML, so the
  paths go straight across with no template
- the matrix of components whose paths actually changed, assembled by looking
  each matched name back up in the same file

Deriving both from one file is the point: a component cannot be declared in a
filter and forgotten in a matrix, because there is only one declaration.

The matrix is built as a whole list rather than with `include:`. An `include`
entry that matches no existing combination does not get dropped -- GitHub
creates a *new* combination for it, which would quietly build every component
on every run regardless of what changed.

Each component watches its family's `machinery` paths as well as its own, so a
change to a shared action or a workflow rebuilds everything it could affect.

## The `-ok` gates

`containers-ok` and `firmware-ok` are the required checks. They always run.

Their `if: always()` is load-bearing. Under default `needs:` semantics a job is
skipped when a job it needs *fails* -- so without `always()`, the gate would be
skipped exactly when a build broke, report `skipped`, and be counted as a pass.
A gate that goes green because the thing it guards failed is worse than no
gate. They run unconditionally and inspect `needs.*.result` themselves.

The same trap applies to anything else that becomes a required check here:
being reached is not the same as passing.

## Where the Python tests run

Every test that has a container of its own runs at that image's build time, in
the environment it targets:

| test | gate |
|---|---|
| `containers/coordinator-mavlink/test_router.py` | `Dockerfile:37` |
| `containers/sh1106-display/test_display.py` | `Dockerfile:38` |
| `containers/campod-camera/test_capture_wait.py` | `Dockerfile:123` |
| `containers/campod-camera/accel` (`go vet`, `go test`) | `Dockerfile:36` |

The rest have no container to be tested inside. `analysis/` is workstation
tooling and `harness/` is bench tooling, neither of which ships to a device,
and `bin/coord` is the host CLI that drives compose. `tests.yaml` runs those in
the JupyterHub notebook image, which is where that code is run -- so its
pillow, scipy, numpy and pymavlink are the versions a person gets, and there is
no dependency list here to drift from it.

## Per-component checks

Checks that belong to one component live in its Dockerfile where they can --
`coordinator-mavlink` runs its router isolation test at image build time, so
the build failing *is* the test failing.

`fleet-control` is the exception: its TypeScript typecheck and tests need Node
rather than the image, so it has its own job. That job no longer gates the
build the way it did when it was a `needs:` predecessor. It does not need to:
the branch ruleset requires a pull request and `containers-ok`, so code that
fails typecheck cannot reach `main` to be built from.

## Not in the matrix

The `vio-*` images still have a workflow each, still path-filtered, and so are
still outside the gate. They are unbuilt pending the Trixie decision.
