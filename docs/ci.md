# CI shape

## One build workflow, filtered inside

`.github/workflows/build.yaml` builds every container image and firmware target
in the matrix, and has **no `paths:` filter**. That is deliberate and is the
whole reason the file is shaped the way it is.

A workflow skipped by path filtering never reports a check at all. A required
status check that never reports stays pending, and the pull request can never
merge -- so a path-filtered workflow cannot be a merge gate. GitHub says as
much: *"You should not use path or branch filtering to skip workflow runs if
the workflow is required to pass before merging."*

A **job** skipped by an `if:` conditional is different: it reports a conclusion
of `skipped`, which GitHub counts as a pass. So the filtering moved one level
down. `build.yaml` always runs; its `changes` job decides what actually needs
building, and the build jobs skip themselves when the answer is nothing.

## Where the component list lives

`.github/components.json` is the only place a component is declared. The
`changes` job derives two things from it:

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

## `build-ok`

`build-ok` is the required check. It is the only one, and it always runs.

Its `if: always()` is load-bearing. Under default `needs:` semantics a job is
skipped when a job it needs *fails* -- so without `always()`, `build-ok` would
be skipped exactly when a build broke, report `skipped`, and be counted as a
pass. A gate that goes green because the thing it guards failed is worse than
no gate. It runs unconditionally and inspects `needs.*.result` itself.

The same trap applies to anything else that becomes a required check here:
being reached is not the same as passing.

## Per-component checks

Checks that belong to one component live in its Dockerfile where they can --
`coordinator-mavlink` runs its router isolation test at image build time, so
the build failing *is* the test failing.

`fleet-control` is the exception: its TypeScript typecheck and tests need Node
rather than the image, so it has its own job. That job no longer gates the
build the way it did when it was a `needs:` predecessor. It does not need to:
the branch ruleset requires a pull request and `build-ok`, so code that fails
typecheck cannot reach `main` to be built from.
