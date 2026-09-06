#!/usr/bin/env python3
"""ADXL345 vibration logger for the Rekon camera pod (coordinator #211).

Reads one or more ADXL345 accelerometers over SPI and writes batched samples as
JSONL into the same session directory as the camera frames, stamped on the same
kernel clock. Two sensors are the intended first configuration:

  * **camera** -- colocated with the CM3, bonded behind it. What the imaging
    sensor sees; this is the one X20 needs.
  * **arm** -- at the arm end toward the motor. The source spectrum, the transfer
    across the arm, and the before/after reference for felt on the motors.

Why the pair, and why the separation must be recorded
-----------------------------------------------------
One pixel of image shift on the CM3 is 282 urad of camera rotation, but 705 um of
camera *translation* at a 2.5 m hover -- rotation is ~1000x more efficient at
writing rolling-shutter banding. A single accelerometer senses rotation only
through its lever arm from the rotation axis and under-reads it. Differential
acceleration between two points at a known separation is the rotational signature:

    theta = (delta_a / d) / (2*pi*f)^2

At 120 Hz over a 150 mm baseline, 1 g of differential acceleration is 115 urad --
0.41 px of image shift. Which is why `separation_m` belongs in the run manifest
and "near the end of the arm" does not.

Why userspace SPI and not the IIO driver
-----------------------------------------
Checked rather than assumed. There *is* a stock `i2c-sensor,adxl345` overlay, and
the mainline IIO driver grew FIFO + watermark support in kernel 6.16 -- but Pi OS
does not ship it. The Raspberry Pi archive's Contents index lists only
`drivers/input/misc/adxl34x*.ko` (the legacy input driver: /dev/input events, no
IIO buffer) for every kernel through 6.18.39, and `CONFIG_ADXL345_SPI` appears in
no bcm27xx defconfig. There is no SPI overlay for the part, and the generic
`anyspi` overlay cannot express `spi-cpol`/`spi-cpha` (this part is mode 3) or
`interrupt-names`, without which the IIO driver forces FIFO_BYPASS and offers no
buffer at all. Even with the module built, it pushes X/Y/Z with no IIO_TIMESTAMP
channel, so the timestamps would still be ours to make.

So: spidev, and **no INT wire** -- two fewer conductors through the camera
sandwich, and the FIFO is deep enough that polling cannot overrun (see POLL_NOTE).

Timestamps
----------
Each FIFO *batch* is stamped, not each sample: CLOCK_BOOTTIME (to match the
camera's `SensorTimestamp`) and CLOCK_MONOTONIC. Per-sample times are NOT
computed here. The record carries the cumulative sample index, so the effective
ODR can be recovered offline by regressing index against batch time -- which
matters, because the datasheet specifies no tolerance at all on the part's
internal clock. Writing derived per-sample timestamps now would bake in a nominal
rate we have no basis for and lose the evidence needed to correct it.

Configuration (all optional):
  POD_ACCEL_DEVICES   comma list of `label:/dev/spidevN.M`; empty disables.
                      e.g. camera:/dev/spidev0.0,arm:/dev/spidev0.1
  POD_ACCEL_ODR_HZ    output data rate (default: 3200)
  POD_ACCEL_RANGE_G   2 | 4 | 8 | 16 (default: 16)
  POD_ACCEL_SPI_HZ    SPI clock (default: 1500000 -- see POP_NOTE)
  POD_ACCEL_POLL_HZ   FIFO poll rate (default: 200)
  POD_ACCEL_DIR       output dir (default: /captures)
  POD_ACCEL_SEPARATION_M  camera-to-arm baseline, recorded in the manifest
  POD_NODE_NAME / POD_SESSION   shared with capture.py
"""

import datetime as dt
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

import spidev

SCHEMA = 1

# --- registers (ADXL345 Rev. G, Table 19) ---
REG_DEVID = 0x00
REG_BW_RATE = 0x2C
REG_POWER_CTL = 0x2D
REG_INT_ENABLE = 0x2E
REG_INT_SOURCE = 0x30
REG_DATA_FORMAT = 0x31
REG_DATAX0 = 0x32
REG_FIFO_CTL = 0x38
REG_FIFO_STATUS = 0x39

DEVID_EXPECTED = 0xE5
SPI_READ = 0x80
SPI_MULTIBYTE = 0x40

POWER_CTL_MEASURE = 0x08
DATA_FORMAT_FULL_RES = 0x08
DATA_FORMAT_SELF_TEST = 0x80
FIFO_CTL_STREAM = 0x80  # FIFO_CTL bits 7:6 = 10 -> stream mode
INT_SOURCE_OVERRUN = 0x01
FIFO_DEPTH = 32
AXES = 3
BYTES_PER_SAMPLE = 6

