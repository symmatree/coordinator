# AGENTS.md -- operating this repo's systems

Repo-specific operating rules for coordinator. General agent conduct is in
[CLAUDE.md](CLAUDE.md); this file is the part that only applies here.

## Read the procedure before proposing a device action

Anything that touches the vehicle -- the coordinator, the campods, the FC -- has a standard
procedure. Read it before suggesting a way to do something, and use it as written. A
hand-rolled alternative leaves the operator unable to say what ran on the vehicle and breaks
the record later analysis depends on.

- **Fleet-control is the interface to the devices.** `https://fleet.tiles.symmatree.com`.
  Routes and what each one costs the device:
  [containers/fleet-control/README.md](containers/fleet-control/README.md).
- **Bench cycle, collect, and reading what came back:** [docs/campod.md](docs/campod.md),
  "Running it". `harness/bench_cycle.py` is that chain.
- **Collecting a flight:** `GET /nodes/<node>/sessions` for each node, then one
  `POST /flights/<flight>/collect` naming the sessions and FC logs. Bundles land in the
  flight's directory on the NAS.
- **Unpacking and analysing:** the flight-analysis runner in `tiles`
  (`tanka/environments/flight-analysis/runner.py`) owns extraction (`extract_bundles`) and
  the per-flight notebook set; layout is [docs/flight-data-layout.md](docs/flight-data-layout.md).
- **FC configuration** changes are a PR editing a fragment in `ardupilot/inputs/`, see
  [ardupilot/README.md](ardupilot/README.md).

If a procedure does not fit what is needed, say so and propose changing it; do not route
around it.
