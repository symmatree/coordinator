package main

import (
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
	if p.drops != 1 {
		t.Fatalf("drops = %d, want 1: a dropped batch must be counted, not silent", p.drops)
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

func TestPoolDepthCoversASecondOfStalls(t *testing.T) {
	// 256 batches at roughly 200 drains/second per device is over a second of
	// absorption, which is the timescale of the SD write stalls measured on
	// campod-se. If someone shrinks this, the test should make them think.
	const depth = 256
	p := newPool(depth)
	for i := 0; i < depth; i++ {
		if p.get() == nil {
			t.Fatalf("pool exhausted after %d of %d", i, depth)
		}
	}
	if p.drops != 0 {
		t.Fatalf("drops = %d before exhaustion", p.drops)
	}
}
