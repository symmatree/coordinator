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
import tempfile
from contextlib import redirect_stderr, redirect_stdout
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

# A chip with no VCC does not drive MISO: every register, status included, reads 0x00,
# so OSF reads clear and the time is month 0, day 0. hctosys refuses it and says why,
# rather than raising ValueError out of strptime (2026-10-30, hub board dark).
moved.clear()
bus = FakeBus({cr.REG_STATUS: 0x00, **{i: 0 for i in range(7)}})
err = io.StringIO()
sys.stderr = err
rc = cr.cmd_hctosys(cr.Ds3234(bus))
sys.stderr = _stderr
check("hctosys refuses an all-zero read", rc == 1 and not moved, f"rc={rc} moved={moved}")
check("and says it is not a date", "not a date" in err.getvalue(), err.getvalue())
check("chip_seconds is None for an all-zero read", cr.chip_seconds(bytes(7)) is None)
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


src = (CLI / "coord-rtc").read_text()

# 15. The sample reports the CHIP, always. Reporting UTC here would make chrony's
# reference a different clock from the pulse it is locked to, and the difference would
# drift at the chip's 2 ppm until it crossed the 0.4 s tolerance and every pulse was
# discarded. This is the property the whole design turns on, so it is asserted.
m_chip = cr.sock_sample(1791742065, 1791742065.75)
_tvs, _tvus, off, pulse, leap, _p, magic = _struct.unpack(cr.SOCK_SAMPLE, m_chip)
check("the sample is a valid sock_sample",
      len(m_chip) == 40 and magic == 0x534F434B and pulse == 0 and leap == 0)
check("offset is the chip's reading minus system time", abs(off - (-0.75)) < 1e-6, str(off))
# The FC offset exists, but only to set the chip. It must never become the sample
# chrony sees -- that is the error that puts a drifting phase difference between
# chrony's reference and the pulse it is locked to.
_feed = src[src.index("def cmd_feed"):src.index("def set_system")]
check("the only thing sent to chrony is the chip sample",
      _feed.count("sendto(") == 1 and "sendto(sock_sample(chip_s, at)" in _feed,
      "something other than the chip sample reaches the socket")

# 17a. A chip with no epoch must not be fed to chrony: it reads 2000-01-01, so the
# offset would be about -26 years.
_body = src[src.index("def cmd_feed"):src.index("def set_system")]
check("the feed checks osf before using the chip's label",
      "SR_OSF" in _body, "osf guard missing from cmd_feed")

# 17. The gate on writing the chip. A write re-phases the 1 Hz and makes one second the
# wrong length, and that signal is what libcamera's SyncMode aligns the pods' shutters
# against -- so this is the safety property of the design and it is asserted rather than
# intended. It had no test at all until now: the assertions that used to be here checked
# that cmd_feed never wrote the chip, which stopped being true when the FC started
# correcting the epoch, and their replacement was never added.
check("armed: never, whatever the error",
      cr.should_set_chip(False, 10.0, True) is False)
check("armed with no epoch: still never",
      cr.should_set_chip(True, 10.0, True) is False)
check("no FC sample: never, nothing else knows the time of day",
      cr.should_set_chip(False, None, False) is False
      and cr.should_set_chip(True, None, False) is False)
check("disarmed with no epoch: always, there is no phase to protect yet",
      cr.should_set_chip(True, 0.0, False) is True)
check("disarmed, inside the debounce: no",
      cr.should_set_chip(False, cr.FC_CORRECT_OVER_S * 0.9, False) is False)
check("disarmed, past the debounce: yes, either sign",
      cr.should_set_chip(False, cr.FC_CORRECT_OVER_S * 1.1, False) is True
      and cr.should_set_chip(False, -cr.FC_CORRECT_OVER_S * 1.1, False) is True)

# The quantity compared is the CHIP's error, not our clock's offset. Our clock is only a
# proxy for the chip while chronyd is up and converged onto the chip; before it steps at
# boot, or with chronyd stopped, the two are independently wrong. That is the point: the
# decision is about the chip, so it should not rest on the state of a daemon.
check("a drifted chip is corrected even when our own clock is near truth",
      cr.should_set_chip(False, 10.0, False) is True)

