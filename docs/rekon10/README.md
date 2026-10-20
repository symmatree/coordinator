# Rekon 10 Pro

Airframe and payload design notes for the **rekon10** vehicle this repo flies on. These moved here from the `fables` repo (`Drones/rekon10/`) so the design docs sit next to the code and FC config that implement them.

**Bulky reference material stayed behind in fables** and is linked by absolute URL from the docs that cite it: vendor manuals and datasheets under [`Drones/rekon10/attachments/`](https://github.com/symmatree/fables/blob/main/Drones/rekon10/attachments), plus the rendered [`flight-analysis-loiter-around.html`](https://github.com/symmatree/fables/blob/main/Drones/rekon10/flight-analysis-loiter-around.html). Nothing there is authored -- it is re-downloadable vendor PDFs and one log artifact -- and it is 38 MB against 1.4 MB of actual documents, which this repo's 500 KB `check-added-large-files` guard exists to keep out.

System overview, mission context, and design rationale: **[rekon-design.md](rekon-design.md)**

## Where a hardware fact goes

The index below lists topics. This is the part that is not obvious from the headings: which
doc answers which **question**, since the questions people actually arrive with are "what is
on this pin" and "what can I move", not "tell me about timing".

| If you are asking | Look in |
|---|---|
| What is on an **FC pad or port** -- what is free, what is wired, what voltage it tolerates | [flight-platform.md](flight-platform.md), *Flight controller* |
| What **ArduPilot parameter** makes a pin behave that way | [ardupilot.md](ardupilot.md) |
| What is on the **hub board** -- the buffer tree, the 5 V rails, the pod harness, the PPS signal | [central-hub.md](central-hub.md) |
| What is on a **campod** -- the Zero, the camera, the accelerometers, their wiring | [campod.md](../campod.md), [campod-electrical.md](../campod-electrical.md) |
| What the **ESC, motors, battery, radio or VTX** are and how they are wired | [flight-platform.md](flight-platform.md) |
| **Why** a part or value was chosen, and what was tried before it | [flight-platform-build-log.md](flight-platform-build-log.md) |
| Which **propeller set** is which, and what each one is | [props.md](props.md) |
| How something is **mechanically mounted** -- the OAK-D, the arm pods | [oak-d-mount.md](oak-d-mount.md), [campod.md](../campod.md) |
| What a **blink pattern** means with no link up | [led-cheatsheet.md](led-cheatsheet.md) |
| Who owns a hardware question, and what is open on the vehicle | [`docs/handoffs/flight-hardware-handoff.md`](../handoffs/flight-hardware-handoff.md) |

**The rule is by component, not by subsystem.** A fact about an FC pad belongs with the FC
even if it exists because of the hub board, and vice versa. Grouping by the subsystem you
happen to be working on puts facts where only you would look -- somebody with a soldering
iron in their hand looks under the board the iron is touching.

So the two seams that keep needing trimming:

- `central-hub.md` describes the **signal** a buffered output carries. The moment it starts
  explaining an FC pad or a parameter, that part belongs in `flight-platform.md` or
  `ardupilot.md` with a pointer back.
- `flight-platform.md` describes the **pad**. The moment it starts explaining why the
  signal on it has the shape it does, that part belongs in `central-hub.md`.

When something genuinely sits on a line, put it in one doc and point from the other. Two
copies diverge, and the copy nobody follows back is the one that goes stale.

Software consumers of any of this -- chrony, the capture stack, the router -- are outside
this directory: [`docs/time-base.md`](../time-base.md), [`docs/campod-software.md`](../campod-software.md),
[`docs/architecture.md`](../architecture.md).

## Topic documents

| Topic | File |
|-------|------|
| System overview, mission, mapping payload | [rekon-design.md](rekon-design.md) |
| Canopy ops doctrine (ice-hole pattern, gap detection, map building, VIO risks) | [canopy-ops.md](canopy-ops.md) |
| OAK-D forehead mount | [oak-d-mount.md](oak-d-mount.md) |
| Arm pods (Pi Zero + cameras; multicamera sync, DS3234 PPS, chrony, upward gap-detect pair) | [campod.md](../campod.md) |
| Mapping pipeline (PPK interpolation, ODM, rolling-shutter correction) | [mapping.md](mapping.md) |
| Central hub, power, pod harness | [central-hub.md](central-hub.md) |
| Flight platform (as-built hardware, wiring, stack recipe) | [flight-platform.md](flight-platform.md) |
| Flight platform build log (chronicle, bench notes) | [flight-platform-build-log.md](flight-platform-build-log.md) |
| Vibration and structural testing (chain validation, bump-test method) | [vibration-testing.md](vibration-testing.md) |
| Propellers, by label (which sets exist and what they are) | [props.md](props.md) |
| ArduPilot configuration (params, serial, RC, tools) | [ardupilot.md](ardupilot.md) ; [`ardupilot/rekon10-methodi.param`](../../ardupilot/rekon10-methodi.param) |
| EdgeTX REKON10 model | [`config/MODELS/model01.yml`](config/MODELS/model01.yml) |
| EdgeTX FIREFLY16 model | [`config/MODELS/model02.yml`](config/MODELS/model02.yml) |
| EdgeTX Boxer radio (calibration, `currModel`, etc.) | [`config/RADIO/radio.yml`](config/RADIO/radio.yml) |
| Ground equipment (radio, goggles) | [ground-station.md](ground-station.md) |
| LED cheat sheet (ELRS RX, FC, GPS blink patterns) | [led-cheatsheet.md](led-cheatsheet.md) |
| RTK integration (Holybro F9P, threads A through I, NTRIP/Tiles later) | *tracker not yet written; threads referenced inline in [ardupilot.md](ardupilot.md)* |
| Flight stack bring-up (phases + params) | Cursor plan `~/.cursor/plans/rekon_flight_stack_bring-up_b564811b.plan.md` |


**`config/`:** EdgeTX exports live under [`config/`](config/) -- [`MODELS/model01.yml`](config/MODELS/model01.yml), [`MODELS/model02.yml`](config/MODELS/model02.yml), [`RADIO/radio.yml`](config/RADIO/radio.yml). The **ArduPilot** export and the per-subsystem fragments are maintained in [`ardupilot/`](../../ardupilot/README.md), not here -- the copy that used to sit in `config/` was a stale second export and has been dropped.
