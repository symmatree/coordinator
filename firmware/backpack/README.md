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

Three narrow changes in `lib/WIFI/devWIFI.cpp`, no new files, ~19 lines:

1. **Ride out transient drops.** Stock treats `WL_CONNECTION_LOST` as fatal and falls straight
   to its own AP -- so one missed-beacon blip strands the backpack off-network until a
   power-cycle. Patched, it reconnects (like `WL_DISCONNECTED`) and only falls back after the
   existing 30 s failure window. This is the direct cause of the "power-cycle roulette."
2. **Disable WiFi power-save** (esp8285 modem-sleep) + enable auto-reconnect -- fewer missed
   beacons / drops at range.
3. **Expose the backpack<->AP link health** on the `GET /mavlink` status endpoint (previously
   invisible -- we had RF-link stats but nothing on the WiFi side): `rssi`, `ssid`, `bssid`,
   `reconnects` (a real flap counter), and `uptime_ms`. `drops_down` is left as-is (it's a
   per-source sequence artifact, not real loss); `reconnects` is the meaningful error-state.

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

Flash `ESP_TX_Backpack_1.5.9_rekon10-wifi.bin` to the Boxer backpack **the same way you
flashed the stock version** (EdgeTX passthrough / ExpressLRS Configurator "flash local file" /
the backpack's own WiFi web-UI update page). **Rollback** is symmetric: flash any stock
1.5.x release the same way. Same-family firmware, so backpack config (home-network creds,
MAVLink ports) persists -- but re-verify the home-network SSID after.

## Verify

After flashing, `curl http://boxer-txbp.local.symmatree.com/mavlink` (or `http://10.0.6.120/mavlink`)
should now include a `link` block:

```json
"link": { "rssi": -63, "ssid": "...", "bssid": "...", "reconnects": 0, "uptime_ms": 123456 }
```

- **`rssi`** is the backpack's own signal to the AP -- read it under the antenna vs. at the
  flying spot to quantify the link margin (turns the manual RSSI survey into a live number).
- **`reconnects`** climbing while stationary = a flapping link (the thing patch #1 now rides
  out instead of dying on).

## Scope / status

- **esp8285 / Boxer internal backpack only.** The newer C3 backpacks (Nomad, GX12, ...) are a
  different target and already got the upstream C3 WiFi fix.
- **Compile-verified in the image; not yet field-verified.** The runtime behavior (reconnect,
  power-save, link telemetry) still needs a bench/flight confirmation.
