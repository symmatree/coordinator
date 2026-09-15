// campod-accel reads both ADXL345s into JSONL.
//
// Shape, and why:
//
//	capture goroutine        one per process, alternating between the two
//	                         devices, never touching the filesystem
//	pool                     preallocated batches, non-blocking handoff
//	writer goroutine         one per device, owns all blocking I/O
//
// The constraint the whole thing exists to meet: the ADXL345 FIFO is 32 samples,
// which at 3200 Hz is 10 ms of data. Miss that and the part overwrites -- the
// loss happens in silicon before any of our code runs, so no amount of buffering
// downstream can recover it. Everything here is in service of the capture loop
// never being the reason we are late.
//
// There is no fixed poll rate. The previous Python reader woke at 200 Hz and
// drained both devices, which meant a schedule it could fall behind, and it did:
// its median poll interval was 1.20 ms against a 5 ms target, because it was
// permanently in catch-up. Alternating removes the schedule. While one device is
// being drained the other accumulates, so batches arrive naturally fat, and the
// only bound is that a complete cycle finish before the other FIFO overflows --
// 10 ms rather than the 5 ms a fixed poll gave us. The same work, twice the room.
package main

import (
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"
)

const schema = 1

type config struct {
	dir       string
	node      string
	session   string
	odrHz     int
	rangeG    int
	spiHz     uint32
	threshold int
	poolDepth int
	sepM      string
}

func envInt(k string, def int) int {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
		fmt.Fprintf(os.Stderr, "accel: %s=%q is not an integer; using %d\n", k, os.Getenv(k), def)
	}
	return def
}

func loadConfig() config {
	c := config{
		dir:     os.Getenv("CAMPOD_ACCEL_DIR"),
		node:    os.Getenv("CAMPOD_NODE_NAME"),
		session: os.Getenv("CAMPOD_SESSION"),
		sepM:    os.Getenv("CAMPOD_ACCEL_SEPARATION_M"),
		odrHz:   envInt("CAMPOD_ACCEL_ODR_HZ", 3200),
		rangeG:  envInt("CAMPOD_ACCEL_RANGE_G", 16),
		spiHz:   uint32(envInt("CAMPOD_ACCEL_SPI_HZ", 1500000)),
		// Drain at half depth: fat enough that the per-drain cost is amortised
		// (measured: 105.5 us/sample at 10-17 entries against 178.7 at 1-2),
		// while leaving 16 slots -- 5 ms at 3200 Hz -- before overflow.
		threshold: envInt("CAMPOD_ACCEL_DRAIN_AT", fifoDepth/2),
		poolDepth: envInt("CAMPOD_ACCEL_POOL", 256),
	}
	if c.dir == "" {
		c.dir = "/captures"
	}
	if c.node == "" {
		h, _ := os.Hostname()
		c.node = h
	}
	if c.session == "" {
		c.session = time.Now().UTC().Format("20060102T150405Z")
	}
	return c
}

var devices = []struct{ label, path string }{
	// Chip select IS the identity, by wiring convention: CE0 is bonded behind
	// the camera, CE1 is at the arm end toward the motor.
	{"camera", "/dev/spidev0.0"},
	{"arm", "/dev/spidev0.1"},
}

const (
	clockMonotonic = 1
	clockBoottime  = 7
)

func clockGettime(id uintptr) int64 {
	var ts syscall.Timespec
	_, _, errno := syscall.Syscall(syscall.SYS_CLOCK_GETTIME, id, uintptr(unsafe.Pointer(&ts)), 0)
	if errno != 0 {
		return time.Now().UnixNano()
	}
	return ts.Sec*1e9 + int64(ts.Nsec)
}

// bootNS is CLOCK_BOOTTIME, the clock the camera's SensorTimestamp is on, which
// is what lets a frame and a vibration record be placed on the same timeline.
func bootNS() int64 { return clockGettime(clockBoottime) }
func monoNS() int64 { return clockGettime(clockMonotonic) }

type stats struct {
	batches, samples, overruns, drops, readErrs atomic.Int64
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "accel: %v\n", err)
		os.Exit(1)
	}
}

type live struct {
	s      *sensor
	pool   *pool
	st     *stats
	lastNS int64
	count  int64
}

