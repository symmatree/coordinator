# Johnny (ground rover)

Design notes for **johnny**, the converted Axial SCX10 III running ArduRover. These moved
here from the `fables` repo (`Drones/johnny/`) so they sit next to the rekon10 docs and the
coordinator/campod code the rover is meant to share.

The vendor manuals for the BLITZ H743 Wing FC stayed behind in fables, under
[`Drones/johnny/attachments/`](https://github.com/symmatree/fables/blob/main/Drones/johnny/attachments),
the same way the rekon10 attachments did.

| Doc | What it is |
|-----|------------|
| [rover-design.md](rover-design.md) | Chassis, drive, FC, radio, payload and lidar parts; current state; camera layout study |
| [rover-camera-layout.scad](rover-camera-layout.scad) | OpenSCAD study of 8x CM3 around the deck; uses [`../rekon10/components/pi-blanks.scad`](../rekon10/components/pi-blanks.scad) |
| [`config/johnny-ardurover.param`](config/johnny-ardurover.param) | Last full ArduRover parameter dump off the BLITZ |
| [`config/MODELS/model06.yml`](config/MODELS/model06.yml) | EdgeTX model ("Johnny") from the flight radio |
