#!/usr/bin/env python3
"""Checks for device-sw/cli/coord-rtc. Run by test.sh, or by hand:

    python3 device-sw/tests/test_coord_rtc.py

No hardware. A fake bus implements the DS3234's register semantics -- byte
framing, the write bit, BCD -- so what is tested is the framing and the decode,
which is where the mistakes are. The one thing a fake cannot catch is the ioctl
layer, and that is why `enable-pps` reads the register back on the real chip.
"""

import importlib.machinery
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "cli"
spec = importlib.util.spec_from_loader(
    "coord_rtc", importlib.machinery.SourceFileLoader("coord_rtc", str(CLI / "coord-rtc"))
)
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


class FakeBus:
    """A DS3234's register file over the real byte framing.

    POR defaults: control 0x1C per the datasheet, status with OSF set because a
    part that has just powered up has stopped its oscillator at some point.
    """

    def __init__(self, regs=None, writable=True):
        self.regs = bytearray(0x18)
        self.regs[cr.REG_CONTROL] = 0x1C
        self.regs[cr.REG_STATUS] = cr.SR_OSF
        for k, v in (regs or {}).items():
            self.regs[k] = v
        self.writable = writable
        self.writes = []

    def transfer(self, tx: bytes) -> bytes:
        addr = tx[0]
        if addr & cr.WRITE:
            reg = addr & 0x7F
            self.writes.append((addr, tx[1]))
            if self.writable:
                self.regs[reg] = tx[1]
            return bytes(len(tx))
        # A read echoes nothing useful in the first byte; the data follows.
        n = len(tx) - 1
        return b"\x00" + bytes(self.regs[addr:addr + n])

    def close(self):
        pass


# 1. The write bit. The other SPI part in this repo (ADXL345) uses 0x80 to mean
# READ, so taking the convention from its driver would address register 0x0E for
# a write and silently read instead.
bus = FakeBus()
cr.Ds3234(bus).enable_pps()
check("a control-register write addresses 0x8e", bus.writes == [(0x8E, 0x00)], str(bus.writes))

# 2. A read does not set the write bit, and lands on the register asked for.
bus = FakeBus({cr.REG_SECONDS: 0x42})
check("a read uses the bare address", cr.Ds3234(bus).read(cr.REG_SECONDS)[0] == 0x42)

# 3. enable-pps leaves the chip in the state the PPS line needs.
bus = FakeBus()
check("enable-pps reads back 0x00", cr.Ds3234(bus).enable_pps() == 0x00)

# 4. A chip that does not take the write is reported, not assumed. This is the
# case the readback exists for: unpowered, miswired, or no chip select.
bus = FakeBus(writable=False)
out = io.StringIO()
with redirect_stdout(out):
    rc = cr.cmd_enable_pps(cr.Ds3234(bus))
check("a write that does not stick exits non-zero", rc == 1, f"rc={rc}")

# 5. The POR state decodes as a silent pin, which is the state the board arrives
# in and is indistinguishable from a working one without this.
por = cr.decode_control(0x1C)
check("POR 0x1c is an alarm output, not a square wave",
      "pin=alarm-output" in por and "rate=8.192kHz" in por, por)
pps = cr.decode_control(0x00)
check("0x00 is a 1 Hz square wave",
      "pin=square-wave" in pps and "rate=1Hz" in pps and "osc=running" in pps, pps)
check("every RS2:RS1 value has a rate", len(cr.SQW_RATES) == 4)

# 6. BCD decode, including the two things that are not plain BCD.
t = cr.decode_time(bytes([0x03, 0x40, 0x19, 0x07, 0x11, 0x10, 0x26]))
check("24-hour time decodes", t == "2026-10-11T19:40:03", t)
t = cr.decode_time(bytes([0x03, 0x40, 0x40 | 0x20 | 0x07, 0x07, 0x11, 0x10, 0x26]))
check("12-hour PM decodes to 24-hour", t == "2026-10-11T19:40:03", t)
t = cr.decode_time(bytes([0x00, 0x00, 0x00, 0x01, 0x01, 0x80 | 0x01, 0x00]))
check("the century bit decodes", t == "2100-01-01T00:00:00", t)

# 7. status reports rather than failing, and says the two things that make a
# reading untrustworthy: a stopped oscillator and a pin that is not at 1 Hz.
bus = FakeBus()
out = io.StringIO()
with redirect_stdout(out):
    rc = cr.cmd_status(cr.Ds3234(bus))
text = out.getvalue()
check("status exits 0 on a chip with its output off", rc == 0, f"rc={rc}")
check("status names the osf", "osf=set" in text and "not" in text, text)
check("status says the pin is not at 1 Hz", "not producing 1 Hz" in text, text)

bus = FakeBus({cr.REG_CONTROL: 0x00, cr.REG_STATUS: 0x00})
out = io.StringIO()
with redirect_stdout(out):
    cr.cmd_status(cr.Ds3234(bus))
text = out.getvalue()
check("and says nothing of the sort once it is running",
      "not producing" not in text and "osf=clear" in text, text)

# 8. The ioctl request numbers, which cannot be checked against hardware here.
# struct spi_ioc_transfer is 32 bytes and SPI_IOC_MESSAGE encodes that size;
# containers/campod-camera/accel/spidev.go computes the same values for the part
# that is already working on a campod.
import struct as _struct
check("spi_ioc_transfer is 32 bytes", _struct.calcsize(cr.XFER) == 32)
check("SPI_IOC_MESSAGE(1) matches the working Go reader",
      cr.spi_ioc_message(1) == 0x40206B00, hex(cr.spi_ioc_message(1)))

# 9. Bus speed and mode stay inside what the part allows.
check("clock is at or under the 4 MHz ceiling", cr.SPI_HZ <= 4_000_000, str(cr.SPI_HZ))
check("mode is 1 or 3", cr.SPI_MODE in (1, 3), str(cr.SPI_MODE))

# 10. No argument soup: two verbs, and anything else is usage.
check("two verbs", sorted(cr.COMMANDS) == ["enable-pps", "status"], str(sorted(cr.COMMANDS)))
err = io.StringIO()
_stderr, sys.stderr = sys.stderr, err
rc = cr.main(["coord-rtc", "frobnicate"])
sys.stderr = _stderr
check("an unknown verb is usage, not a traceback", rc == 2, f"rc={rc}")

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_coord_rtc: all checks passed")
