package main

import (
	"testing"
)

// fakeSPI implements enough of the ADXL345 to exercise everything that would
// otherwise only be found out on the airframe. Same intent as the Python
// reader's fake spidev, but it also COUNTS IOCTLS, because collapsing a drain
// into one ioctl is the central claim of this package.
type fakeSPI struct {
	regs     [64]byte
	fifo     [][3]int16
	drains   int // DrainFIFO calls
	regReads int
	selfTest bool
	closed   bool
}

func newFake() *fakeSPI {
	f := &fakeSPI{}
	f.regs[regDevID] = devIDExpected
	return f
}

func (f *fakeSPI) WriteReg(reg, val byte) error {
	f.regs[reg] = val
	if reg == regDataFormat {
		f.selfTest = val&dataFormatSelfTest != 0
	}
	return nil
}

func (f *fakeSPI) ReadRegs(reg byte, n int) ([]byte, error) {
	f.regReads++
	switch reg {
	case regFIFOStatus:
		q := len(f.fifo)
		if q > fifoDepth {
			q = fifoDepth
		}
		return []byte{byte(q)}, nil
	case regIntSource:
		if len(f.fifo) >= fifoDepth {
			return []byte{intSourceOverrun}, nil
		}
		return []byte{0}, nil
	case regDataX0:
		// Bypass-mode direct read, used by self-test. The datasheet's Table 1
		// limits are SIGNED and Y is negative (-2.10 to -0.20 g), which is how a
		// real part behaves -- campod-se measures y=-1.17 g. A fake that offset
		// all three axes the same way would "pass" a check that must reject it.
		x, y, z := int16(100), int16(100), int16(100)
		if f.selfTest {
			x += 256 // ~ +1.0 g at 3.9 mg/LSB
			y -= 256 // ~ -1.0 g
			z += 384 // ~ +1.5 g
		}
		return []byte{
			byte(x), byte(x >> 8),
			byte(y), byte(y >> 8),
			byte(z), byte(z >> 8),
		}, nil
	}
	out := make([]byte, n)
	for i := 0; i < n; i++ {
		out[i] = f.regs[int(reg)+i]
	}
	return out, nil
}

func (f *fakeSPI) DrainFIFO(count int, dst []byte) ([]byte, error) {
	f.drains++
	if count > len(f.fifo) {
		count = len(f.fifo)
	}
	out := dst[:0]
	for i := 0; i < count; i++ {
		s := f.fifo[i]
		out = append(out,
			byte(s[0]), byte(s[0]>>8),
			byte(s[1]), byte(s[1]>>8),
			byte(s[2]), byte(s[2]>>8))
	}
	f.fifo = f.fifo[count:]
	return out, nil
}

func (f *fakeSPI) Close() error { f.closed = true; return nil }

func TestProbeGatesOnDevID(t *testing.T) {
	f := newFake()
	s := newSensor("camera", "/dev/null", f, 3200, 16)
	if err := s.probe(); err != nil {
		t.Fatalf("probe should succeed with DEVID 0xE5: %v", err)
	}
	f.regs[regDevID] = 0x00 // a miswired chip select reads as zero
	if err := s.probe(); err == nil {
		t.Fatal("probe must fail when DEVID is wrong -- that is the only way to " +
			"tell a missing sensor from a present one, since SPI has no enumeration")
	}
}

func TestConfigureWritesTheDatasheetCodes(t *testing.T) {
	f := newFake()
	s := newSensor("camera", "/dev/null", f, 3200, 16)
	if err := s.configure(); err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct {
		name string
		reg  byte
		want byte
	}{
		{"BW_RATE 3200 Hz", regBWRate, 0x0F},
		{"DATA_FORMAT FULL_RES|+/-16g", regDataFormat, dataFormatFullRes | 0x03},
		{"FIFO_CTL stream, watermark 31", regFIFOCtl, fifoCtlStream | 31},
		{"POWER_CTL measure", regPowerCtl, powerCtlMeasure},
		{"INT_ENABLE none (no INT wire)", regIntEnable, 0x00},
	} {
		if got := f.regs[c.reg]; got != c.want {
			t.Errorf("%s: reg 0x%02X = 0x%02X, want 0x%02X", c.name, c.reg, got, c.want)
		}
	}
}

