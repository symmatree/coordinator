# capture-common

The capture control loop and the campod conventions, in C++ with no camera library: shared
source for each capture app, built into it with `add_subdirectory`. Used by
[`oak-capture`](../oak-capture/); a libcamera campod capture would be the second user.

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
own loop, while libcamera completes requests on its own thread, so a libcamera backend moves
each completed frame onto its loop's thread before handing it over. `Session` is not
thread-safe.

What it does not do yet, because no backend has needed it: a readiness condition beyond
warm (the campods' `ready` also needs camera sync).
