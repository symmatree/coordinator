# blackfly: the FLIR camera pod

**blackfly** is a camera pod made of a FLIR Blackfly S on a Radxa Zero 3W. Its device role is
also **blackfly**: a campod's job on a Radxa board, riding the same pod bus, gadget network and
file contract as the campods ([campod.md](campod.md)). This doc is how the pieces are put
together. The parts themselves have their own docs:

| Part | Doc |
|---|---|
| The camera and lens: sensor, GPIO, power inputs, trigger, mounting holes, this unit's quirks | [rekon10/flir-blackfly-s.md](rekon10/flir-blackfly-s.md) |
| The board: ports, header pins, kernel, the USB3 orientation defect, the OS image | [dotfiles-symm `pi-image/RADXA-ZERO3.md`](https://github.com/symmatree/dotfiles-symm/blob/main/pi-image/RADXA-ZERO3.md) |
| The capture software | [containers/flir-camera/README.md](../containers/flir-camera/README.md) |

## Wiring

| Board side | Goes to | Carries |
|---|---|---|
| USB-C 1 (J2, OTG, the power port) | the coordinator's USB hub | the board's 5 V, and the gadget network link |
| USB-C 2 (J9, USB3 host) | the camera's USB3 Micro-B | the camera's data and, today, its 5 V |
| header pin 37 (`GPIO1_A4`), pin 39 ground | the PPS line | the 1 Hz edge chrony disciplines to |

**Power.** The camera draws up to 4.2 W. Today it takes that over J9, whose 5 V comes off the
board's own input, so both come through the hub's lead to J2. On 2026-10-28 blackfly and the
camera on an unpowered hub did not come up; on a wall-powered hub they did. The other ways to
feed the camera:

1. **5 V to the camera's VBUS directly from a UBEC pigtail** ([rekon10/central-hub.md](rekon10/central-hub.md)),
   with the cable's VBUS conductor broken on the Radxa side so the two sources are not tied.
   Data pairs pass through untouched, and the camera's current never passes through the board.
2. **VAUX on the camera's GPIO connector,** 8-24 V. A 5 V pigtail cannot feed it directly and a
   full 6S pack (25.2 V) is over the maximum, so it needs a boost converter, wired into the same
   Hirose cable as any trigger. Costs Line 3.

A Radxa forum user reported J9 sagging to about 4.2 V at about 1 A, supply unknown
([thread](https://forum.radxa.com/t/how-do-i-make-the-usb-c-port-offer-a-higher-maximum-current/24160)).

**USB link speed.** The camera has linked at 480M (USB 2.0) on every connection so far, on
blackfly and on a Windows PC, in both plug orientations, all through the same USB-C splitter
cable. On blackfly the type-C controller reported `orientation: unknown` and no partner. At
480M the camera caps itself at 1.757 Hz at full resolution, above the 1 Hz it runs at. Why
plug orientation matters on J9 is in the board doc.

## Image

Flashed from dotfiles-symm `pi-image` with `roles/blackfly.env`: Armbian trixie on Rockchip's
vendor 6.1 kernel, with `@data` mounted at `/var/lib/blackfly`. What the image sets for this
pod:

- `usbcore.usbfs_memory_mb=1000` on the kernel command line. The default, 16 MB, is smaller
  than one 24.5 MB frame; 1000 is FLIR's recommendation.
- An overlay with `pps-gpio` on `GPIO1_A4` (falling edge) and `dr_mode = "peripheral"` on the
  OTG controller, so J2 is always the gadget side.

## Network and time

- **Gadget link:** `g_ether` on the OTG port, `usb0` at **10.55.0.21/24**
  (`host/ansible/vars/gadget-net.yml`), on the coordinator's `br0` at 10.55.0.1. The pod bus
  broker is `tcp://10.55.0.1:1883`.
- **Time:** chrony with `server 10.55.0.1` and `refclock PPS /dev/pps0`. There is no DS3234 on
  the pod. Until the coordinator answers, blackfly has no time source, and the capture waits:
  `blackfly-camera` is `After=`/`Wants=time-sync.target`.

## Provisioning and the stack

`device_role` is `blackfly` (fleet-control's inventory in tiles,
`tanka/environments/fleet-control/inventory.json`). In `host/ansible`:

- **`provision.yaml`** runs `campod-hardware` for blackfly: the `g_ether` module load, pinned
  MACs, and the NetworkManager profile for `usb0`. Without it there is no `usb0`.
- **`deploy.yaml`** runs the `campod` role with the stack `stacks/blackfly`, state root
  `/var/lib/blackfly`.
- After a provision the play says to converge and then reboot; the reboot applies the module
  loads and udev rules and returns `/usr` to read-only.

`stacks/blackfly` is `blackfly-stack.target` and two units, both `Network=host`:

- **`pod-link`** (container `blackfly_pod_link`), the bus subscriber. Its collection unit is
  `blackfly-camera`; there is no accelerometer status file, so its `ready` is the camera's.
- **`blackfly-camera`** (container `blackfly_camera`), the `coordinator-flir-camera` image,
  `--privileged` for libusb, writing to `/var/lib/blackfly/captures`. Arming is the capture
  flag pod-link writes at `/tmp/campod_capture`.

The role and stack were named `rampod`, with state at `/var/lib/campod`, until coordinator#652.
On 2026-10-28 the device still ran that earlier build; the rename reaches it with a reflash
from the new image, then provision and converge.

The bench simulated-flight cycle included blackfly from coordinator#650 until it was taken
out for the 2026-10-29 four-campod flight, which blackfly is not mounted for.

## Not working yet

- **No accelerometer reader.** The campod reader is hard-wired to `spidev0.0`/`0.1`.
- **Frame time is host receipt, not exposure.** No trigger or `ExposureActive` line is wired;
  the sidecar fields are in the capture README.
- **No `sync_ready`.** blackfly's `camera.ready` is warm and frames arriving; a campod's is
  warm and synced.
