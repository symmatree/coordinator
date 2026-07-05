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

## Related

- [host-setup.md](host-setup.md) — base flash + Imager settings
- Filesystem robustness against ungraceful power loss — coordinator #41
- Time sync / RTC — coordinator #11
