# Reproducible build of the rekon10 ELRS TX backpack firmware with WiFi-robustness patches.
#
# The RadioMaster Boxer internal 2.4GHz TX backpack is an ESP8285. Stock ExpressLRS Backpack
# firmware drops to its own AP on any transient WiFi loss and never reconnects until a
# power-cycle, leaves WiFi modem-sleep enabled, and exposes nothing about the backpack<->AP
# WiFi link (the surface that actually keeps failing). This builds release 1.5.9 +
# patches/wifi-robustness.patch, hermetically: PlatformIO self-fetches the pinned esp8266
# toolchain + framework into the image, so no system cross-toolchain is needed. See README.md
# for the "why". The artifact is NOT checked in -- copy it out at run time.
#
#   docker build -t rekon10-backpack .
#   docker run --rm -v "/path/to/datasets/firmware/backpack:/out" rekon10-backpack
#   # -> /out/ESP_TX_Backpack_1.5.9_rekon10-wifi.bin   (flash to the Boxer backpack)
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# PlatformIO in a venv (bookworm's system pip is externally-managed). PIO self-fetches the
# esp8266 platform + toolchain on first build, so the compile is hermetic.
RUN python3 -m venv /opt/pio && /opt/pio/bin/pip install --no-cache-dir -q platformio
ENV PATH="/opt/pio/bin:${PATH}"

WORKDIR /work
# Pin to the exact upstream release so the ONLY delta from stock is the WiFi patch.
RUN git clone --depth 1 --branch 1.5.9 https://github.com/ExpressLRS/Backpack.git .

COPY patches/wifi-robustness.patch .
RUN git apply wifi-robustness.patch

# Compile (also pulls the esp8266 toolchain/framework + lib deps into the image). The via_*
# envs differ only in upload tooling; the firmware.bin is identical. esp8285 == Boxer target.
RUN pio run -e ESP_TX_Backpack_via_UART

# On `docker run`, drop the flashable binary into the mounted /out (e.g. the datasets share).
CMD ["sh", "-c", "install -D .pio/build/ESP_TX_Backpack_via_UART/firmware.bin /out/ESP_TX_Backpack_1.5.9_rekon10-wifi.bin && echo 'wrote /out/ESP_TX_Backpack_1.5.9_rekon10-wifi.bin'"]
