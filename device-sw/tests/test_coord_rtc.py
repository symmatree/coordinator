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
import calendar
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
                # The chip auto-increments the address across a burst, so a
                # seven-register time write is one transfer.
                for i, b in enumerate(tx[1:]):
                    self.regs[reg + i] = b
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

# 10. No argument soup: a fixed verb set, and anything else is usage.
check("the verb set", sorted(cr.COMMANDS) == ["enable-pps", "feed", "hctosys", "status", "systohc"],
      str(sorted(cr.COMMANDS)))
err = io.StringIO()
_stderr, sys.stderr = sys.stderr, err
rc = cr.main(["coord-rtc", "frobnicate"])
sys.stderr = _stderr
check("an unknown verb is usage, not a traceback", rc == 2, f"rc={rc}")

# 11. systohc: all seven time registers in ONE transfer, addressed at 0x80, and the
# BCD it writes is the BCD the decoder reads back. One transfer because the datasheet
# wants the time registers written together, and because the write instant is what
# sets the 1 Hz phase.
import time as _time


class RecordingBus(FakeBus):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.transfers = []

    def transfer(self, tx):
        self.transfers.append(bytes(tx))
        return super().transfer(tx)


bus = RecordingBus()
out = io.StringIO()
with redirect_stdout(out):
    rc = cr.cmd_systohc(cr.Ds3234(bus))
text = out.getvalue()
check("systohc exits 0", rc == 0, f"rc={rc}")
timewrites = [x for x in bus.transfers if x[0] == cr.REG_SECONDS | cr.WRITE]
check("the time registers go in one transfer of seven", len(timewrites) == 1
      and len(timewrites[0]) == 8, str([len(x) for x in timewrites]))
check("it reports how far from the boundary the write landed", "landed" in text
      and "ms from the second it claims" in text, text)
check("osf is cleared by systohc", not bus.regs[cr.REG_STATUS] & cr.SR_OSF,
      hex(bus.regs[cr.REG_STATUS]))
# 0xc8 is the real POR-ish value seen on the hardware: OSF plus the two 32 kHz
# enables. Clearing OSF must not disturb them.
bus = RecordingBus(regs={cr.REG_STATUS: 0xC8})
with redirect_stdout(io.StringIO()):
    cr.cmd_systohc(cr.Ds3234(bus))
check("clearing osf preserves the other status bits",
      bus.regs[cr.REG_STATUS] == 0x48, hex(bus.regs[cr.REG_STATUS]))

# What it wrote decodes back to exactly the second it said it wrote. This is the
# check that caught the fake dropping six of the seven bytes.
bus2 = RecordingBus()
out2 = io.StringIO()
with redirect_stdout(out2):
    cr.cmd_systohc(cr.Ds3234(bus2))
claimed = [l for l in out2.getvalue().splitlines()
           if l.startswith("wrote")][0].split()[1].rstrip("Z")
written = cr.decode_time(bytes(bus2.regs[0:7]))
check("the written registers decode back to the claimed second",
      written == claimed, f"wrote {claimed}, registers say {written}")
check("and that second is now, not an epoch",
      abs(calendar.timegm(_time.strptime(written, "%Y-%m-%dT%H:%M:%S"))
          - _time.time()) < 10, written)

# 12. hctosys refuses a chip that says its own time is untrustworthy. A Pi 4B has no
# other RTC, so believing OSF-set time replaces a wrong clock with a confident one.
moved = []
_real_set, cr.set_system = cr.set_system, lambda ts: moved.append(ts)
bus = FakeBus()  # OSF set by default
err = io.StringIO()
_stderr, sys.stderr = sys.stderr, err
rc = cr.cmd_hctosys(cr.Ds3234(bus))
sys.stderr = _stderr
check("hctosys refuses while osf is set", rc == 1 and not moved, f"rc={rc} moved={moved}")
check("and says why", "osf set" in err.getvalue(), err.getvalue())

# With OSF clear it sets the clock from the chip, and nothing in the test touches the
# real clock -- cr.set_system is the seam.
regs = cr.time_regs(_time.gmtime(1791742065))
bus = FakeBus({cr.REG_STATUS: 0x00, **{i: regs[i] for i in range(7)}})
out = io.StringIO()
with redirect_stdout(out):
    rc = cr.cmd_hctosys(cr.Ds3234(bus))
sys.stderr = _stderr
check("hctosys sets the clock from the chip", rc == 0 and len(moved) == 1, f"rc={rc}")
check("to the chip's own time, read as UTC",
      moved and abs(moved[0] - 1791742065) < 1, str(moved))
cr.set_system = _real_set