func run() error {
	c := loadConfig()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)

	sessionDir := filepath.Join(c.dir, c.node, c.session)
	if err := os.MkdirAll(sessionDir, 0o755); err != nil {
		return err
	}
	fmt.Printf("accel: session %s -> %s\n", c.session, sessionDir)

	var lives []*live
	var writers []*writer
	var wg sync.WaitGroup

	for _, d := range devices {
		bus, err := openSPI(d.path, 3, c.spiHz) // mode 3
		if err != nil {
			fmt.Printf("accel: %s (%s): %v; skipping\n", d.label, d.path, err)
			continue
		}
		s := newSensor(d.label, d.path, bus, c.odrHz, c.rangeG)
		if err := s.probe(); err != nil {
			fmt.Printf("accel: %v; skipping\n", err)
			bus.Close()
			continue
		}
		if err := s.configure(); err != nil {
			return fmt.Errorf("%s: configure: %w", d.label, err)
		}
		stRes, err := s.selfTest()
		if err != nil {
			return fmt.Errorf("%s: self-test: %w", d.label, err)
		}
		fmt.Printf("accel: %s: DEVID ok, self-test %s (x=%+.2fg y=%+.2fg z=%+.2fg)\n",
			d.label, map[bool]string{true: "PASS", false: "FAIL"}[stRes.AllPass],
			stRes.DeltaG["x"], stRes.DeltaG["y"], stRes.DeltaG["z"])

		hdr := map[string]any{
			"schema": schema, "type": "header", "node": c.node, "session": c.session,
			"label": d.label, "device": d.path, "devid": devIDExpected,
			"odr_hz_nominal": c.odrHz, "range_g": c.rangeG, "full_res": true,
			"scale_mg_per_lsb": scaleMgPerLSB, "spi_hz": c.spiHz,
			"drain_at": c.threshold, "pool_depth": c.poolDepth,
			"reader":       "campod-accel (go)",
			"self_test":    stRes,
			"separation_m": c.sepM,
			"started_utc":  time.Now().UTC().Format("2006-01-02T15:04:05.000000Z"),
			"note": "Samples are raw LSB counts, not g. There is NO fixed poll rate: " +
				"the reader alternates between devices and drains at a fill threshold, " +
				"so batch spacing varies by design. Each batch carries drain_ns (how long " +
				"the read took) and gap_ns (since this device's previous drain), which is " +
				"what bounds how much the FIFO could have discarded -- a batch with n < 32 " +
				"and ovr false lost nothing, because the FIFO never filled.",
		}
		w, err := newWriter(filepath.Join(sessionDir, "accel-"+d.label+".jsonl"), hdr, 64*1024)
		if err != nil {
			return err
		}
		writers = append(writers, w)

		p := newPool(c.poolDepth)
		st := &stats{}
		lv := &live{s: s, pool: p, st: st}
		lives = append(lives, lv)

		wg.Add(1)
		go func(p *pool, w *writer, st *stats) {
			defer wg.Done()
			for b := range p.filled {
				rec := map[string]any{
					"t": "b", "i": st.samples.Load(), "boot_ns": b.BootNS, "mono_ns": b.MonoNS,
					"drain_ns": b.DrainNS, "gap_ns": b.GapNS,
					"n": b.N, "ovr": b.Ovr, "x": b.X, "y": b.Y, "z": b.Z,
				}
				st.samples.Add(int64(b.N))
				st.batches.Add(1)
				if err := w.record(rec); err != nil {
					fmt.Fprintf(os.Stderr, "accel: write: %v\n", err)
				}
				p.recycle(b)
			}
		}(p, w, st)
	}

	if len(lives) == 0 {
		return fmt.Errorf("no ADXL345 answered on either chip select; " +
			"check dtparam=spi=on and the wiring")
	}
	fmt.Printf("accel: logging %d device(s) odr=%d range=+/-%dg spi=%d drain_at=%d pool=%d\n",
		len(lives), c.odrHz, c.rangeG, c.spiHz, c.threshold, c.poolDepth)

	capture(lives, c, stop)

	for _, lv := range lives {
		close(lv.pool.filled)
	}
	wg.Wait()
	for _, w := range writers {
		if err := w.Close(); err != nil {
			fmt.Fprintf(os.Stderr, "accel: close: %v\n", err)
		}
	}
	for i, lv := range lives {
		fmt.Printf("accel: %s: %d samples in %d batches, %d overruns, %d dropped batches, %d read errors\n",
			devices[i].label, lv.st.samples.Load(), lv.st.batches.Load(),
			lv.st.overruns.Load(), lv.pool.drops, lv.st.readErrs.Load())
		lv.s.bus.Close()
	}
	return nil
}

// capture alternates between devices until told to stop.
//
// It never blocks on anything but the clock: FIFO status, a batched drain, a
// non-blocking handoff to the pool, and a sleep sized from how far each device
// still is from the drain threshold. There is deliberately no busy-wait -- a
// status read is its own ioctl at ~80-100 us, so spinning on it would burn a
// core to learn nothing.
func capture(lives []*live, c config, stop <-chan os.Signal) {
	perSample := time.Duration(float64(time.Second) / float64(c.odrHz))
	maxSleep := time.Duration(float64(c.threshold) * float64(perSample))

	for {
		select {
		case <-stop:
			fmt.Println("accel: stop requested")
			return
		default:
		}

		shortest := maxSleep
		worked := false

		for _, lv := range lives {
			n, ovr, err := lv.s.entries()
			if err != nil {
				lv.st.readErrs.Add(1)
				continue
			}
			if n < c.threshold {
				// How long until this device reaches the threshold?
				if wait := time.Duration(c.threshold-n) * perSample; wait < shortest {
					shortest = wait
				}
				continue
			}
			worked = true
			if ovr {
				lv.st.overruns.Add(1)
			}
			b := lv.pool.get()
			if b == nil {
				// Writer is behind. Drain anyway -- leaving entries in the FIFO
				// would push the overflow into the next cycle -- but throw the
				// samples away rather than stall the loop.
				_ = lv.s.drain(n, &Batch{X: make([]int16, 0, fifoDepth),
					Y: make([]int16, 0, fifoDepth), Z: make([]int16, 0, fifoDepth)})
				continue
			}
			t0 := bootNS()
			b.BootNS = t0
			b.MonoNS = monoNS()
			b.Ovr = ovr
			if lv.lastNS != 0 {
				b.GapNS = t0 - lv.lastNS
			}
			lv.lastNS = t0
			if err := lv.s.drain(n, b); err != nil {
				lv.st.readErrs.Add(1)
				lv.pool.recycle(b)
				continue
			}
			b.DrainNS = bootNS() - t0
			lv.pool.put(b)
		}

		if !worked && shortest > 0 {
			time.Sleep(shortest)
		}
	}
}
