# Fleet bring-up: the order things happen across devices

The **cross-device sequence** for taking a set of blank cards to a working vehicle. Per-device
detail lives elsewhere and is not repeated here:

- [host-setup.md](host-setup.md) -- the coordinator (Pi 4B), one device, start to finish
- [campod.md](campod.md) -- a campod (Zero 2 W), one device, start to finish

This doc is only about **what order, and why** -- the thing neither of those can say, because
each describes a single machine.

> **Status: stages 1 and 2 are written from a bring-up that actually happened; stages 3--5 are
> the plan of record.** An unfilled stage says what is supposed to happen and what "done"
> means -- it is not a runbook and must not be read as verified. Stages 3--5 still carry
> `TO BE FILLED IN` markers and are to be completed by whoever performs that step.

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
2. Power on. cloud-init applies identity from `user-data` on the boot partition. One boot.
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

## Stage 2 -- Per-device software, independently (**done, 2026-09-12**)

Each device separately, still on WiFi, still wired to nothing. Order among devices does not
matter; they do not interact yet. Both the coordinator (Pi 4B) and campod-sw (Zero 2 W)
converged; transcript and raw output on
[#247](https://github.com/symmatree/coordinator/issues/247).

This is the stage [#236](https://github.com/symmatree/coordinator/issues/236) automates. The
first run was deliberately done by hand so this section records what happened rather than what
a service was assumed to do.

### The sequence

Driven from any machine that can reach the device. A freshly flashed card needs nothing
installed on it first -- `python3`, `sudo` with passwordless and sshd are all in the image --
so this runs against a virgin unit:

```bash
ansible-playbook host/ansible/site.yaml -i '<addr>,' -u pi \
  -e device_role=<coordinator|campod>
```

A bare `'<addr>,'` is a valid inventory, so no inventory file and no DNS are needed. The play
remounts `/usr` read-write, installs the prerequisites, creates the checkout, converges the
device, and reboots and waits if anything requires it.

**This replaced `host/one_time.sh`, which no longer exists.** That script was necessary only
because a device bootstrapping *itself* cannot use ansible to install ansible, so the
remount-then-apt ordering had to be survived in bash before the playbook could start. Driving
from outside removes the ordering problem rather than working around it, and removed
`host/lib/usr-rw.sh` with it -- the `/usr` hatch is a task in `roles/bootstrap` now, ordered
before the things that need it.

### What the reboot loop actually did

**Nothing** -- and that is the finding. No package on either unit ever set
`/var/run/reboot-required`; no kernel, firmware or module install happened. The only thing that
wanted a reboot was the `/usr` hatch, which cannot be closed on a running system.

So the "reboot and re-run until clean" loop the script documents did not occur, and the reboot
in step 3 is there to close the hatch rather than because anything demanded it.

The coordinator's run did exit 1 and take a second pass, but only because I had deliberately
rebooted it first to restore `ro` and test the wrapper in isolation
([#253](https://github.com/symmatree/coordinator/pull/253)) -- that is a test artifact, not a
second path through bring-up.

**Consequence worth knowing:** after convergence `/usr` is left writable, and the script says
nothing about it, because its flag fires on "did I remount" rather than "is this rw when it
should be ro". Finishing with a reboot is what makes the device match the invariant it shipped
with.

### Timing, Zero 2 W

Clone to converged: **~13 minutes** (15:57 to 16:10), of which the Docker install is most of
it -- `dockerd` first came active at 16:09. The 512 MB RAM did not bite; no OOM, no swap
thrash, `PLAY RECAP: ok=24 changed=13 failed=0 skipped=16`. The Pi 4B was several times faster.

Then `coord pull`: **4m22s** for `campod-camera` over lab WiFi -- 235 MB compressed, 941 MB on
disk. That is the number `campod.md`'s "how do updates reach a flying set of nodes" section was
missing. Note it is one node on an uncontended channel; four campods pulling at once share one
2.4 GHz radio, so it is a floor rather than an estimate for the fleet.

### Starting the campod stack with nothing attached

Worth recording because it is the state a freshly flashed pod is in, and because two of the
three things it proves are positive:

- **libcamera works in the container.** `libcamera v0.5.2+99-bfd68f78` initialises. So the
  Raspberry Pi apt archive pairing is right and the "stock Debian enumerates no cameras"
  container gotcha does not apply here. This is what `RPI_SUITE`-tracks-the-image
  ([#219](https://github.com/symmatree/coordinator/pull/219)) buys, confirmed rather than
  argued.
- **The ADXL345 reader degrades exactly as designed** ([#233](https://github.com/symmatree/coordinator/pull/233)):
  probes both chip selects, reports `DEVID 0x00, expected 0xE5` for each, says it will
  re-probe, exits 1, retries in 30 s. Bounded and legible with no sensors wired.
- **The camera loop exits and the container cycles**, which is correct -- the entrypoint keeps
  capture in the foreground so container health is camera health -- but it used to do it by
  letting picamera2 raise `IndexError: list index out of range` from `global_camera_info()`.
  Fixed in this change: it now names what libcamera can see, and on an empty list says so and
  points at the ribbon. It also takes ~109 s to get that far, because the picamera2 import is
  heavy on a Zero.

Nothing downstream of a camera being present is proven: capture, the exposure cap, the focus
control and the accel data path all remain untested.

### Carried into stage 3

Capture on a campod is **ungated**: `capture.py` loops from container start to SIGTERM with no
arm file, no trigger, no condition. At `CAMPOD_CAPTURE_HZ=1.0` and full-res q90 that is roughly
**10 GB/hour**, ~3 hours to fill the card. Combined with `restart: unless-stopped`, a campod
that cold-boots on a battery starts writing immediately -- so it fills the card while the
aircraft sits on the flight line waiting to be armed.

The coordinator already has the right shape for this (`OAK_ARM_FILE`, written by the router
from the FC's arm state, [#88](https://github.com/symmatree/coordinator/issues/88)). The campod
has no FC link, so getting arm state to it is what stage 3's gadget network is for. Worth
settling before a campod is ever powered from the vehicle rail with a camera attached.

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
