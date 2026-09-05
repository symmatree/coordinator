# rekon10 ELRS TX backpack firmware -- WiFi-robustness build

Custom [ExpressLRS Backpack](https://github.com/ExpressLRS/Backpack) build for the rekon10's
**RadioMaster Boxer internal 2.4 GHz TX backpack** (an **ESP8285**), which bridges house-WiFi
UDP MAVLink/RTCM to the ELRS uplink. It exists because that WiFi bridge is the least reliable
link in the RTCM corrections path -- it intermittently fails to hold the house AP and needs
repeated power-cycles. Corrections path and the failure history:
coordinator [`docs/rtk-corrections-path.md`](../../docs/rtk-corrections-path.md); ground kit
inventory: fables [`Drones/rekon10/ground-station.md`](https://github.com/symmatree/fables/blob/main/Drones/rekon10/ground-station.md).

## Why not just update stock

Updating stock (you're on 1.5.5; latest is 1.5.9) does **not** fix this. The one relevant WiFi
fix since 1.5.5 -- [Backpack #219](https://github.com/ExpressLRS/Backpack/pull/219) "TLM WiFi
mode fix" -- is **ESP32-C3-only** (issue [#217](https://github.com/ExpressLRS/Backpack/issues/217)),
and the Boxer's backpack is esp8285. The deeper robustness gaps are still present in current
upstream master and apply to the esp8285 path, so they need a local patch.

## What the patch changes (details in [`patches/wifi-robustness.patch`](patches/wifi-robustness.patch))

Four narrow changes in `lib/WIFI/devWIFI.cpp`, no new files:

1. **Ride out transient drops.** Stock treats `WL_CONNECTION_LOST` as fatal and falls straight
   to its own AP -- so one missed-beacon blip strands the backpack off-network until a
   power-cycle. Patched, it reconnects (like `WL_DISCONNECTED`) and only falls back after the
   existing 30 s failure window. This is the direct cause of the "power-cycle roulette."
2. **Retry the home network from AP mode.** Change 1 widens the window before the backpack
   gives up, but upstream's fallback to AP is still *terminal*: both the status handler and
   the 30 s timeout are gated on `wifiMode == WIFI_STA`, and the only routes back are the
   `/connect` and forget-network HTTP handlers -- reachable only from a browser on the
   backpack's own AP. So any outage longer than 30 s (flying out of range) leaves the
   backpack sitting as an access point, and it will not rejoin even once it is back in
   range. Patched, an *involuntary* fallback re-attempts the home network every
   `STA_RETRY_INTERVAL_MS` (60 s). A deliberate `/access` request is exempt, and a retry
   never fires while a client is associated to the AP, so an in-progress config or flash
   session is never yanked away.
3. **Disable WiFi power-save** (esp8285 modem-sleep) + enable auto-reconnect -- fewer missed
   beacons / drops at range.
4. **Expose the backpack<->AP link health** on the `GET /mavlink` status endpoint (previously
   invisible -- we had RF-link stats but nothing on the WiFi side): `rssi`, `ssid`, `bssid`,
   `reconnects` (see the caveat under Verify), and `uptime_ms`. `drops_down` is left as-is
   (it's a per-source sequence artifact, not real loss); `overflows_down` is the meaningful
   loss counter.

Everything else -- MAVLink forwarding, ports, targets -- is byte-identical to stock 1.5.9.

## Build

```sh
docker build -t rekon10-backpack .
docker run --rm -v "/path/to/datasets/firmware/backpack:/out" rekon10-backpack
# -> /out/ESP_TX_Backpack_1.5.9_rekon10-wifi.bin
```

The Dockerfile clones ExpressLRS/Backpack at the **1.5.9 tag** (only delta = the patch), lets
PlatformIO self-fetch the pinned esp8266 toolchain + framework into the image, applies the
patch, and builds the `ESP_TX_Backpack` env. **The built binary is intentionally not checked
in** -- `docker run` copies it out to a mounted dir; keep the versioned `.bin` on the datasets
share, not in git.

To build without Docker: `git clone -b 1.5.9 ...Backpack && cd Backpack &&
git apply /path/wifi-robustness.patch && pio run -e ESP_TX_Backpack_via_UART` (artifact at
`.pio/build/ESP_TX_Backpack_via_UART/firmware.bin`).

## Flash

[`flash.sh`](flash.sh) drives the WiFi web-UI flash: it downloads the CI-built firmware,
verifies the target is an ELRS backpack in WiFi mode (`/config`), and posts to `/update`.
The backpack's own target-check rejects a wrong image (`"status": "mismatch"`) and the
script never auto-forces.

Because a single client usually can't be on both the internet and the backpack's own AP,
and modern Windows/Android clients bail off a no-uplink AP, it splits into two phases:

```sh
# 1) on house WiFi -- downloads to /tmp and prints the exact flash line:
./flash.sh fetch
# 2) switch your client to the "ExpressLRS TX Backpack" AP (password expresslrs), then:
./flash.sh flash 10.0.0.1 /tmp/backpack-firmware/<file>.bin
```

`fetch` needs internet + `gh` and never touches the backpack; `flash` never touches the
internet. If you *can* reach both at once (backpack on house WiFi, or a dual-homed jump
box), `./flash.sh auto 10.0.0.1` does both in one go. Flash over the backpack's **own AP**
(`10.0.0.1`), not the flaky house-WiFi STA path.

**Rollback** is symmetric -- `./flash.sh flash <ip> stock.bin` with any stock 1.5.x
release. Same-family firmware, so backpack config (home-network creds, MAVLink ports)
persists across the flash -- but re-verify the home-network SSID after (via `/config`).
Wired alternatives (EdgeTX passthrough / ExpressLRS Configurator "flash local file")
remain available if you'd rather not go over WiFi.

If `flash.sh` reports **`mismatch`**, our generic `ESP_TX_Backpack` build isn't tagged as
the boxer target -- bake the boxer target into the Docker build rather than force-flashing.

## Verify

After flashing, `curl http://boxer-txbp.local.symmatree.com/mavlink` (or `http://10.0.6.120/mavlink`)
should now include a `link` block:

```json
"link": { "rssi": -63, "ssid": "...", "bssid": "...", "reconnects": 1, "uptime_ms": 123456 }
```

- **`rssi`** is the backpack's own signal to the AP -- read it under the antenna vs. at the
  flying spot to quantify the link margin (turns the manual RSSI survey into a live number).
- **`reconnects`** is misnamed -- read it as "connections". `sta_reconnects++` sits in the
  shared `WL_CONNECTION_LOST` / `WL_DISCONNECTED` case, so it counts every entry into
  reconnection, not just genuine link losses. Its post-boot baseline is therefore **not
  necessarily 0** -- take the first post-association sample as the baseline and read only
  increments *above* it as real losses. Climbing while stationary is a flapping link (the
  thing patch #1 rides out instead of dying on).

## Scope

- **esp8285 / Boxer internal backpack only.** The newer C3 backpacks (Nomad, GX12, ...) are a
  different target and already got the upstream C3 WiFi fix.
