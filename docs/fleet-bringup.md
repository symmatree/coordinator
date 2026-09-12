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

## Stage 2 -- Per-device software, independently (**done, 2026-09-12**)

Each device separately, still on WiFi, still wired to nothing. Order among devices does not
matter; they do not interact yet. Both the coordinator (Pi 4B) and campod-sw (Zero 2 W)
converged; transcript and raw output on
[#247](https://github.com/symmatree/coordinator/issues/247).

This is the stage [#236](https://github.com/symmatree/coordinator/issues/236) automates. The
first run was deliberately done by hand so this section records what happened rather than what
a service was assumed to do -- and it needed **two workarounds that the runbooks did not
predict**, both written up below, because a service that does not handle them will fail the way
we did.

### The sequence, as actually run

```bash
# 0. WORKAROUND: grow the filesystem. Not in any runbook -- see below.
sudo /sbin/sfdisk -F /dev/mmcblk0                  # confirm free space follows p2
printf "yes\n" | sudo /sbin/parted ---pretend-input-tty /dev/mmcblk0 \
     u s resizepart 2 <last-sector>
sudo btrfs filesystem resize max /

# 1. WORKAROUND: /usr is read-only and git is not in the image, so this one
#    remount cannot come from the repo -- the repo is what you need git to clone.
sudo mount -o remount,rw /usr
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git

# 2. Clone and converge.
git clone https://github.com/symmatree/coordinator.git
cd coordinator && ./host/one_time.sh <coordinator|campod>

# 3. Reboot if it asks (it will, on a card whose /usr started read-only), then
#    re-run. The re-run is a no-op that exits 0.
sudo systemctl reboot
```

### What the reboot loop actually did

Nothing to do with kernels or firmware -- **no package ever set
`/var/run/reboot-required`** on either unit. The only thing that asked for a reboot was the
`/usr` hatch: `one_time.sh` remounts `/usr` read-write to install anything, `remount,ro` can
never succeed on a running system (`mount point is busy`, exit 32, measured), and a reboot is
what restores it. So the script flags `reboot-required` itself and exits 1 telling you to
reboot and run again ([#253](https://github.com/symmatree/coordinator/pull/253)).

- **Coordinator:** exited 1 with the hatch flag set. Rebooted; `/usr` came back
  `ro,noatime,compress=zstd:3,...,subvol=/@usr` and refused writes. One pass plus one reboot.
- **campod-sw:** exited **0** with no reboot asked for -- but only because the manual remount in
  step 1 had already left `/usr` writable, so the hatch saw `rw` and never fired. Its `/usr` is
  therefore still `rw` and needs a reboot to close. **A card where step 1 is not needed
  (i.e. once `git` ships in the image) will take the coordinator's path, not this one.**

### Timing, Zero 2 W

Clone to converged: **~13 minutes** (15:57 to 16:10), of which the Docker install is most of
it -- `dockerd` first came active at 16:09. The 512 MB RAM did not bite; no OOM, no swap
thrash, `PLAY RECAP: ok=24 changed=13 failed=0 skipped=16`. The Pi 4B was several times faster.

### Workaround 1: the filesystem never grew (image bug, blocks everything)

Both cards came up with a **3.24 GB root partition on a 31 GB card**. The coordinator had
59 MB free and died mid-`docker-ce` install; the campod had 725 MB, which would have survived
the install and then run out during `coord pull`. Docker alone wants 323 MB.

The failure is badly disguised. apt reports a full disk as:

```
E: Write error - write (28: No space left on device)
E: IO Error saving source cache
E: The package lists or status file could not be parsed or opened.
```

which reads like a corrupt apt database. **Anything automating this stage should check free
space itself rather than trust that text.**

Cause: the vendor's resize is two stages and this image loses both.
`init=/usr/lib/raspi-config/init_resize.sh` grows the partition, and our `cmdline.txt` carries
no `init=` at all, so it never runs. `resize2fs_once` then grows the filesystem, and it is
`resize2fs $(findmnt / -o source -n)` -- which on this layout is handed `/dev/mmcblk0p2[/@]`,
btrfs subvolume notation, and fails. Its `&&` chain means it never removes itself either, so it
re-fails every boot and is the entry in `systemctl --failed` on an otherwise healthy unit.

Worked around by hand on both units. Three things learned that the eventual fix needs:

- **`parted -m` refuses on a live mounted root** (`Partition /dev/mmcblk0p2 is being used`,
  exit 1) because `-m` cannot prompt without a tty. The vendor only gets away with a bare
  `resizepart` because `init_resize.sh` runs with `/` mounted read-only. `---pretend-input-tty`
  with `yes` piped in is `init_resize.sh`'s own idiom for this and works.
- **`partx -u` is not needed.** parted's BLKPG ioctl updated the live kernel view even with the
  partition mounted, on both units.
- **btrfs grows online**, so none of the vendor's two-stage-plus-reboot dance is required --
  that split exists only because `resize2fs` could not grow a mounted ext4 root.

The image-side fix is `dotfiles-symm`'s, and the hand version proves the *mechanism* only, not
the unit (ordering, the already-grown no-op check, self-disable, failure handling).

### Workaround 2: one remount that cannot come from this repo

`git` is not in the image, and `/usr` is read-only. So the **first** `apt-get install git` on a
fresh card needs a manual `mount -o remount,rw /usr` -- and the helper that would do it
(`host/lib/usr-rw.sh`) lives in the repo you need `git` to clone. Every other remount in the
flow is handled by `one_time.sh`; this one structurally cannot be.

Shipping `git` in the image removes it: the clone is load-bearing and universal, so with `git`
present there is no pre-clone apt and therefore no pre-clone remount. Not yet filed.

Everything else Ansible needs on a virgin card is already there -- `python3` 3.11.2, `sudo`
with passwordless, `ca-certificates`, `curl`, sshd.

### Done when -- and what it looked like

Both units reported the same shape:

| check | coordinator | campod-sw |
|---|---|---|
| `docker --version` / active | 29.8.0, Compose v5.5.1 | 29.8.0 |
| `/opt/stacks/<role>` | symlink into the checkout | symlink into the checkout |
| `coord` on PATH | yes (+ `vio-pose-tap`, `vio-ipc-record`) | yes |
| state dirs | `captures config ipc` | `captures config` |
| `pi` in `docker` | yes | yes |
| free space | 26G of 29G | 27G of 30G |

Role-specific, and both are firsts on this image:

- **Coordinator:** `/dev/i2c-1` now exists and survives a reboot
  ([#246](https://github.com/symmatree/coordinator/pull/246) -- the image supplies
  `dtparam=i2c_arm=on`, which binds the controller but not the char device; loading `i2c-dev`
  is the Ansible half and was simply never written). `coordinator-stack.service` enabled, and it
  auto-started the whole stack on the next boot with no prompting: tracker, estimator, router
  and display all up, tracker emitting real feature counts against the OAK-D.
- **campod-sw:** `g_ether` loaded with both MACs pinned from the hostname
  (`dev_addr`/`host_addr` in `/etc/modprobe.d/campod-g_ether.conf`), `usb0` present and `DOWN`
  -- correct, since nothing is plugged into the coordinator yet. That is stage 3.

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