func TestConfigureRejectsAnUnsupportedRate(t *testing.T) {
	s := newSensor("camera", "/dev/null", newFake(), 2500, 16)
	if err := s.configure(); err == nil {
		t.Fatal("2500 Hz is not on the ADXL345's menu -- it offers halvings of 3200 " +
			"only -- and configure must say so rather than silently pick something else")
	}
}

func TestDrainIsOneIoctlRegardlessOfBatchSize(t *testing.T) {
	f := newFake()
	s := newSensor("camera", "/dev/null", f, 3200, 16)
	for i := 0; i < 16; i++ {
		f.fifo = append(f.fifo, [3]int16{int16(i), int16(-i), 1000})
	}
	b := &Batch{X: make([]int16, 0, fifoDepth), Y: make([]int16, 0, fifoDepth), Z: make([]int16, 0, fifoDepth)}
	if err := s.drain(16, b); err != nil {
		t.Fatal(err)
	}
	if b.N != 16 {
		t.Fatalf("decoded %d samples, want 16", b.N)
	}
	if f.drains != 1 {
		t.Fatalf("drain issued %d ioctls for 16 entries, want exactly 1 -- "+
			"one-ioctl-per-drain is the whole reason this is not the Python reader", f.drains)
	}
	if b.X[3] != 3 || b.Y[3] != -3 || b.Z[3] != 1000 {
		t.Fatalf("decode wrong at index 3: %d,%d,%d", b.X[3], b.Y[3], b.Z[3])
	}
}

func TestDecodeAcrossTheSignBoundary(t *testing.T) {
	f := newFake()
	s := newSensor("camera", "/dev/null", f, 3200, 16)
	want := [][3]int16{{0, 1, -1}, {4095, -4096, 2048}, {-1, 0, 1}}
	f.fifo = append(f.fifo, want...)
	b := &Batch{X: make([]int16, 0, fifoDepth), Y: make([]int16, 0, fifoDepth), Z: make([]int16, 0, fifoDepth)}
	if err := s.drain(3, b); err != nil {
		t.Fatal(err)
	}
	for i, w := range want {
		if b.X[i] != w[0] || b.Y[i] != w[1] || b.Z[i] != w[2] {
			t.Errorf("sample %d: got %d,%d,%d want %v", i, b.X[i], b.Y[i], b.Z[i], w)
		}
	}
}

func TestOverrunIsReportedWhenTheFIFOCameBackFull(t *testing.T) {
	f := newFake()
	s := newSensor("camera", "/dev/null", f, 3200, 16)
	for i := 0; i < 10; i++ {
		f.fifo = append(f.fifo, [3]int16{1, 2, 3})
	}
	if n, ovr, _ := s.entries(); n != 10 || ovr {
		t.Fatalf("short FIFO: got n=%d ovr=%v, want 10/false", n, ovr)
	}
	f.fifo = nil
	for i := 0; i < fifoDepth; i++ {
		f.fifo = append(f.fifo, [3]int16{1, 2, 3})
	}
	if n, ovr, _ := s.entries(); n != fifoDepth || !ovr {
		t.Fatalf("full FIFO: got n=%d ovr=%v, want 32/true", n, ovr)
	}
}

func TestSelfTestVerdict(t *testing.T) {
	f := newFake()
	s := newSensor("camera", "/dev/null", f, 3200, 16)
	if err := s.configure(); err != nil {
		t.Fatal(err)
	}
	r, err := s.selfTest()
	if err != nil {
		t.Fatal(err)
	}
	if !r.AllPass {
		t.Fatalf("expected PASS: x and z positive, y negative, per Table 1: %+v", r.DeltaG)
	}
	if r.DeltaG["y"] >= 0 {
		t.Errorf("y delta must be negative to pass Table 1, got %v", r.DeltaG["y"])
	}
	if f.regs[regFIFOCtl] != fifoCtlStream|31 {
		t.Error("self-test must leave the FIFO back in stream mode")
	}
	if f.regs[regDataFormat]&dataFormatSelfTest != 0 {
		t.Error("self-test must clear the SELF_TEST bit afterwards")
	}
}
