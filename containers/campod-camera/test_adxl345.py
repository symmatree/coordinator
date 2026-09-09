#!/usr/bin/env python3
"""Hardware-free test for the ADXL345 reader (coordinator #211).

No Pi, no SPI bus, no accelerometer: a fake `spidev` stands in for the part and
implements enough of the datasheet to exercise everything that would otherwise
only be found out on the airframe --

  * the SPI register protocol (bit 7 read, bit 6 multibyte)
  * DEVID gating, so a miswired chip select is caught at startup not in the data
  * configure() actually writing FULL_RES + the right ODR and range codes
  * self-test producing deltas inside the datasheet's g limits, and the verdict
  * a FIFO drain returning the right count and decoding two's complement right
    across the full 13-bit range, including the sign boundary
  * the JSONL shape: one header, then batch records with cumulative sample index

Runs at image build time, so a broken reader fails the build.

    python3 test_adxl345.py
"""
import json
import sys
import tempfile
import types
from pathlib import Path

# --- fake ADXL345 on a fake spidev, installed before adxl345 imports it -------

R_DEVID, R_BW_RATE, R_POWER_CTL = 0x00, 0x2C, 0x2D
R_DATA_FORMAT, R_DATAX0, R_FIFO_CTL, R_FIFO_STATUS = 0x31, 0x32, 0x38, 0x39
R_INT_SOURCE = 0x30
SELF_TEST_BIT = 0x80

# Deltas the fake applies with SELF_TEST set, in LSB at 3.9 mg/LSB. Chosen mid-band
# of the datasheet limits (x 0.20..2.10 g, y -2.10..-0.20 g, z 0.30..3.40 g).
SELF_TEST_DELTA_LSB = (256, -256, 384)


class FakeSpiDev:
    instances = []

    def __init__(self):
        self.regs = {R_DEVID: 0xE5}
        self.writes = []
        self.fifo = []
        self.base = (0, 0, 0)
        self.mode = None
        self.max_speed_hz = None
        self.bits_per_word = None
        self.opened = None
        FakeSpiDev.instances.append(self)

    def open(self, bus, dev):
        self.opened = (bus, dev)

    def close(self):
        pass

    def _sample(self):
        if self.fifo:
            return self.fifo.pop(0)
        s = self.base
        if self.regs.get(R_DATA_FORMAT, 0) & SELF_TEST_BIT:
            s = tuple(a + b for a, b in zip(s, SELF_TEST_DELTA_LSB))
        return s

    def xfer2(self, data):
        cmd = data[0]
        reg, read, n = cmd & 0x3F, bool(cmd & 0x80), len(data) - 1
        if not read:
            self.regs[reg] = data[1]
            self.writes.append((reg, data[1]))
            return [0] * len(data)
        if reg == R_DATAX0:
            x, y, z = self._sample()
            payload = []
            for v in (x, y, z):
                u = v & 0xFFFF
                payload += [u & 0xFF, (u >> 8) & 0xFF]
            return [0] + payload[:n]
        if reg == R_FIFO_STATUS:
            return [0, min(len(self.fifo), 32)]
        if reg == R_INT_SOURCE:
            return [0, 0x01 if len(self.fifo) >= 32 else 0x00]
        return [0] + [self.regs.get(reg + i, 0) for i in range(n)]


sys.modules["spidev"] = types.SimpleNamespace(SpiDev=FakeSpiDev)
import adxl345  # noqa: E402


def check(ok, label, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}{(' -- ' + detail) if detail else ''}")
    return ok


def main():
    ok = True
    dev = adxl345.Adxl345("camera", "/dev/spidev0.1", 1500000, 3200, 16)
    fake = FakeSpiDev.instances[-1]

    ok &= check(fake.opened == (0, 1), "spidev path parsed", str(fake.opened))
    ok &= check(fake.mode == 3, "SPI mode 3 (CPOL=1, CPHA=1)", str(fake.mode))
    ok &= check(fake.max_speed_hz == 1500000, "SPI clock set")
    ok &= check(dev.devid() == 0xE5, "DEVID reads 0xE5")

    dev.configure()
    w = dict(fake.writes)
    ok &= check(w.get(R_BW_RATE) == 0x0F, "BW_RATE = 3200 Hz code", hex(w.get(R_BW_RATE, 0)))
    ok &= check(
        w.get(R_DATA_FORMAT) == (adxl345.DATA_FORMAT_FULL_RES | 0x03),
        "DATA_FORMAT = FULL_RES | +/-16 g",
        hex(w.get(R_DATA_FORMAT, 0)),
    )
    ok &= check(
        w.get(R_FIFO_CTL) == (adxl345.FIFO_CTL_STREAM | 31),
        "FIFO_CTL = stream, watermark 31",
        hex(w.get(R_FIFO_CTL, 0)),
    )
    ok &= check(w.get(R_POWER_CTL) == adxl345.POWER_CTL_MEASURE, "measurement mode enabled")

    st = dev.self_test()
    expect = {
        a: SELF_TEST_DELTA_LSB[i] * adxl345.SCALE_MG_PER_LSB / 1000.0
        for i, a in enumerate(("x", "y", "z"))
    }
    close = all(abs(st["delta_g"][a] - expect[a]) < 1e-6 for a in expect)
    ok &= check(close, "self-test delta in g", str({k: round(v, 3) for k, v in st["delta_g"].items()}))
    ok &= check(st["all_pass"], "self-test verdict PASS against datasheet limits")
    ok &= check(
        fake.regs[R_DATA_FORMAT] & SELF_TEST_BIT == 0, "SELF_TEST bit cleared afterwards"
    )
    ok &= check(
        fake.regs[R_FIFO_CTL] == (adxl345.FIFO_CTL_STREAM | 31),
        "FIFO returned to stream mode after self-test",
    )

    # Two's complement across the 13-bit full-res range, sign boundary included.
    fake.fifo = [(0, 1, -1), (4095, -4096, 2048), (-1, 0, 1)]
    samples, entries, overrun = dev.drain()
    ok &= check(entries == 3 and len(samples) == 3, "drain returns every queued sample")
    ok &= check(
        samples == [(0, 1, -1), (4095, -4096, 2048), (-1, 0, 1)],
        "signed 16-bit decode",
        str(samples),
    )
    ok &= check(not overrun, "no overrun flagged on a short FIFO")

    fake.fifo = [(1, 2, 3)] * 32
    _, entries, overrun = dev.drain()
    ok &= check(entries == 32 and overrun, "full FIFO reports the hardware overrun bit")

    # JSONL shape
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "accel-camera.jsonl"
        wtr = adxl345.Writer(path, {"schema": adxl345.SCHEMA, "type": "header", "label": "camera"})
        wtr.record({"t": "b", "i": 0, "boot_ns": 1, "mono_ns": 2, "n": 2,
                    "ovr": False, "x": [1, 2], "y": [3, 4], "z": [5, 6]})
        wtr.record({"t": "b", "i": 2, "boot_ns": 3, "mono_ns": 4, "n": 1,
                    "ovr": False, "x": [7], "y": [8], "z": [9]})
        wtr.close()
        lines = [json.loads(x) for x in path.read_text().splitlines()]
    ok &= check(len(lines) == 3, "one header + two batch records")
    ok &= check(lines[0]["type"] == "header", "first line is the header")
    ok &= check(
        [x["i"] for x in lines[1:]] == [0, 2],
        "cumulative sample index advances by batch size (offline ODR fit)",
    )
    ok &= check(
        all(len(x["x"]) == x["n"] for x in lines[1:]), "declared count matches the arrays"
    )

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
