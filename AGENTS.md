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

## Naming motors

Name rekon10's motors by corner -- NE, NW, SE, SW, nose up -- and get every label from
[`analysis/motors.py`](analysis/motors.py). The table, and how it was checked against flight
data, is in [docs/rekon10/ardupilot.md, "Which motor is which"](docs/rekon10/ardupilot.md#which-motor-is-which).

- **ESC index and FC output are not the ArduPilot motor number.** Outputs 1-4 are Motor1,
  Motor3, Motor4, Motor2. Map ESC index to motor with `motors.esc_motors(parms)` before you
  say which motor did anything.
- **Front pair against rear pair is fore/aft.** NE + NW against SE + SW, which is ESC 0 + 1
  against ESC 2 + 3, is the forward-CG split (coordinator#169). A diagonal is NE + SW against
  NW + SE; don't call a front/rear difference diagonal.