# ODR -> BW_RATE rate code (datasheet Table 6). Bandwidth is half the ODR.
ODR_CODES = {
    3200: 0x0F, 1600: 0x0E, 800: 0x0D, 400: 0x0C, 200: 0x0B,
    100: 0x0A, 50: 0x09, 25: 0x08,
}
RANGE_CODES = {2: 0x00, 4: 0x01, 8: 0x02, 16: 0x03}

# In FULL_RES the scale factor is a constant 3.9 mg/LSB at EVERY range -- the bit
# depth grows instead (10 bits at 2 g, 13 at 16 g). So +/-16 g costs nothing in
# resolution and buys headroom against clipping at the arm tip, which is why it is
# the default. Datasheet Table 1, "SENSITIVITY".
SCALE_MG_PER_LSB = 3.9

# POP_NOTE: "there must be at least 5 us between the end of reading the data
# registers and the start of a new read of the FIFO... For SPI operation at 1.6 MHz
# or less, the register addressing portion of the transmission is a sufficient
# delay." Staying at or below 1.6 MHz makes the CS-deassert dance unnecessary.
# A 32-sample drain is 32 x 7 bytes = 1.2 ms at 1.5 MHz against a FIFO that takes
# 10 ms to fill at 3200 Hz -- comfortable even with two sensors on one bus.
SPI_HZ_NO_POP_DELAY = 1600000

# POLL_NOTE: the FIFO is 32 samples deep, so it fills in FIFO_DEPTH/ODR seconds --
# 10 ms at 3200 Hz. Polling faster than ~100 Hz cannot overrun. Default 200 Hz
# leaves 2x margin for scheduling jitter on a loaded Zero.

# Self-test limits, datasheet Table 1: the output change with SELF_TEST set,
# in g, valid across the whole 2.0-3.6 V supply range. Requires ODR >= 100 Hz.
SELF_TEST_LIMITS_G = {"x": (0.20, 2.10), "y": (-2.10, -0.20), "z": (0.30, 3.40)}

_stop = False


def _request_stop(signum, _frame):
    global _stop
    _stop = True
    print(f"accel: signal {signum}, stopping", flush=True)


def _env_int(name, default):
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name, default):
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _s16(lo, hi):
    """Little-endian signed 16-bit, as the part presents right-justified data."""
    v = lo | (hi << 8)
    return v - 65536 if v & 0x8000 else v


