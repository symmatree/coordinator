# Rekon 10 Pro

Airframe and payload design notes for the **rekon10** vehicle this repo flies on. These moved here from the `fables` repo (`Drones/rekon10/`) so the design docs sit next to the code and FC config that implement them.

**Bulky reference material stayed behind in fables** and is linked by absolute URL from the docs that cite it: vendor manuals and datasheets under [`Drones/rekon10/attachments/`](https://github.com/symmatree/fables/blob/main/Drones/rekon10/attachments), plus the rendered [`flight-analysis-loiter-around.html`](https://github.com/symmatree/fables/blob/main/Drones/rekon10/flight-analysis-loiter-around.html). Nothing there is authored -- it is re-downloadable vendor PDFs and one log artifact -- and it is 38 MB against 1.4 MB of actual documents, which this repo's 500 KB `check-added-large-files` guard exists to keep out.

System overview, mission context, and design rationale: **[rekon-design.md](rekon-design.md)**

## Topic documents

| Topic | File |
|-------|------|
| System overview, mission, mapping payload | [rekon-design.md](rekon-design.md) |
| Canopy ops doctrine (ice-hole pattern, gap detection, map building, VIO risks) | [canopy-ops.md](canopy-ops.md) |
| OAK-D forehead mount | [oak-d-mount.md](oak-d-mount.md) |
| Arm pods (Pi Zero + cameras; multicamera sync, DS3234 PPS, chrony, upward gap-detect pair) | [arm-pods.md](arm-pods.md) |
| Mapping pipeline (PPK interpolation, ODM, rolling-shutter correction) | [mapping.md](mapping.md) |
| Central hub, power, pod harness | [central-hub.md](central-hub.md) |
| Flight platform (as-built hardware, wiring, stack recipe) | [flight-platform.md](flight-platform.md) |
| Flight platform build log (chronicle, bench notes) | [flight-platform-build-log.md](flight-platform-build-log.md) |
| ArduPilot configuration (params, serial, RC, tools) | [ardupilot.md](ardupilot.md) ; [`ardupilot/rekon10-methodi.param`](../../ardupilot/rekon10-methodi.param) |
| EdgeTX REKON10 model | [`config/MODELS/model01.yml`](config/MODELS/model01.yml) |
| EdgeTX FIREFLY16 model | [`config/MODELS/model02.yml`](config/MODELS/model02.yml) |
| EdgeTX Boxer radio (calibration, `currModel`, etc.) | [`config/RADIO/radio.yml`](config/RADIO/radio.yml) |
| Ground equipment (radio, goggles) | [ground-station.md](ground-station.md) |
| RTK integration (Holybro F9P, threads A through I, NTRIP/Tiles later) | *tracker not yet written; threads referenced inline in [ardupilot.md](ardupilot.md)* |
| Flight stack bring-up (phases + params) | Cursor plan `~/.cursor/plans/rekon_flight_stack_bring-up_b564811b.plan.md` |


**`config/`:** EdgeTX exports live under [`config/`](config/) -- [`MODELS/model01.yml`](config/MODELS/model01.yml), [`MODELS/model02.yml`](config/MODELS/model02.yml), [`RADIO/radio.yml`](config/RADIO/radio.yml). The **ArduPilot** export and the per-subsystem fragments are maintained in [`ardupilot/`](../../ardupilot/README.md), not here -- the copy that used to sit in `config/` was a stale second export and has been dropped.
