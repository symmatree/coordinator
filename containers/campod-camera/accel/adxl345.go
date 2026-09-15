package main

import (
	"fmt"
	"math"
	"time"
)

// Registers and codes are the ADXL345 datasheet's, Rev. G.
const (
	regDevID      = 0x00
	regBWRate     = 0x2C
	regPowerCtl   = 0x2D
	regIntEnable  = 0x2E
	regIntSource  = 0x30
	regDataFormat = 0x31
	regDataX0     = 0x32
	regFIFOCtl    = 0x38
	regFIFOStatus = 0x39

	devIDExpected = 0xE5

	powerCtlMeasure    = 0x08
	dataFormatFullRes  = 0x08
	dataFormatSelfTest = 0x80
	fifoCtlStream      = 0x80
	intSourceOverrun   = 0x01

	fifoDepth    = 32
	bytesPerRead = 7 // 1 address + 6 data
	popDelayUsec = 5 // AN-1025: >= 5 us between a data read and the next FIFO access

	// 3 MHz, not the 1.5 MHz the Python reader used. That 1.5 was chosen to stay
	// under the 1.6 MHz above which AN-1025 requires deasserting CS to guarantee
	// the 5 us pop delay -- and it was the real bottleneck. Measured on campod-se,
	// two devices at ODR 3200 over 30 s:
	//
	//   1.5 MHz   2698 samples/s (84% of nominal)   2516 overruns of 2530 batches
	//   3   MHz   3246 samples/s (100%)                 3 overruns
	//   6   MHz   3249 samples/s (100%)                 2 overruns
	//
	// 6 MHz buys nothing over 3, so 3 is where we stop being SPI-bound. We can
	// raise it because DrainFIFO sets cs_change and delay_usecs per transfer, which
	// satisfies the delay explicitly instead of relying on the address byte's
	// incidental duration. Per-sample cost at 3 MHz decomposes as 18.7 us of clock
	// plus 29.6 us fixed per transaction.
	defaultSPIHz = 3000000

	// In FULL_RES the scale is a constant 3.9 mg/LSB at every range -- the bit
	// depth grows instead -- so the widest range costs nothing in resolution.
	scaleMgPerLSB = 3.9
)

var odrCodes = map[int]byte{
	3200: 0x0F, 1600: 0x0E, 800: 0x0D, 400: 0x0C,
	200: 0x0B, 100: 0x0A, 50: 0x09, 25: 0x08,
}

var rangeCodes = map[int]byte{2: 0x00, 4: 0x01, 8: 0x02, 16: 0x03}

// Datasheet Table 1: output change with SELF_TEST set, in g, valid across the
// whole 2.0-3.6 V supply range. Requires ODR >= 100 Hz.
var selfTestLimits = map[string][2]float64{
	"x": {0.20, 2.10}, "y": {-2.10, -0.20}, "z": {0.30, 3.40},
}

type sensor struct {
	label   string
	path    string
	bus     spi
	odrHz   int
	rangeG  int
	scratch []byte
}

func newSensor(label, path string, bus spi, odrHz, rangeG int) *sensor {
	return &sensor{
		label: label, path: path, bus: bus, odrHz: odrHz, rangeG: rangeG,
		scratch: make([]byte, fifoDepth*(bytesPerRead-1)),
	}
}

// probe reads DEVID. SPI has no enumeration -- the spidev nodes exist whether or
// not anything is wired to them -- so this is the only way to know a sensor is
// there, and the reader has to do it regardless.
func (s *sensor) probe() error {
	b, err := s.bus.ReadRegs(regDevID, 1)
	if err != nil {
		return err
	}
	if b[0] != devIDExpected {
		return fmt.Errorf("%s (%s): DEVID 0x%02X, expected 0x%02X", s.label, s.path, b[0], devIDExpected)
	}
	return nil
}

func (s *sensor) configure() error {
	code, ok := odrCodes[s.odrHz]
	if !ok {
		return fmt.Errorf("no ADXL345 code for %d Hz; the part offers halvings of 3200 only", s.odrHz)
	}
	rc, ok := rangeCodes[s.rangeG]
	if !ok {
		return fmt.Errorf("no ADXL345 code for +/-%d g", s.rangeG)
	}
	steps := []struct{ reg, val byte }{
		{regPowerCtl, 0x00}, // standby while configuring
		{regBWRate, code},
		{regDataFormat, dataFormatFullRes | rc},
		{regIntEnable, 0x00}, // no interrupts; there is no INT wire
		{regFIFOCtl, fifoCtlStream | (fifoDepth - 1)},
		{regPowerCtl, powerCtlMeasure},
	}
	for _, st := range steps {
		if err := s.bus.WriteReg(st.reg, st.val); err != nil {
			return err
		}
	}
	return nil
}