class Adxl345:
    """One ADXL345 on a spidev node, in FIFO stream mode."""

    def __init__(self, label, path, spi_hz, odr_hz, range_g):
        self.label = label
        self.path = path
        self.odr_hz = odr_hz
        self.range_g = range_g
        bus, dev = (int(x) for x in path.rsplit("spidev", 1)[1].split("."))
        self.spi = spidev.SpiDev()
        self.spi.open(bus, dev)
        self.spi.mode = 3  # CPOL=1, CPHA=1 -- datasheet "SPI" section
        self.spi.max_speed_hz = spi_hz
        self.spi.bits_per_word = 8

    def read(self, reg, n=1):
        cmd = reg | SPI_READ | (SPI_MULTIBYTE if n > 1 else 0)
        return self.spi.xfer2([cmd] + [0] * n)[1:]

    def write(self, reg, value):
        self.spi.xfer2([reg & 0x3F, value])

    def devid(self):
        return self.read(REG_DEVID)[0]

    def configure(self):
        self.write(REG_POWER_CTL, 0x00)  # standby while configuring
        self.write(REG_BW_RATE, ODR_CODES[self.odr_hz])
        self.write(REG_DATA_FORMAT, DATA_FORMAT_FULL_RES | RANGE_CODES[self.range_g])
        self.write(REG_INT_ENABLE, 0x00)  # no interrupts -- we poll
        self.write(REG_FIFO_CTL, FIFO_CTL_STREAM | (FIFO_DEPTH - 1))
        self.write(REG_POWER_CTL, POWER_CTL_MEASURE)

    def read_one(self):
        """A single X/Y/Z sample straight from the data registers."""
        d = self.read(REG_DATAX0, BYTES_PER_SAMPLE)
        return (_s16(d[0], d[1]), _s16(d[2], d[3]), _s16(d[4], d[5]))

    def self_test(self):
        """Actuate the sensor electrostatically and check it moves.

        Proves the part is alive, wired, and mechanically responding -- the cheap
        catch for a cold joint or a dead device before the airframe leaves the
        ground, rather than after a flight of zeros. Datasheet: use ODR >= 100 Hz,
        and the output settles after 4/ODR.
        """
        settle = 8.0 / self.odr_hz
        fmt = DATA_FORMAT_FULL_RES | RANGE_CODES[self.range_g]

        def _mean(n=16):
            acc = [0, 0, 0]
            for _ in range(n):
                s = self.read_one()
                acc = [a + b for a, b in zip(acc, s)]
                time.sleep(1.0 / self.odr_hz)
            return [a / n for a in acc]

        self.write(REG_FIFO_CTL, 0x00)  # bypass; self-test reads data registers
        time.sleep(settle)
        off = _mean()
        self.write(REG_DATA_FORMAT, fmt | DATA_FORMAT_SELF_TEST)
        time.sleep(settle)
        on = _mean()
        self.write(REG_DATA_FORMAT, fmt)
        time.sleep(settle)
        self.write(REG_FIFO_CTL, FIFO_CTL_STREAM | (FIFO_DEPTH - 1))

        delta_g = {
            axis: (on[i] - off[i]) * SCALE_MG_PER_LSB / 1000.0
            for i, axis in enumerate(("x", "y", "z"))
        }
        passed = {
            axis: SELF_TEST_LIMITS_G[axis][0] <= v <= SELF_TEST_LIMITS_G[axis][1]
            for axis, v in delta_g.items()
        }
        return {"delta_g": delta_g, "pass": passed, "all_pass": all(passed.values())}

    def drain(self):
        """Read every queued FIFO sample. Returns (samples, entries, overrun)."""
        entries = self.read(REG_FIFO_STATUS)[0] & 0x3F
        if entries == 0:
            return [], 0, False
        # Only pay for INT_SOURCE when the FIFO came back full; reading it also
        # clears the latched overrun bit, which is what we want while polling.
        overrun = False
        if entries >= FIFO_DEPTH:
            overrun = bool(self.read(REG_INT_SOURCE)[0] & INT_SOURCE_OVERRUN)
        samples = [self.read_one() for _ in range(entries)]
        return samples, entries, overrun

    def close(self):
        try:
            self.write(REG_POWER_CTL, 0x00)
        finally:
            self.spi.close()


class Writer:
    """Append-only JSONL, flushed per record.

    The `.feat` lesson from #89: a power cut should cost one record, not the file.
    flush() every record puts it in the kernel; fsync() at 1 Hz gets it to the
    card without paying an SD sync 200 times a second.
    """

    def __init__(self, path, header):
        self.path = path
        self.fh = open(path, "a", buffering=1)
        self._emit(header)
        self._last_sync = time.monotonic()

    def _emit(self, obj):
        self.fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self.fh.flush()

    def record(self, obj):
        self._emit(obj)
        now = time.monotonic()
        if now - self._last_sync >= 1.0:
            os.fsync(self.fh.fileno())
            self._last_sync = now

    def close(self):
        try:
            os.fsync(self.fh.fileno())
        finally:
            self.fh.close()


def _parse_devices(raw):
    out = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        label, _, path = item.partition(":")
        if not path:
            print(f"accel: ignoring {item!r} -- expected label:/dev/spidevN.M", flush=True)
            continue
        out.append((label.strip(), path.strip()))
    return out


