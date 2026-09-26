package main

import "sync/atomic"

// Batch is one FIFO drain: the samples, and everything needed to reason about
// what is missing around them. Batches are recycled through a pool, so the
// capture loop never allocates after startup.
type Batch struct {
	BootNS  int64 // CLOCK_BOOTTIME, stamped BEFORE the drain
	MonoNS  int64
	DrainNS int64 // how long the drain itself took
	GapNS   int64 // since this device's previous drain
	N       int
	Ovr     bool
	// Counters as of this drain, so a hole in the stream can be attributed by
	// differencing them across it rather than guessed at. Stamped in the capture
	// goroutine, which owns both, so the writer never reads them concurrently.
	Drops   int64 // batches drained and discarded because the pool was empty
	Errs    int64 // entries()/drain() failures, neither emitted nor counted as samples
	X, Y, Z []int16
}

// pool is a fixed set of preallocated batches cycling between the capture
// goroutine and the writer goroutine. free -> capture fills -> filled ->
// writer drains -> free.
//
// Depth is the only thing standing between a slow card and lost samples, and it
// is cheap: a batch is 32 samples x 3 axes x 2 bytes plus a header, so 256
// batches is well under a megabyte. Absorption is depth/drain-rate, and the
// drain rate is set by how fast the FIFO refills, not by a schedule: 260923
// measured 1463 drains/second on camera and 1261 on arm, so 256 batches buys
// 175 ms and 203 ms respectively. The SD stalls measured on that flight reach
// 361 ms, which is longer than that -- when the pool loses, `drops` on the next
// emitted batch is what says so.
type pool struct {
	free   chan *Batch
	filled chan *Batch
	// Atomic because a drop is read back out on the capture goroutine to stamp
	// the next batch, and again on the main goroutine at teardown.
	drops atomic.Int64
}

func newPool(depth int) *pool {
	p := &pool{free: make(chan *Batch, depth), filled: make(chan *Batch, depth)}
	for i := 0; i < depth; i++ {
		p.free <- &Batch{
			X: make([]int16, 0, fifoDepth),
			Y: make([]int16, 0, fifoDepth),
			Z: make([]int16, 0, fifoDepth),
		}
	}
	return p
}

// get never blocks. If the writer is behind, we drop this batch and say so,
// because blocking the capture loop would cost the hardware FIFO -- samples we
// can never recover -- to save samples we already have in hand. Dropping is the
// cheaper failure and the only one that is bounded.
func (p *pool) get() *Batch {
	select {
	case b := <-p.free:
		return b
	default:
		p.drops.Add(1)
		return nil
	}
}

func (p *pool) put(b *Batch) { p.filled <- b }

func (p *pool) recycle(b *Batch) {
	select {
	case p.free <- b:
	default: // cannot happen: free has the same depth as the number of batches
	}
}