# The debounce has to be loose enough that one pre-arm period sees at most one correction.
# At 2 ppm the error grows ~0.17 s/day, so the tolerance sets the interval; a few ms would
# re-phase the pulse repeatedly.
check("the debounce is hours of drift, not seconds",
      cr.FC_CORRECT_OVER_S / 2e-6 > 3600,
      f"{cr.FC_CORRECT_OVER_S}s -> {cr.FC_CORRECT_OVER_S / 2e-6 / 3600:.1f}h")

# An unreadable arm flag counts as ARMED: the only thing it gates is a write that would
# disturb capture, so not knowing is not a reason to risk it.
check("an unreadable arm flag reads as armed", cr.armed("/nonexistent/fc_armed") is True)
_armfile = Path(tempfile.mkdtemp()) / "fc_armed"
_armfile.write_text("0\n")
check("a 0 arm flag reads as disarmed", cr.armed(str(_armfile)) is False)
_armfile.write_text("1\n")
check("a 1 arm flag reads as armed", cr.armed(str(_armfile)) is True)

# ---- 17b. the FC sample is against the raw clock (#593) -------------------------------
#
# The system clock follows the chip, so an FC offset measured against it went stale the
# moment it was used to write the chip: chrony slewed realtime toward the new chip time,
# the next pass read that slew as chip error, and with a GPS fix the chip was rewritten
# every few seconds. The sample is now UTC minus CLOCK_MONOTONIC_RAW, tagged with the boot.
_fcfile = Path(tempfile.mkdtemp()) / "fc_time"
_raw_us = int(cr.raw_now() * 1e6)
_fcfile.write_text(f"1791858443000000 3000 {_raw_us} {cr.boot_id()}\n")
check("a fresh raw-clock sample from this boot is used",
      cr.fc_offset(str(_fcfile)) == (1791858443.0, 0.003), str(cr.fc_offset(str(_fcfile))))
_fcfile.write_text(f"1791858443000000 3000 {_raw_us} not-this-boot\n")
check("a sample from another boot is refused -- the raw clock restarts at boot",
      cr.fc_offset(str(_fcfile)) is None)
_fcfile.write_text(f"1791858443000000 3000 {_raw_us - int((cr.FC_MAX_AGE_S + 1) * 1e6)} "
                   f"{cr.boot_id()}\n")
check("a stale sample is refused", cr.fc_offset(str(_fcfile)) is None)
_fcfile.write_text(f"1791858443000000 3000 {_raw_us + 10_000_000} {cr.boot_id()}\n")
check("a sample from the future of this boot's raw clock is refused",
      cr.fc_offset(str(_fcfile)) is None)
_fcfile.write_text(f"-6065 6329 {int(_time.time() * 1e6)}\n")
check("the realtime format from before #593 is refused, not misread",
      cr.fc_offset(str(_fcfile)) is None)

# THE property. A chip that is right reads as right however far chrony has slewed the
# system clock since the FC sample: moving realtime moves `at` and `real_now` together, and
# only their difference reaches the answer.
_U = 1791858443.0          # UTC minus raw
_R = 1000.2                # raw clock now; the edge was 0.2 s ago, at raw 1000.0
_chip = 1791859443         # = 1000.0 + _U, a chip with no error
for _slew in (0.0, 0.17, -0.17, 1.685):
    _real_now = 1791859443.2 + _slew
    check(f"a correct chip reads as correct with realtime moved {_slew:+.3f} s",
          abs(cr.chip_error_s(_chip, _real_now - 0.2, _real_now, _R, _U)) < 1e-9)
check("a chip that is off reads as off by that much",
      abs(cr.chip_error_s(_chip - 1, 1791859443.0, 1791859443.2, _R, _U) + 1.0) < 1e-9)


# set_chip times its write on the clock it is handed. The FC path hands it the raw clock,
# so the instant it aims for cannot be moved by chrony either. A fake clock nowhere near
# realtime proves the target comes from that clock and not from time.time().
class _FakeClock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        self.t += 0.001   # every read advances, so the spin terminates
        return self.t

    def sleep(self, s):
        self.t += s


