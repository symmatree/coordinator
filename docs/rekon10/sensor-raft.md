# Sensor raft -- the pod mounts

What goes on the raft and how each camera pod attaches to it. The raft itself (the 10 mm
aluminum cheese plate, the rubber isolators it hangs on, the gimbal dampers held in
reserve) is in [flight-platform-build-log.md](flight-platform-build-log.md), "Sensor raft
material (2026-10-27)". The camera geometry the mounts have to produce (aim angles, pod
assignment, field of view) is in [campod.md](../campod.md). This file is the mechanical
spec for the parts that sit between the two, as stated by Seth on 2026-10-28.

## What is being built first

The NE and NW pods, mounted on the raft. Each needs:

- an angled camera support with M2 heat-set inserts for the module;
- two 1/4-20 attachments down into the raft's corner bosses;
- the Pi tied to the cheese plate with zip ties, with Kapton between the Pi and the plate.

The Blackfly S ([flir-blackfly-s.md](flir-blackfly-s.md)) still flies, but its mount is a
separate, harder job and is not covered here.

## The corner bosses, as measured

Four corner mounts on the raft, each with two 1/4-20 holes in line with the direction of
travel. Measured across the pair, inner edge to inner edge and outer edge to outer edge:

| corner | inner edges | outer edges | centre to centre | implied bore |
|---|---|---|---|---|
| front | 9.9 mm | 20.1 mm | 15.0 mm | 5.1 mm |
| rear | 12.8 mm | 23.1 mm | 17.95 mm | 5.15 mm |

Centre spacing is the mean of the two measurements; the bore is half their difference.
A 5.1 mm bore is the 1/4-20 tap-drill size, which is what a tapped boss measures at.

## The wedge

One part per pod, bolted down into a corner pair. From the spec:

- **Body:** a truncated wedge. The angled face is the camera support, rotated and tilted
  to the pod's facing.
- **Facing:** pointed out at 45 degrees, and down so that nadir sits at the bottom edge of
  the frame. The down angle follows from the module's vertical field of view: the Camera
  Module 3 standard lens is 66 x 41 degrees ([campod.md](../campod.md)), so with the module
  landscape the optical axis is 69.5 degrees below horizontal (`90 - 41/2`). campod.md
  works with -70.
- **Position:** the camera support plane is centred over the outer hole of the pair, so
  the bolt is behind the camera.
- **Bolt seat:** a flattened circular area, 10 mm diameter, for the bolt head, with the
  1/4-20 clearance hole below it down to the boss.
- **Camera holes:** M2, for heat-set inserts, on a 21 mm horizontal by 13 mm vertical
  pattern (measured on the module).
- **Extent of the plane:** it stops 1.5 mm past the upper holes. From that edge the body
  drops away normal to the plane for 6 mm, then straight down.
- **Inboard tab:** 4 mm thick, reaching inboard to cover the second 1/4-20 hole. That bolt
  locates and clamps the part.

Not stated yet: the wedge's footprint and overall height, the clearance-hole diameter, and
which of the front/rear boss spacings each of NE and NW lands on. Those come out in the
model.

## The Pis

Zip-tied to the cheese plate, Kapton between board and plate.