# 13. The SOCK sample chrony will read. Size and magic are the two things that make
# chrony either accept it or log "unexpected length" and drop it forever.
msg = cr.sock_sample(1791742065, 1791742065.25)
check("the sample is 40 bytes", len(msg) == 40, str(len(msg)))
tv_s, tv_us, offset, pulse, leap, _pad, magic = _struct.unpack(cr.SOCK_SAMPLE, msg)
check("magic is chrony's", magic == 0x534F434B, hex(magic))
check("pulse is 0, because we supply whole seconds", pulse == 0)
check("leap is 0", leap == 0)
check("the measurement time is the system time of the reading",
      tv_s == 1791742065 and abs(tv_us - 250000) < 2, f"{tv_s}.{tv_us}")
check("offset is true minus system", abs(offset - (-0.25)) < 1e-6, str(offset))


# 14. chip_boundary exists because the chip has no sub-second register. It must catch
# the rollover, and it must give up rather than hang on a chip whose clock is stopped.
class TickingBus(FakeBus):
    """Seconds register advances after a set number of reads."""

    def __init__(self, flip_after=3, **kw):
        regs = cr.time_regs(_time.gmtime(1791742065))
        super().__init__(regs={i: regs[i] for i in range(7)}, **kw)
        self.reads = 0
        self.flip_after = flip_after

    def transfer(self, tx):
        if not tx[0] & cr.WRITE:
            self.reads += 1
            if self.reads > self.flip_after:
                nxt = cr.time_regs(_time.gmtime(1791742066))
                for i in range(7):
                    self.regs[i] = nxt[i]
        return super().transfer(tx)


bus = TickingBus()
t0 = _time.time()
edge = cr.chip_boundary(cr.Ds3234(bus))
check("chip_boundary catches the rollover", edge is not None)
check("and reports the second it rolled INTO, not out of",
      edge and edge[0] == 1791742066, str(edge))
check("and the instant it saw it", edge and t0 <= edge[1] <= _time.time(), str(edge))

static = FakeBus(regs={i: cr.time_regs(_time.gmtime(1791742065))[i] for i in range(7)})
t0 = _time.time()
edge = cr.chip_boundary(cr.Ds3234(static), deadline_s=0.2)
check("a stopped chip gives up rather than hanging",
      edge is None and _time.time() - t0 < 1.0, f"{edge} after {_time.time()-t0:.2f}s")

# 15. fc_offset: the FC-measured offset, and the staleness rule that keeps a previous
# flight's sample -- the file is on disk and survives a reboot -- from being applied as
# if it were current.
import tempfile as _tf2

d = _tf2.mkdtemp()
fresh = f"{d}/fc_time_fresh"
now_us = int(_time.time() * 1e6)
with open(fresh, "w") as f:
    f.write(f"{-250000} {4200} {12345678} {now_us - 250000} {now_us}\n")
got = cr.fc_offset(fresh)
check("fc_offset reads the offset and the rtt",
      got is not None and abs(got[0] - (-0.25)) < 1e-6 and abs(got[1] - 0.0042) < 1e-6,
      str(got))

stale = f"{d}/fc_time_stale"
with open(stale, "w") as f:
    f.write(f"{-250000} {4200} {12345678} {now_us} {now_us - 600_000_000}\n")
check("a stale sample is rejected", cr.fc_offset(stale) is None, str(cr.fc_offset(stale)))
check("a missing file is not an error", cr.fc_offset(f"{d}/nope") is None)
with open(f"{d}/junk", "w") as f:
    f.write("not numbers at all\n")
check("a malformed file is not an error", cr.fc_offset(f"{d}/junk") is None)

# 16. The sample carries whatever offset it is given, so the FC path and the chip path
# produce the same wire shape and only the number differs.
m_fc = cr.sock_sample_offset(-0.25, 1791742065.5)
m_chip = cr.sock_sample(1791742065, 1791742065.75)
for name, m in (("fc", m_fc), ("chip", m_chip)):
    _tvs, _tvus, off, pulse, leap, _p, magic = _struct.unpack(cr.SOCK_SAMPLE, m)
    check(f"{name} sample is a valid sock_sample",
          len(m) == 40 and magic == 0x534F434B and pulse == 0 and leap == 0)
_, _, off, _, _, _, _ = _struct.unpack(cr.SOCK_SAMPLE, m_chip)
check("the chip path still reports chip-minus-system", abs(off - (-0.75)) < 1e-6, str(off))

# 17. Nothing in the feed path writes the chip. A divider reset would step the 1 Hz
# phase and make one second the wrong length, which the campods' blanking alignment
# does not survive -- so this is a property worth asserting, not just intending.
src = (CLI / "coord-rtc").read_text()
feed_body = src[src.index("def cmd_feed"):src.index("def set_system")]
for forbidden in ("write_time", "clear_osf", "set_chip", "rtc.write("):
    check(f"cmd_feed does not call {forbidden}", forbidden not in feed_body)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_coord_rtc: all checks passed")
