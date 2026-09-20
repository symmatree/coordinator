# Coordinator networking, WiFi provisioning, and headless recovery

Background WiFi setup for the coordinator (Pi 4B), how to provision it reliably, and how to recover it headless. Written after a 2026-07-04 loss-of-WiFi event whose root cause is captured below.

## Base image

- **Raspberry Pi OS (64-bit)** via Raspberry Pi Imager 2.0+ (per [host-setup.md](host-setup.md)).
- The current official online image installs **Debian 13 "Trixie"** — note this where older docs just say "stock Pi OS." This unit is the **Desktop** image (the bring-up plan targets **Lite**; flag for re-image).

## Network stack: NetworkManager

Since Bookworm (and on Trixie), the default network stack is **NetworkManager**, *not* `dhcpcd` + `wpa_supplicant`. `wpa_supplicant` still runs underneath as NM's 802.11 backend, but the control surface is:

- `nmtui` — curses UI, easiest with a keyboard on the console
- `nmcli` — CLI (`nmcli device status`, `nmcli device wifi list`, `nmcli device wifi connect "<SSID>" password "<PW>"`, `nmcli connection show`)
- the panel network applet on the Desktop image
- logs: `journalctl -b -u NetworkManager`

Saved connections live as keyfiles in `/etc/NetworkManager/system-connections/<name>.nmconnection`.

## WiFi provisioning — two paths, and a Trixie caveat

**Imager step 4 (cloud-init) — unreliable on Trixie.** Imager writes a cloud-init *NoCloud* seed to `/boot/firmware/` (`network-config`, `user-data`, `meta-data`) in netplan v2 format. On the Trixie image observed here, cloud-init's **netplan→NetworkManager translation is broken**:

- `cloud-init status --long` → `degraded`, with `WARNING: Could not find module named cc_netplan_nm_patch`
- the generated `/etc/netplan/90-NM-*.yaml` files are **0 bytes**
- result: the Imager-provisioned WiFi **never becomes a persistent NetworkManager profile** — it may associate transiently but has no durable keyfile to auto-reconnect to.

**Provision via NetworkManager directly — reliable.** Create a real NM keyfile:

```bash
sudo nmtui                                        # Activate a connection / add WiFi
# or
nmcli device wifi connect "<SSID>" password "<PW>"
```

This writes `/etc/NetworkManager/system-connections/<SSID>.nmconnection` (autoconnect on by default), which survives reboots. **Prefer this over trusting the Imager WiFi step on Trixie.**

## Post-mortem: 2026-07-04 loss of WiFi

**Symptom:** coordinator dropped off the network (seen once on the UniFi client graph, then gone), unreachable over SSH.

**Ruled out by measurement (via HDMI console, then SSH):**
- **Power** — `vcgencmd get_throttled` = `0x0` (no undervoltage, ever). The Castle BEC path is proven; not a capacity issue.
- **Radio / firmware / hardware** — `brcmfmac` loaded cleanly (BCM4345/6), `wlan0` present.
- **Corruption** — boots to desktop, root FS healthy (~42% used). Not corruption.

**Root cause:** the only saved NM WiFi profile was the one created manually; the Imager-provisioned network had **no persistent NetworkManager profile**, because of the cloud-init/netplan breakage above. It could associate once but never durably reconnect. Creating a proper NM keyfile (`nmtui`) resolved it.