// entries reports how many samples the FIFO holds, and whether the overrun bit
// is set. INT_SOURCE is only read when the FIFO came back full, because reading
// it clears the latched bit and we only care while we are behind.
func (s *sensor) entries() (int, bool, error) {
	b, err := s.bus.ReadRegs(regFIFOStatus, 1)
	if err != nil {
		return 0, false, err
	}
	n := int(b[0] & 0x3F)
	if n < fifoDepth {
		return n, false, nil
	}
	src, err := s.bus.ReadRegs(regIntSource, 1)
	if err != nil {
		return n, false, err
	}
	return n, src[0]&intSourceOverrun != 0, nil
}

// drain reads n entries in one ioctl and decodes them into the batch.
func (s *sensor) drain(n int, b *Batch) error {
	raw, err := s.bus.DrainFIFO(n, s.scratch)
	if err != nil {
		return err
	}
	got := len(raw) / (bytesPerRead - 1)
	b.X = b.X[:0]
	b.Y = b.Y[:0]
	b.Z = b.Z[:0]
	for i := 0; i < got; i++ {
		o := i * (bytesPerRead - 1)
		b.X = append(b.X, int16(uint16(raw[o])|uint16(raw[o+1])<<8))
		b.Y = append(b.Y, int16(uint16(raw[o+2])|uint16(raw[o+3])<<8))
		b.Z = append(b.Z, int16(uint16(raw[o+4])|uint16(raw[o+5])<<8))
	}
	b.N = got
	return nil
}

type selfTestResult struct {
	DeltaG  map[string]float64 `json:"delta_g"`
	Pass    map[string]bool    `json:"pass"`
	AllPass bool               `json:"all_pass"`
}

// selfTest applies the datasheet's electrostatic self-test and checks the output
// change against Table 1. Runs in FIFO bypass so it reads the data registers
// directly, and restores stream mode afterwards.
func (s *sensor) selfTest() (*selfTestResult, error) {
	settle := time.Duration(float64(time.Second) * 8 / float64(s.odrHz))
	avg := func() ([3]float64, error) {
		var acc [3]float64
		const n = 16
		for i := 0; i < n; i++ {
			d, err := s.bus.ReadRegs(regDataX0, 6)
			if err != nil {
				return acc, err
			}
			acc[0] += float64(int16(uint16(d[0]) | uint16(d[1])<<8))
			acc[1] += float64(int16(uint16(d[2]) | uint16(d[3])<<8))
			acc[2] += float64(int16(uint16(d[4]) | uint16(d[5])<<8))
			time.Sleep(time.Duration(float64(time.Second) / float64(s.odrHz)))
		}
		for i := range acc {
			acc[i] /= n
		}
		return acc, nil
	}

	if err := s.bus.WriteReg(regFIFOCtl, 0x00); err != nil { // bypass
		return nil, err
	}
	fmtReg := dataFormatFullRes | rangeCodes[s.rangeG]
	time.Sleep(settle)
	off, err := avg()
	if err != nil {
		return nil, err
	}
	if err := s.bus.WriteReg(regDataFormat, fmtReg|dataFormatSelfTest); err != nil {
		return nil, err
	}
	time.Sleep(settle)
	on, err := avg()
	if err != nil {
		return nil, err
	}
	if err := s.bus.WriteReg(regDataFormat, fmtReg); err != nil {
		return nil, err
	}
	if err := s.bus.WriteReg(regFIFOCtl, fifoCtlStream|(fifoDepth-1)); err != nil {
		return nil, err
	}

	res := &selfTestResult{DeltaG: map[string]float64{}, Pass: map[string]bool{}, AllPass: true}
	for i, ax := range []string{"x", "y", "z"} {
		d := (on[i] - off[i]) * scaleMgPerLSB / 1000.0
		lim := selfTestLimits[ax]
		ok := d >= lim[0] && d <= lim[1]
		res.DeltaG[ax] = math.Round(d*1000) / 1000
		res.Pass[ax] = ok
		if !ok {
			res.AllPass = false
		}
	}
	return res, nil
}
