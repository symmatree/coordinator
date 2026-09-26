package main

import (
	"os"
	"sync"
	"testing"
	"time"
)

// The capture loop must never block on the writer. If it did, a slow SD card
// would cost us the hardware FIFO -- samples that are gone before our code runs
// and can never be recovered -- in order to protect samples we already hold.
// Dropping is the cheaper failure, and the only one that is bounded.
func TestPoolNeverBlocksWhenTheWriterIsBehind(t *testing.T) {
	p := newPool(4)

	var held []*Batch
	for i := 0; i < 4; i++ {
		b := p.get()
		if b == nil {
			t.Fatalf("get %d returned nil while the pool still had batches", i)
		}
		held = append(held, b)
	}

	done := make(chan *Batch, 1)
	go func() { done <- p.get() }()
	select {
	case b := <-done:
		if b != nil {
			t.Fatal("get returned a batch from an exhausted pool")
		}
	case <-time.After(time.Second):
		t.Fatal("get BLOCKED on an exhausted pool -- this is the failure mode the " +
			"whole design exists to prevent")
	}
	if got := p.drops.Load(); got != 1 {
		t.Fatalf("drops = %d, want 1: a dropped batch must be counted, not silent", got)
	}

	p.recycle(held[0])
	if b := p.get(); b == nil {
		t.Fatal("get returned nil after a batch was recycled")
	}
}

func TestPoolRoundTripReusesBatchesWithoutAllocating(t *testing.T) {
	p := newPool(2)
	first := p.get()
	second := p.get() // take both, so the free channel is empty and FIFO order
	_ = second        // cannot hand back a different preallocated batch
	if cap(first.X) != fifoDepth {
		t.Fatalf("batch X capacity %d, want %d preallocated so the hot path never grows it",
			cap(first.X), fifoDepth)
	}
	first.X = append(first.X, 1, 2, 3)
	p.put(first)
	got := <-p.filled
	if got != first {
		t.Fatal("filled channel returned a different batch than was put in")
	}
	p.recycle(got)
	again := p.get()
	if again != first {
		t.Fatal("recycled batch was not handed back out -- the pool is allocating")
	}
}

func TestPoolAbsorbsItsFullDepthBeforeDropping(t *testing.T) {
	// Absorption is depth divided by the drain rate, and the drain rate is set by
	// how fast the FIFO refills rather than by a schedule. 260923 measured 1463
	// drains/second on camera and 1261 on arm, so 256 batches is 175 ms and 203 ms
	// -- not the "over a second" this was originally written against, and shorter
	// than the 361 ms stall that flight actually hit. If someone shrinks this, the
	// test should make them think; if someone grows it, `drops` is the evidence.
	const depth = 256
	p := newPool(depth)
	for i := 0; i < depth; i++ {
		if p.get() == nil {
			t.Fatalf("pool exhausted after %d of %d", i, depth)
		}
	}
	if got := p.drops.Load(); got != 0 {
		t.Fatalf("drops = %d before exhaustion", got)
	}
}

// A hole in the stream has three possible causes and the file has to say which.
// Before this, a pool drop drained the FIFO into a throwaway buffer and left no
// trace at all: no record, no lastNS update, and the overrun latch consumed by
// the discard read. The only trace was a large gap_ns on the next batch that got
// through -- indistinguishable from the device having had nothing to give, which
// is how 260923's two camera-only holes ended up unattributable after the fact.
func TestDroppedBatchesReachTheStreamAsACounter(t *testing.T) {
	f := &refillingSPI{inner: newFake()}
	s := newSensor("camera", "/dev/null", f, 3200, 16)
	if err := s.configure(); err != nil {
		t.Fatal(err)
	}

	const depth = 2
	p := newPool(depth)
	held := make([]*Batch, 0, depth)
	for i := 0; i < depth; i++ {
		held = append(held, p.get())
	}

	stop := make(chan os.Signal, 1)
	lives := []*live{{s: s, pool: p, st: &stats{}}}
	returned := make(chan struct{})
	go func() { capture(lives, config{odrHz: 3200}, stop); close(returned) }()

	// With every batch held, the capture loop can only drain-and-discard.
	deadline := time.After(2 * time.Second)
	for p.drops.Load() == 0 {
		select {
		case <-deadline:
			t.Fatal("capture loop recorded no drops while the pool was exhausted")
		default:
			time.Sleep(time.Millisecond)
		}
	}

	// Hand one back; the next batch through must carry the drops that happened
	// while it was unavailable.
	p.recycle(held[0])
	select {
	case b := <-p.filled:
		if b.Drops == 0 {
			t.Fatal("batch emitted after a pool exhaustion carries drops = 0, so the " +
				"hole before it is indistinguishable from a quiet device")
		}
		if b.Errs != 0 {
			t.Fatalf("errs = %d with a fake that never fails", b.Errs)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no batch emitted after the pool was replenished")
	}

	stop <- os.Interrupt
	<-returned
}

// refillingSPI is a fakeSPI whose FIFO never runs dry, so the capture loop keeps
// finding work the way a live part at 3.2 kHz does. The mutex is not decoration:
// capture() runs on its own goroutine and `go test -race` is the point.
type refillingSPI struct {
	mu    sync.Mutex
	inner *fakeSPI
}

func (r *refillingSPI) top() {
	for len(r.inner.fifo) < fifoDepth {
		r.inner.fifo = append(r.inner.fifo, [3]int16{1, 2, 3})
	}
}

func (r *refillingSPI) WriteReg(reg, val byte) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.inner.WriteReg(reg, val)
}

func (r *refillingSPI) ReadRegs(reg byte, n int) ([]byte, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.top()
	return r.inner.ReadRegs(reg, n)
}

func (r *refillingSPI) DrainFIFO(count int, dst []byte) ([]byte, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.inner.DrainFIFO(count, dst)
}

func (r *refillingSPI) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.inner.Close()
}