_clk = _FakeClock(100.25)
_saved_sleep = cr.time.sleep
cr.time.sleep = _clk.sleep
bus = RecordingBus()
_target, _err, _sr = cr.set_chip(cr.Ds3234(bus), 1791859343.0, clock=_clk)
cr.time.sleep = _saved_sleep
check("set_chip aims at clock() + offset, two seconds out",
      _target == 1791859445, str(_target))
check("and the registers it wrote decode to that second",
      cr.decode_time(bytes(bus.regs[0:7])) == _time.strftime(
          "%Y-%m-%dT%H:%M:%S", _time.gmtime(1791859445)),
      cr.decode_time(bytes(bus.regs[0:7])))
check("and it landed on that clock's boundary", 0 <= _err < 0.01, f"{_err}")

_feed_src = (CLI / "coord-rtc").read_text()
_feed_src = _feed_src[_feed_src.index("def cmd_feed"):_feed_src.index("def set_system")]
check("the feed's chip write is timed on the raw clock",
      "set_chip(rtc, offset, clock=raw_now)" in _feed_src)

# ---- 18. the second boundary comes off the pulse --------------------------------------
#
# The instant is the kernel's timestamp of the falling edge; we only supply the label. So
# the risks are in the pairing, not in the timing: a stale sequence number read as a new
# edge, and a label read so late that the register has rolled again and the label belongs
# to the next second. Both would be a whole second wrong, which is the one error size this
# design cannot absorb -- chrony folds a PPS into +/-0.5 s.
#
# There is no fallback path to test. Without the edge there is no sample, and the feed says
# so: the chip's label alone leaves the second ambiguous, which is the error it cannot
# absorb. A chip reachable over SPI whose 1 Hz is not reaching GPIO24 is not a wiring state
# the board has -- both come off the same distribution board.

_ppsdir = Path(tempfile.mkdtemp())
_ppsfile = _ppsdir / "assert"


