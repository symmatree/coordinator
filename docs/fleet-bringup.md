# Fleet bring-up: the order things happen across devices

The **cross-device sequence** for taking a set of blank cards to a working vehicle. Per-device
detail lives elsewhere and is not repeated here:

- [host-setup.md](host-setup.md) -- the coordinator (Pi 4B), one device, start to finish
- [campod.md](campod.md) -- a campod (Zero 2 W), one device, start to finish

This doc is only about **what order, and why** -- the thing neither of those can say, because
each describes a single machine.

> **Status: skeleton.** Stage 1 is written from a bring-up that actually happened. Stages 2--5
> are the **plan of record**, not a runbook: they say what is supposed to happen and what
> "done" means, and are to be filled in with real commands and real failure modes by whoever
> performs the first run. Do not read an unfilled stage as verified.

## The governing rule: software first, then connect

Each device is brought to a working software state **independently, over WiFi**, before any
of them are wired to each other. Only then are the gadget network, cameras and sensors
connected, and only then is the whole thing powered from the vehicle's 5 V rail.

This is deliberate, and the reason is diagnostic rather than technical: a device that is
already known-good over WiFi turns "the gadget link does not work" into a question about the
link. If both halves are new at once, every failure has two candidate causes and no way to
separate them. The same logic applies to the camera and the accelerometers.

It also means **the network used to install software is not the network used in flight.**
WiFi is bring-up infrastructure. The USB gadget net is the flight-time path, and it is
brought up against devices that already work.

---

## Stage 1 -- Flash, first boot, SSH (**done, 2026-09-12**)

Blank card to a reachable host. This stage is **verified on hardware**: it is how the two
units currently running were brought up.

Per-device procedure: [host-setup.md](host-setup.md) / [campod.md](campod.md) section 1.
Identity injection mechanics: `dotfiles-symm/pi-image/provision/README.md`.

1. Flash the role's btrfs image, injecting per-unit identity at flash time (hostname, user,
   SSH public key, WiFi). The image itself is generic and secret-free.
2. Power on. `firstrun.sh` applies identity, deletes itself, reboots. Expect two boots.
3. The unit joins lab WiFi and is reachable over SSH.

**Verified in this stage's first run:**

- Both roles boot the btrfs-subvolume image from SD -- the Pi 4B was the last unproven
  hardware class, and it booted.
- SSH by key works; `pi` has passwordless sudo and is in `adm` and `sudo`, confirmed on the
  running units rather than read out of the image.
- On the coordinator the FC UART is clean: no `console=serial0` in `/proc/cmdline`, and
  `serial-getty@ttyAMA0` inactive -- nothing transmits into the port the flight controller
  uses.

Addresses are DHCP and not stable; resolve by hostname rather than copying leases into
scripts.

**Done when:** every unit answers SSH by key, and `cat /etc/fleet-image` reports the image it
was flashed from.

---

## Stage 2 -- Per-device software, independently (**not yet run**)

Each device separately, still on WiFi, still wired to nothing. Order among devices does not
matter; they do not interact yet.

Per device: clone the repo and run the bootstrap for its role
(`./host/one_time.sh coordinator` / `campod`). The clone is load-bearing -- `/opt/stacks/<role>`
is a symlink into it, so `git pull` *is* the config deploy. The bootstrap installs Ansible on
the device and converges it, and may ask for a reboot and a re-run.

This is the stage [#236](https://github.com/symmatree/coordinator/issues/236) automates. The
first run is deliberately done **by hand**, so the process this doc records is what actually
happened rather than what a service was assumed to do.

**Done when:** each device independently reports a converged stack -- to be filled in with the
actual checks used.

> TO BE FILLED IN: the real command sequence per role, what the reboot loop actually did, how
> long it took on a 512 MB Zero, and anything that had to be worked around.

---

## Stage 3 -- Connect: gadget network, cameras, sensors (**not yet run**)

Only now is anything wired. Each item added here is added against devices that already work,
so a failure is attributable to the thing just connected.

- **USB gadget network.** Campods enumerate as peripherals; the coordinator is the host and
  bridges them. Both halves are configured in the repo but **no link has ever been brought
  up** -- throughput and stability are unmeasured, and the link was specified for commands
  rather than bulk transfer. See [#12](https://github.com/symmatree/coordinator/issues/12) /
  [#24](https://github.com/symmatree/coordinator/issues/24).
- **Cameras.** OAK-D on the coordinator; Camera Module 3 on each campod. Nothing in the campod
  capture path has run on real hardware.
- **Accelerometers.** ADXL345 over SPI, colocated with the camera and at the arm end
  ([#211](https://github.com/symmatree/coordinator/issues/211)). Inert until
  `CAMPOD_ACCEL_DEVICES` is set in git.

> TO BE FILLED IN: connection order, what enumerated and what did not, and the first real
> measurement of the gadget link.

---

## Stage 4 -- Power from the vehicle's 5 V rail (**not yet run**)

Off bench power and onto the rail that feeds the payload, with everything connected.

> TO BE FILLED IN: what is powered from where, what comes up in what order, and what a cold
> start looks like with the whole payload attached.

---

## Stage 5 -- Determine system function (**not yet run**)

The check that the assembled system works, not that each piece is reachable. The distinction
matters: **reachable is not working.** A device answering SSH says nothing about whether its
camera enumerated or its capture path runs.

One shape proposed for this, from [#223](https://github.com/symmatree/coordinator/issues/223):
a probe over MAVLink injected into the mavproxy, with the coordinator running a script in
response and emitting telemetry observable in the tlog or the live stream -- a check that needs
no laptop and rides a path already being recorded.

> TO BE FILLED IN: what is actually checked, what the pass condition is, and how a partial
> failure presents.

---

## Related

- [#223](https://github.com/symmatree/coordinator/issues/223) -- pre-flight / post-flight, the
  epic that makes stages 2 and 5 repeatable rather than manual
- [#236](https://github.com/symmatree/coordinator/issues/236) -- the provisioning service that
  automates stage 2
- [deployment-model.md](deployment-model.md) -- why config is git-authoritative and the clone
  is the deploy
- `dotfiles-symm/pi-image/` -- the image build and per-unit provisioning behind stage 1
