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
	"strings"
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

// nodeName is the host's name, not the container's. os.Hostname() in a container returns the
// container ID, which changes on every recreate, so captures would scatter across directories
// instead of collecting under one name. /etc/host-hostname is bind-mounted read-only by the
// stack file; os.Hostname is a last resort that only applies outside a container.
func nodeName() string {
	if b, err := os.ReadFile("/etc/host-hostname"); err == nil {
		if n := strings.TrimSpace(string(b)); n != "" {
			return n
		}
	}
	h, _ := os.Hostname()
	return h
}

func loadConfig() config {
	c := config{
		dir:       os.Getenv("CAMPOD_ACCEL_DIR"),
		node:      os.Getenv("CAMPOD_NODE_NAME"),
		session:   os.Getenv("CAMPOD_SESSION"),
		sepM:      os.Getenv("CAMPOD_ACCEL_SEPARATION_M"),
		odrHz:     envInt("CAMPOD_ACCEL_ODR_HZ", 3200),
		rangeG:    envInt("CAMPOD_ACCEL_RANGE_G", 16),
		spiHz:     uint32(envInt("CAMPOD_ACCEL_SPI_HZ", defaultSPIHz)),
		poolDepth: envInt("CAMPOD_ACCEL_POOL", 256),
	}
	if c.dir == "" {
		c.dir = "/captures"
	}
	if c.node == "" {
		c.node = nodeName()
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
			"pool_depth":   c.poolDepth,
			"reader":       "campod-accel (go)",
			"self_test":    stRes,
			"separation_m": c.sepM,
			"started_utc":  time.Now().UTC().Format("2006-01-02T15:04:05.000000Z"),
			"note": "Samples are raw LSB counts, not g. There is NO fixed poll rate " +
				"and no fill threshold: the reader alternates between devices and takes " +
				"whatever each has, so batch spacing and size vary by design. Each batch carries drain_ns (how long " +
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
	fmt.Printf("accel: logging %d device(s) odr=%d range=+/-%dg spi=%d pool=%d\n",
		len(lives), c.odrHz, c.rangeG, c.spiHz, c.poolDepth)

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

// capture alternates between devices until told to stop, draining whatever each
// one has rather than waiting for it to accumulate.
//
// The first version of this waited for a fill threshold of 16 entries, and that
// was the bug. Measured on campod-se: after draining both devices their FIFOs are
// near-empty, so neither met the threshold, so the loop slept the 5 ms it takes to
// reach 16 -- and then the two drains took 6.6 ms more. A 12 ms cycle against a
// FIFO that fills in 10 ms, so 16 samples arrived during the sleep and 21 more
// during the drains: 37 into a 32-deep FIFO, overflowing every single cycle. 99%
// of batches came back flagged and 14% of samples were lost.
//
// There was never anything to gain by waiting, either. Per-sample cost is fixed
// PER TRANSACTION, not per drain -- the part requires a complete read of
// DATAX0..DATAZ1 to pop one entry, so 32 entries is 32 transactions however they
// are packaged. Draining 4 costs about the same per sample as draining 32.
//
// So: take what is there, every time. The only sleep is one sample period when
// BOTH devices came back empty, which cannot delay work that exists and stops the
// loop reading FIFO_STATUS thousands of times a second to be told "nothing yet".
func capture(lives []*live, c config, stop <-chan os.Signal) {
	perSample := time.Duration(float64(time.Second) / float64(c.odrHz))

	for {
		select {
		case <-stop:
			fmt.Println("accel: stop requested")
			return
		default:
		}

		idle := true
		for _, lv := range lives {
			n, ovr, err := lv.s.entries()
			if err != nil {
				lv.st.readErrs.Add(1)
				continue
			}
			if n == 0 {
				continue
			}
			idle = false
			if ovr {
				lv.st.overruns.Add(1)
			}
			b := lv.pool.get()
			if b == nil {
				// Writer is behind. Drain anyway so the FIFO does not carry the
				// backlog into the next cycle, but discard rather than stall.
				lv.st.readErrs.Add(0)
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

		if idle {
			time.Sleep(perSample)
		}
	}
}