def main():
    devices = _parse_devices(os.getenv("POD_ACCEL_DEVICES"))
    if not devices:
        print("accel: POD_ACCEL_DEVICES empty, nothing to log", flush=True)
        return 0

    odr = _env_int("POD_ACCEL_ODR_HZ", 3200)
    range_g = _env_int("POD_ACCEL_RANGE_G", 16)
    spi_hz = _env_int("POD_ACCEL_SPI_HZ", 1500000)
    poll_hz = _env_float("POD_ACCEL_POLL_HZ", 200.0)
    out_dir = Path(os.getenv("POD_ACCEL_DIR", "/captures"))
    node = os.getenv("POD_NODE_NAME") or socket.gethostname()
    session = os.getenv("POD_SESSION") or dt.datetime.now(dt.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    if odr not in ODR_CODES:
        print(f"accel: ODR {odr} not one of {sorted(ODR_CODES)}", file=sys.stderr)
        return 2
    if range_g not in RANGE_CODES:
        print(f"accel: range {range_g}g not one of {sorted(RANGE_CODES)}", file=sys.stderr)
        return 2
    if spi_hz > SPI_HZ_NO_POP_DELAY:
        print(
            f"accel: WARNING SPI at {spi_hz} Hz is above {SPI_HZ_NO_POP_DELAY} Hz. The "
            "datasheet then requires CS to be deasserted between FIFO sample reads to "
            "make up 5 us of pop delay, which this reader does not do. Samples may "
            "repeat or shear. Use 1500000 unless you have changed the drain loop.",
            flush=True,
        )
    fill_ms = 1000.0 * FIFO_DEPTH / odr
    if poll_hz < 2000.0 / fill_ms:
        print(
            f"accel: WARNING polling at {poll_hz} Hz against a FIFO that fills in "
            f"{fill_ms:.1f} ms leaves less than 2x margin; expect overruns.",
            flush=True,
        )

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    session_dir = out_dir / node / session
    session_dir.mkdir(parents=True, exist_ok=True)

    sensors, writers, counts = [], {}, {}
    for label, path in devices:
        if not os.path.exists(path):
            print(f"accel: {label}: {path} does not exist -- is dtparam=spi=on set?", flush=True)
            continue
        try:
            dev = Adxl345(label, path, spi_hz, odr, range_g)
        except Exception as exc:  # noqa: BLE001  one bad device must not sink the rest
            print(f"accel: {label}: could not open {path}: {exc}", flush=True)
            continue

        devid = dev.devid()
        if devid != DEVID_EXPECTED:
            print(
                f"accel: {label}: DEVID 0x{devid:02X}, expected 0x{DEVID_EXPECTED:02X} -- "
                "wiring, chip select, or SPI mode is wrong. Skipping this device.",
                flush=True,
            )
            dev.close()
            continue

        dev.configure()
        st = dev.self_test()
        verdict = "PASS" if st["all_pass"] else "FAIL"
        deltas = " ".join(f"{k}={v:+.2f}g" for k, v in st["delta_g"].items())
        print(f"accel: {label}: DEVID ok, self-test {verdict} ({deltas})", flush=True)
        if not st["all_pass"]:
            print(
                f"accel: {label}: self-test outside datasheet limits "
                f"{SELF_TEST_LIMITS_G}. Logging anyway -- the data is suspect, not absent.",
                flush=True,
            )

        header = {
            "schema": SCHEMA,
            "type": "header",
            "node": node,
            "session": session,
            "label": label,
            "device": path,
            "devid": devid,
            "odr_hz_nominal": odr,
            "range_g": range_g,
            "full_res": True,
            "scale_mg_per_lsb": SCALE_MG_PER_LSB,
            "spi_hz": spi_hz,
            "poll_hz": poll_hz,
            "self_test": st,
            "separation_m": os.getenv("POD_ACCEL_SEPARATION_M"),
            "started_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "note": (
                "Samples are raw LSB counts, not g. Batches carry the cumulative "
                "sample index so the effective ODR can be fitted offline; the "
                "datasheet specifies no tolerance on the part's internal clock, so "
                "odr_hz_nominal is nominal."
            ),
        }
        sensors.append(dev)
        writers[label] = Writer(session_dir / f"accel-{label}.jsonl", header)
        counts[label] = 0

    if not sensors:
        print("accel: no usable devices", flush=True)
        return 1

    print(
        f"accel: logging {len(sensors)} device(s) to {session_dir} "
        f"odr={odr} range=+/-{range_g}g spi={spi_hz} poll={poll_hz}",
        flush=True,
    )

    interval = 1.0 / poll_hz
    next_tick = time.monotonic()
    overruns = {s.label: 0 for s in sensors}
    try:
        while not _stop:
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.005))
                continue
            next_tick += interval

            for dev in sensors:
                # Stamp before the drain: the newest sample in the FIFO is closest
                # to this instant, and the read itself takes ~1 ms.
                t_boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
                t_mono = time.monotonic_ns()
                try:
                    samples, entries, overrun = dev.drain()
                except Exception as exc:  # noqa: BLE001
                    print(f"accel: {dev.label}: read failed: {exc}", flush=True)
                    continue
                if not samples:
                    continue
                if overrun:
                    overruns[dev.label] += 1
                w = writers[dev.label]
                w.record(
                    {
                        "t": "b",
                        "i": counts[dev.label],
                        "boot_ns": t_boot,
                        "mono_ns": t_mono,
                        "n": entries,
                        "ovr": overrun,
                        "x": [s[0] for s in samples],
                        "y": [s[1] for s in samples],
                        "z": [s[2] for s in samples],
                    }
                )
                counts[dev.label] += entries
    finally:
        for dev in sensors:
            try:
                dev.close()
            except Exception:  # noqa: BLE001
                pass
        for label, w in writers.items():
            w.close()
        summary = ", ".join(
            f"{lab}={counts[lab]} samples/{overruns[lab]} overruns" for lab in counts
        )
        print(f"accel: stopped ({summary})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
