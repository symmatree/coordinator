# capture-common

The capture control loop and the campod conventions, in C++ with no camera library: shared
source for each capture app, built into it with `add_subdirectory`. Used by
[`oak-capture`](../oak-capture/) and the campods' [`campod-capture`](../campod-camera/).

| File | What |
|------|------|
| `session.{hpp,cpp}` | `capture::Session`: warmup, the arm gate, the frame ledger, naming and writing kept frames with their sidecars, the status document |
| `conventions.{hpp,cpp}` | the pieces it is made of, ported from `campod-camera/capture.py`: boot-id session, manifests, clock triple, gate file, status file, node name, UTC stems, PGM |
| `durable_writer.hpp` | tmp, fsync, rename, then fsync the directory; a bounded queue that drops rather than blocks. vio-tracker's #89 writer |
| `test_*.cpp` | hardware-free tests, run by each image build that includes this |

## The interface

A backend owns its device and threads. It calls `Session::frame()` with every frame it
retrieves -- kind, device sequence number, CLOCK_MONOTONIC instant, size, and any
backend-specific fields for the ledger and sidecar -- plus a function that produces the
encoded bytes, which is only called when the frame is kept. It calls `tick()` regularly for
the gate and the status. `Session` never calls back into the backend.

That suits both shapes of camera library: depthai's output queues are polled from the app's
own loop, while libcamera completes requests on its own thread, so campod-capture moves each
completed request onto its loop's thread before handing it over. `Session` is not
thread-safe.

A session configured with `sync` (the campods) adds camera sync to `ready` and to what is
kept: the backend reports it with `set_sync_ready()`, the status carries `sync_ready`, and
frames before it are ledgered as `sync_wait`.

Every frame retrieved gets one ledger line with its `decision`: `warmup`, `paused`,
`sync_wait`, `kept`, `dropped` (the writer's queue was full) or `encode_failed`. Files are
`<prefix>_<kind>_<seq>_<utc>`; a session of one stream leaves out the kind, which with the
node as the prefix is the campods' `<node>_<seq>_<utc>`.