class _LabelRtc:
    """Returns a fixed second as the seven time registers."""

    def __init__(self, second="2026-10-13T02:44:03"):
        self.second = second

    def read(self, reg, n=1):
        t = _time.strptime(self.second, "%Y-%m-%dT%H:%M:%S")
        def bcd(v):
            return ((v // 10) << 4) | (v % 10)
        return bytes([bcd(t.tm_sec), bcd(t.tm_min), bcd(t.tm_hour), 0x01,
                      bcd(t.tm_mday), bcd(t.tm_mon), bcd(t.tm_year - 2000)])


_ppsfile.write_text("1791860357.000005475#1174\n")
check("an assert reads as (instant, sequence)",
      cr.pps_assert(str(_ppsfile)) == (1791860357.000005475, 1174))
check("a missing pps device is None, not a crash",
      cr.pps_assert(str(_ppsdir / "nope")) is None)
_ppsfile.write_text("")
check("a device present but never fired is None",
      cr.pps_assert(str(_ppsfile)) is None)
_ppsfile.write_text("garbage\n")
check("an unparseable assert is None rather than a bogus instant",
      cr.pps_assert(str(_ppsfile)) is None)

# A sequence that never advances is an edge that never came: it must time out, not hand
# back the stale one as if it were fresh.
_ppsfile.write_text("1791860357.000005475#1174\n")
check("a sequence that never advances times out",
      cr.pulse_boundary(_LabelRtc(), deadline_s=0.2, path=str(_ppsfile)) is None)


# A fresh edge: the returned instant must be the KERNEL's, to the nanosecond, not our
# reading of the clock -- that is the entire reason for taking this path.
def _advance_then(_s, _f=_ppsfile):
    _f.write_text(f"{_time.time():.9f}#1175\n")


_ppsfile.write_text("1791860357.000005475#1174\n")
_saved_sleep = cr.time.sleep
cr.time.sleep = _advance_then
_edge = cr.pulse_boundary(_LabelRtc(), deadline_s=1.0, path=str(_ppsfile))
cr.time.sleep = _saved_sleep
check("a fresh edge yields the chip's label and the kernel's instant",
      _edge is not None
      and _edge[0] == calendar.timegm(_time.strptime("2026-10-13T02:44:03",
                                                     "%Y-%m-%dT%H:%M:%S")),
      str(_edge))

# THE failure mode: noticing the edge is free, but reading the label late is not. Past the
# window the register may hold the NEXT second, so the pairing must be refused.
_ppsfile.write_text("1791860357.000005475#1174\n")


def _advance_but_stale(_s, _f=_ppsfile):
    _f.write_text(f"{_time.time() - cr.PPS_LABEL_WINDOW_S - 0.1:.9f}#1176\n")


cr.time.sleep = _advance_but_stale
_late = cr.pulse_boundary(_LabelRtc(), deadline_s=0.3, path=str(_ppsfile))
cr.time.sleep = _saved_sleep
check("an edge noticed too late to label safely is refused, not guessed",
      _late is None, str(_late))

check("the label window is under a second, or it could not tell seconds apart",
      0 < cr.PPS_LABEL_WINDOW_S < 1.0, f"{cr.PPS_LABEL_WINDOW_S}s")
check("the retry wait is a backoff, not a sample period, and is bounded",
      0 < cr.RETRY_PERIOD_S <= 5.0, f"{cr.RETRY_PERIOD_S}s")


# ---- 19. the feed says it is feeding, once -------------------------------------------
#
# The risk this covers is both directions of a one-shot log line: silence, which is what
# sent us chasing a working feed as if it were broken, and a line per sample, which at one
# a minute would bury the journal in notice that nothing changed. So: exactly one line,
# and the sends keep going after it.

import socket as _socket

_sockdir = tempfile.mkdtemp()
_sockpath = str(Path(_sockdir) / "chrony.sock")
_srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
_srv.bind(_sockpath)
cr.CHRONY_SOCK = _sockpath


class _OsfClearRtc:
    """Status register with OSF clear, which is all cmd_feed reads it for."""

    def read(self, reg, n=1):
        return bytes(n)


class _Stop(Exception):
    pass


# The loop is stopped from the PULSE, not from sleep, because a successful pass must not
# sleep at all: the next pass blocks on the next edge, and that is what paces it at 1 Hz.
# Sleeping as well made it one sample per 2 s, which is under the four-per-poll a SOCK
# refclock's filter needs -- the reach register showed it and chrony never filled it.
_edges = {"n": 0}


def _four_edges(rtc, deadline_s=2.0, path=None):
    _edges["n"] += 1
    if _edges["n"] > 4:
        raise _Stop
    return (1791859443, 1791859443.846)


_slept = []
_status_dir = tempfile.mkdtemp()
cr.RTC_STATUS_FILE = str(Path(_status_dir) / "rtc_status")
cr.pulse_boundary = _four_edges
cr.fc_offset = lambda: None
cr.armed = lambda path=None: True          # so no path here can write the chip
cr.time.sleep = lambda s: _slept.append(s)

_out = io.StringIO()
with redirect_stdout(_out):
    try:
        cr.cmd_feed(_OsfClearRtc())
    except _Stop:
        pass

_srv.settimeout(0.2)
_datagrams = 0
try:
    while True:
        _srv.recv(64)
        _datagrams += 1
except TimeoutError:
    pass

_lines = [ln for ln in _out.getvalue().splitlines() if ln.strip()]
check("the feed sends a sample every pass", _datagrams == 4, f"{_datagrams} datagrams")
check("and a successful pass does not sleep -- the pulse is what paces it",
      _slept == [], f"slept {_slept}")
# One one-shot line over four passes. Four rather than one is the point: at 1 Hz a line
# per sample would be a line a second saying nothing changed.
check("the feed says so once over four passes, not per sample", len(_lines) == 1,
      f"{len(_lines)} lines: {_lines}")
check("and the line carries the offset chrony will show",
      bool(_lines) and "-0.846 s" in _lines[0], _lines[0] if _lines else "(no line)")

# cmd_feed's only boundary source is the pulse, and this section patched pulse_boundary --
# so the four datagrams above are themselves the check that it still goes through it.

import json as _json

_doc = _json.loads(Path(cr.RTC_STATUS_FILE).read_text())
check("every feed pass publishes the chip's state for the bus status",
      _doc.get("edge") is True and _doc.get("state") == "no_response"
      and _doc.get("boot_id") == cr.boot_id(), str(_doc))


# `edge` is whether the 1 Hz arrived, which is the kernel's sequence number moving -- not
# whether a sample came out. On 2026-11-04 the coordinator had 2583 edges and a chip that
# read all zeros, and the feed said "no edge" and published edge: false.
def _feed_once(seqs):
    """One feed pass with no sample out of pulse_boundary, the assert file reading `seqs`
    in turn. Returns (published edge, the journal line)."""
    reads = iter(seqs)
    cr.pps_assert = lambda path=None: next(reads)

    def _one(rtc, deadline_s=2.0, path=None):
        if _edges["n"]:
            raise _Stop
        _edges["n"] = 1
        return None

    _edges["n"] = 0
    cr.pulse_boundary = _one
    _err = io.StringIO()
    with redirect_stderr(_err):
        try:
            cr.cmd_feed(_OsfClearRtc())
        except _Stop:
            pass
    return (_json.loads(Path(cr.RTC_STATUS_FILE).read_text()).get("edge"),
            _err.getvalue().strip())


_pps_real = cr.pps_assert
_e, _line = _feed_once([(100.0, 7), (101.0, 8), (101.0, 8)])
check("edges arriving with no sample is edge: true", _e is True, str(_e))
check("and the journal says the chip's read failed, not the pulse",
      "arriving" in _line and "no edge" not in _line, _line)
_e, _line = _feed_once([(100.0, 7), (100.0, 7), (100.0, 7)])
check("a sequence number that does not move is edge: false", _e is False, str(_e))
check("and the journal says no edge", "no edge" in _line, _line)
_e, _line = _feed_once([None, None, None])
check("no assert file at all is edge: false", _e is False, str(_e))
cr.pps_assert = _pps_real

# ---- 19. the chip's state on the bus -------------------------------------------------
#
# Three answers, from the registers alone: nothing driving MISO, a chip that answers but
# has no trustworthy time, and a chip with a date and OSF clear.

check("all 0x00 is no chip: month 0 is not a date",
      cr.chip_state(bytes(7), 0x00, 0x00) == "no_response")
check("all 0xFF is no chip either",
      cr.chip_state(b"\xff" * 7, 0xFF, 0xFF) == "no_response")
_good = cr.time_regs(_time.gmtime(1791859443))
check("a date with OSF clear is ok", cr.chip_state(_good, cr.CR_PPS, 0x00) == "ok")
check("the same date with OSF set is reset",
      cr.chip_state(_good, cr.CR_PPS, cr.SR_OSF) == "reset")
check("OSF clear but not a date is reset, not ok",
      cr.chip_state(bytes([0, 0, 0, 1, 0, 0, 0]), 0x1C, 0x00) == "reset")
# The power-on chip: control 0x1C, OSF set, time registers zero. It answers, so it is not
# no_response, and it has no epoch, so it is not ok.
check("a chip just powered from cold reads reset",
      cr.chip_state(bytes(7), 0x1C, cr.SR_OSF) == "reset")


class _DeadSpi:
    def read(self, reg, n=1):
        raise OSError(5, "Input/output error")


cr.publish_chip_state(_DeadSpi(), False)
_doc = _json.loads(Path(cr.RTC_STATUS_FILE).read_text())
check("an SPI error is published as no_response with the error, not raised",
      _doc.get("state") == "no_response" and "Input/output" in _doc.get("error", "")
      and _doc.get("edge") is False, str(_doc))

cr.publish_chip_state(cr.Ds3234(FakeBus({0: 0x03, 4: 0x02, 5: 0x11, 6: 0x26})), True)
_doc = _json.loads(Path(cr.RTC_STATUS_FILE).read_text())
check("a chip from cold over the real register framing reads reset, with its time",
      _doc.get("state") == "reset" and _doc.get("osf") is True
      and _doc.get("time") == "2026-11-02T00:00:03", str(_doc))

cr.RTC_STATUS_FILE = str(Path(_status_dir) / "missing" / "rtc_status")
_err = io.StringIO()
import contextlib as _contextlib
with _contextlib.redirect_stderr(_err):
    cr.publish_chip_state(cr.Ds3234(FakeBus()), True)
    cr.publish_chip_state(cr.Ds3234(FakeBus()), True)
check("a status file that cannot be written does not stop the feed, and says so once",
      _err.getvalue().count("cannot write") == 1, repr(_err.getvalue()))
cr.RTC_STATUS_FILE = str(Path(_status_dir) / "rtc_status")

# ---- 20. chrony on the PPS: the upper-case S -------------------------------------------
#
# `chronyc -c tracking` leads with the selected source's refid in hex, then its name,
# which for a refclock is the refid. A campod still on its NTP server shows that server's
# address; an unsynchronised chrony shows no name at all.
import subprocess as _subprocess

_run_real = cr.subprocess.run
_asked = []


def _chronyc(stdout=None, raises=None):
    def run(argv, **kw):
        _asked.append(argv)
        if raises:
            raise raises
        return _subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
    cr.subprocess.run = run


_chronyc("50505300,PPS,1,1793995200.000123,-0.000000012,0.000000034\n")
check("chrony on the PPS refclock is selected", cr.chrony_on_pps() is True)
check("and chronyc is asked not to resolve names, since a campod has no DNS",
      bool(_asked) and "-n" in _asked[-1], str(_asked))
_chronyc("0A370001,10.55.0.1,11,1793995200.000123,0.004\n")
check("chrony on the coordinator's NTP is not", cr.chrony_on_pps() is False)
_chronyc("44533332,DS32,1,1793995200.0\n")
check("chrony on the chip's seconds alone is not", cr.chrony_on_pps() is False)
_chronyc("00000000,,0,0.0\n")
check("chrony with no source is not", cr.chrony_on_pps() is False)
_chronyc("")
check("chronyc saying nothing is unknown, not false", cr.chrony_on_pps() is None)
_chronyc(raises=FileNotFoundError("chronyc"))
check("no chronyc is unknown", cr.chrony_on_pps() is None)
_chronyc(raises=_subprocess.TimeoutExpired("chronyc", 2))
check("a chronyc that hangs is unknown", cr.chrony_on_pps() is None)

_chronyc("50505300,PPS,1\n")
cr.publish_chip_state(cr.Ds3234(FakeBus()), True)
_doc = _json.loads(Path(cr.RTC_STATUS_FILE).read_text())
check("the feed's document carries it beside the chip's state",
      _doc.get("pps_selected") is True and "state" in _doc, str(_doc))

# ---- 21. watch: a campod's half, with no chip -------------------------------------------
#
# NEVER THE BUS. A campod's /dev/spidev0.0 is the camera ADXL345, which the accel reader
# holds; a DS3234 read there would be register traffic on someone else's part.
_opened = []
_spidev_real = cr.SpidevBus


def _no_spi(*a, **kw):
    _opened.append(a)
    raise AssertionError("watch opened SPI")


cr.SpidevBus = _no_spi
_looks = iter([(100.0, 7), (101.0, 8), (101.0, 8), None])
cr.pps_assert = lambda path=None: next(_looks)
_slept.clear()
_docs = []
_write_real = cr.write_status


def _write(doc):
    _docs.append(doc)
    if len(_docs) == 3:
        raise _Stop


cr.write_status = _write
try:
    cr.main(["coord-rtc", "watch"])
except _Stop:
    pass
cr.write_status, cr.SpidevBus, cr.pps_assert = _write_real, _spidev_real, _pps_real
check("watch never opens SPI", _opened == [], str(_opened))
check("watch is not one of the chip's verbs",
      "watch" in cr.CHIPLESS and "watch" not in cr.COMMANDS)
check("a sequence number that moved between looks is edge: true, one that did not and a"
      " missing assert file are false",
      [d["edge"] for d in _docs] == [True, False, False], str(_docs))
check("each look carries chrony's selection and the boot",
      all(d["pps_selected"] is True and d["boot_id"] == cr.boot_id() for d in _docs), str(_docs))
check("no chip fields: a pod has no chip to report",
      all("state" not in d for d in _docs), str(_docs))
check("it looks every WATCH_PERIOD_S, over a second so a look always spans an edge",
      _slept == [cr.WATCH_PERIOD_S] * 3 and cr.WATCH_PERIOD_S > 1, str(_slept))
cr.subprocess.run = _run_real

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\ntest_coord_rtc: all checks passed")