**Secondary finding — no reliable clock.** The Pi 4B has **no RTC**, and `fake-hwclock` was empty, so every boot starts with a stale clock until NTP corrects it (`timedatectl` shows sync only after network is up). This is why the journal timestamps were internally inconsistent (early-boot entries misdated ~18 h off). Relevant to log/capture timestamping and the broader time-sync work (chrony/PPS, coordinator #11), and it mirrors the FC's RTC-unset problem.

## Headless recovery runbook

If it won't come up on the network, attach a **micro-HDMI** display + keyboard and:

1. Log in; the desktop/console proves it booted (rules out corruption immediately).
2. Bring WiFi up: `sudo nmtui` → activate/add your network (this also fixes the cloud-init failure permanently by writing a real profile).
3. Diagnostics, in order:
   ```bash
   vcgencmd get_throttled                       # 0x0 = clean; bit0/bit16 = undervoltage
   ip -br addr show wlan0                        # does the interface exist / have an IP?
   nmcli device status                          # is wlan0 connected, and to what?
   nmcli connection show                        # what profiles exist (is the intended one missing?)
   journalctl -b -u NetworkManager | tail -40   # association-level failures
   sudo dmesg | grep -i brcmf                    # radio/firmware init
   ```
4. Ensure SSH is enabled (separate from WiFi): `sudo systemctl enable --now ssh`.
5. Grab the address: `nmcli -g IP4.ADDRESS device show wlan0` (or `hostname -I`).

## SSH access from the dev machine (WSL)

The Pi's SSH key lives in the **1Password SSH agent on Windows**, so connect with the **Windows** client from WSL:

```bash
ssh.exe pi@coordinator.local.symmatree.com
```

1Password prompts for approval on first key use per parent shell (subsequent connections in that shell reuse it). WSL-native `ssh` does not see the 1Password agent.

## USB gadget network (coordinator <-> campods)

**Working as of 2026-09-20**, first verified between `campod-sw` and the coordinator. Each
campod presents itself as a USB Ethernet device; the coordinator bridges them all onto one
L2 segment and holds a single address.

```
campod  usb0 (g_ether)  --micro-USB--> hub --> coordinator usbN (cdc_ether) --> br0
        10.55.0.13/24                                                          10.55.0.1/24
```

| | |
|---|---|
| subnet | `10.55.0.0/24`, **static on both ends, no DHCP** |
| coordinator | `10.55.0.1` on `br0` |
| campods | `.11` ne, `.12` se, `.13` sw, `.14` nw |
| measured | ~0.35 ms RTT, MTU 1500 |
| throughput, one pod | 24.9 MB/s (199 Mbit/s) |
| throughput, two pods at once | 17.1 + 16.2 MB/s, **aggregate 30.2 MB/s** (241 Mbit/s) |
| same pair over WiFi, for comparison | 5.0-5.3 MB/s (40-42 Mbit/s) |
| contract | `host/ansible/vars/gadget-net.yml` -- one file both roles read |

Static rather than DHCP (#211): nothing has to run on the coordinator, and a campod's
address does not depend on a lease. The MACs are derived from the hostname
(`02:` + five bytes of `sha256("campod-dev:"<hostname>)`, and `campod-host:` for the other
end) and pinned as `g_ether` module parameters, so a pod's identity on the wire is stable
across reboots without a per-pod profile.

Measured with `nc` and `dd`, 150 MB per transfer, 2026-09-20. A single pod does not
saturate the USB 2.0 bus -- running two pods concurrently raised the aggregate from 199 to
241 Mbit/s while each pod's share fell, so the per-pod ceiling is somewhere other than the
bus. Four pods concurrently has not been measured.

The WiFi comparison is the same two machines over their WiFi addresses, so the path is
campod -> AP -> coordinator: two traversals of a shared 2.4 GHz medium, not a
point-to-point radio link.

### Which layer owns which half

- **Image** -- `dtoverlay=dwc2,dr_mode=peripheral` on the campod only. Device tree, so it
  has to come from the image. The coordinator is the USB *host* and needs nothing.
- **Ansible, campod** -- load `dwc2`/`g_ether`, pin the MACs, the `campod-gadget`
  NetworkManager profile carrying the static address, and the udev rule below.
- **Ansible, coordinator** -- `br0`, and one `campod-bridge-port` profile with
  `multi-connect=3` that enslaves every matching interface as it appears. It matches on
  **driver**, not interface name: the primary name of a USB NIC here is path-based
  (`enp1s0u1u2...`), which identifies the hub port rather than the campod.

### Two things that make this not work, both since fixed

Recorded because both failed **silently** and each one alone is enough to leave the link
dead (coordinator [#354](https://github.com/symmatree/coordinator/pull/354)).

**NetworkManager will not manage a USB gadget interface.** It ships
`/usr/lib/udev/rules.d/85-nm-unmanaged.rules` containing
`ENV{DEVTYPE}=="gadget", ENV{NM_UNMANAGED}="1"`, and NM does not apply a profile to a device
it does not manage -- so a perfectly correct campod keyfile is inert and `usb0` stays DOWN
with no address and no error. The fix is a `90-` udev rule clearing the property, ordered
before the module load. An NM `conf.d` `managed=1` block does **not** work, and reloading
udev does not help a live interface: NM caches the managed state from when the device
appeared.

**NM keyfile list properties are `;`-separated.** `match.driver` written as
`cdc_ether rndis_host cdc_subset` is one pattern, not three, and matches nothing. On disk it
looks right; during autoconnect NM silently falls back to a default `Wired connection N`
profile and runs DHCP against nothing. Asking by hand is what says it plainly:
`device does not satisfy match.driver property`.

These were coupled, which is what made it confusing: because the gadget end never came up,
the host end never saw carrier, so the coordinator's bridge port never activated either --
one campod-side rule disabled both halves.

### Checking it

```bash
# campod
ip -br addr show usb0            # UP, 10.55.0.x/24
nmcli -g GENERAL.CONNECTION device show usb0   # campod-gadget
# coordinator
ls /sys/class/net/br0/brif/      # the usbN interfaces currently enslaved
ping -c3 10.55.0.13
```

`/sys/class/udc/` being non-empty on a campod shows the image's half took effect; the claim
check in `dotfiles-symm/pi-image` asserts that.

### One wedge during bring-up, with a known cause

While the campod's `usb0` was still DOWN (the udev bug above), the coordinator had already
enumerated the gadget, bound `cdc_ether` and was bridging to it. `g_ether` does not service
its OUT endpoint with the netdev down, so the host's transmits had nowhere to land:
`NETDEV WATCHDOG: transmit queue 0 timed out`, 60 tx_errors, and eventually control
transfers returning `-ETIMEDOUT` so the driver could not even rebind. Recovery needed
`modprobe -r g_ether && modprobe g_ether` **on the campod** -- an NM reconnect and a driver
unbind on the coordinator both failed.

That is a deterministic consequence of one end being misconfigured, not a property of the
link. It has not recurred since both ends were configured correctly, across a cold boot,
two converges and several reboots, with zero tx errors. Noted so the symptom is
recognisable, not as something to design around.

## Related

- [host-setup.md](host-setup.md) — base flash + Imager settings
- Filesystem robustness against ungraceful power loss — [power-loss-filesystem.md](power-loss-filesystem.md), coordinator #41
- Time sync / RTC — coordinator #11
