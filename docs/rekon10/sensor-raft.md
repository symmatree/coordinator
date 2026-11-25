# Sensor raft

## Background

The compute parts are stock raspberry pi parts or clones, and the cameras are commercial
modules. I've either painted conformal
silicone or put a layer of kapton tape over the most at risk bare conductors, but no
full housings or anything.

Current cooling expectation is that they simply don't get hot enough to matter.
Conceptually it's "propwash will handle in-flight, and we'll figure it out for
the bench", but in practice the units on the bench haven't had any observed heat
problems during testing, so I haven't looked at it further. collectd has
monitoring here.

## Base: Cheeseplate

The sensor raft base is a cheeseplate. Current unit is a Artcise CP200 ([B&H](https://www.bhphotovideo.com/c/product/1907895-REG/innorel_cp200_precision_machined_large_size_cheese.html).

* Aluminum
* Dimensions: 7.9 x 3.9 x 0.4" / 20 x 10 x 1 cm
* Weight: 0.7 lb / 300 g

It has 1/4-20 and 3/8-16 threaded holes and a few slots. Modeled as `artcise-cp-200`
in Fusion in the "Admin" project.

## Basic Design: 1/4-20 Forest

The cheese plate is suspended from the frame with 4 rubber
isolators. The camera mounts attach directly (corner mounts for the campod
modules, and the centerline truncated-wedge mount for the blackfly when it
is in use). Several 6" 1/4-20 bolts screw into the cheeseplate with a nut
jammed down against the surface to lock them; I'm still iterating on the
layout and whether to use any 3/8" bolts. Current thinking is to accept that
1/4" bends in a crash, but be happy that consumes energy which would otherwise
be damaging the payload.

Compute nodes are hung on the landing gear bolts, since they don't need
the positional stability of the raft base itself, and maybe it helps stop
the legs from ringing too much on their own. Also it's better than loose or
zip-tied.

What goes on the raft and how each camera pod attaches to it. The raft itself (the 10 mm
aluminum cheese plate, the rubber isolators it hangs on, the gimbal dampers held in
reserve) is in [flight-platform-build-log.md](flight-platform-build-log.md), "Sensor raft
material (2026-10-27)". The camera geometry the mounts have to produce (aim angles, pod
assignment, field of view) is in [campod.md](../campod.md). This file is the mechanical
spec for the parts that sit between the two, as stated by Seth on 2026-10-28.

## Campod compute mounts

Essentially a Y with a bolthole in the center and each arm reaching to the the two end holes
in the pi zero footprint. `zero-quarter-mount` in Fusion.

* Original version in service on campods. Didn't fit the radxa zero due to lack of clearance
  for parts around the mounting holes - the "real" zeros have lower neighbors.
* v2 adds a little step to give more clearance; untested as of 2026-11-25.

## Coordinator compute mounts

`coord-mount` in Fusion.

* v1 was just a hole for the bolt and a tab with a mounting hole on it. Worked okay
  for purpose, broke in the crash (not a problem).
* v2 tried to add a mount for the stack of power buses, didn't work in a few ways.
* v3: closer on power fit. Problems:
  * 1/4-20 is wrong position relative to the mounting hole we're targeting. Should
    be approx 1.5mm further away in both dimensions, but keep the upright and the tab
    shape which are correct.
  * Cut off the corners of the tab with teh M4 on it, there's a fillet on the mating
    surface that's making us stand off (only on the coner, not on the flats).
  * Move coordinator 1mm further away from the bolt to give a little more clearance
    where its bottom almost hits parts on the power thing.
  * Maybe: add a second tab for the other side of the two power things, so it's held
    in a U rather than just by a single tab on one side. But it's not really THAT
    heavy.
* v4: Fits the power unit nicely, but coord spacing isn't the same as power holes.
* v5: Changed to a slot for the coordinator instead of a single hole, see if that makes
  it adjustable enough. Trying to avoid needing explicit top and bottom mounts to
  offset in opposite directions.

## Camera module corner mounts

Modeled in fusion as `cp200-picam`. The same basic model can be used with different
mounting hole layouts if needed. `cp200-arducam-b0310` is an assembly showing the
camera module mounted.

- **Body:** a truncated wedge. The angled face is the camera support, rotated and tilted
  to the pod's facing.
- **Facing:** pointed out at 45 degrees, and down so that nadir sits at the bottom edge of
  the frame. The down angle follows from the module's vertical field of view: the Camera
  Module 3 standard lens is 66 x 41 degrees ([campod.md](../campod.md)), so with the module
  landscape the optical axis is 69.5 degrees below horizontal (`90 - 41/2`). campod.md
  works with -70. What the B0310s see from this facing, and how overlap and GSD change with
  the tilt, is in [campod.md](../campod.md), "4-camera, B0310".
- **Position:** the camera support plane is centred over the outer hole of the pair, so
  the bolt is behind the camera.
