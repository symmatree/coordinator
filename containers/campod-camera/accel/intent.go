package main

// Whether to write to disk, read from the same file the camera gates on.
//
// WHY READ AND COUNT ANYWAY. The FIFO is 32 deep and fills in ~10 ms. Stopping the drain
// would back it up within a tick, so the first samples after being turned on would be
// stale by an unknown amount and the overrun bit would be set for reasons that have
// nothing to do with the capture. Draining costs one SPI transfer per ~2 samples and no
// card writes at all; the expensive part is the JSONL, which is the part this gates.
//
// Keeping the counters live has a second use: `samples` and `batches` continue to
// advance, so the status document says the sensors are alive and at what rate while a pod
// sits disarmed. A pod that looks idle and a pod whose accelerometer has died should not
// read the same.
//
// THE SAME FILE, not a second mechanism. capture.py reads /tmp/campod_capture and so does
// this; pod-link writes it from the coordinator's arm state. One intent, two readers, so
// a pod cannot end up half-collecting -- images without accel or the reverse -- which is
// the failure that would be worst to discover in the data afterwards.

import (
	"os"
	"strings"
	"sync/atomic"
	"time"
)

// How often to re-read the flag. The camera reads it per tick at 1 Hz; this is a tmpfs
// read, so the cost is a syscall rather than I/O, but the capture loop runs at ~1500
// drains/s and a read per drain would be 1500 syscalls/s for an answer that changes when
// somebody arms. Once a second matches the camera and the clock-pair cadence.
const intentPoll = time.Second

// intent caches the flag so the writer goroutines share one read per second rather than
// one each. Atomic because every writer goroutine reads it and the poller writes it.
type intent struct {
	path    string
	writing atomic.Bool
}

// newIntent reads once before returning, so the first batch is already gated correctly
// rather than being written because a goroutine had not caught up.
//
// An empty path means "no gating" -- write continuously. That is what every deployment
// did before this existed, and it is what a bench run with no coordinator wants.
func newIntent(path string) *intent {
	in := &intent{path: path}
	in.writing.Store(in.read())
	return in
}

// read answers the question the file is asked: should we be writing right now?
//
// ABSENT MEANS NO. A gate whose failure mode is "write everything forever" is the
// behaviour being removed -- an unattended pod filled a 29 GB card to 99% overnight
// recording a bench nobody was looking at. Same rule and same vocabulary as
// capture.py's _capture_wanted, deliberately: two readers disagreeing about what "1"
// means would be worse than either default.
func (in *intent) read() bool {
	if in.path == "" {
		return true
	}
	b, err := os.ReadFile(in.path)
	if err != nil {
		return false
	}
	switch strings.TrimSpace(string(b)) {
	case "1", "true", "on", "yes":
		return true
	}
	return false
}

// poll re-reads until stop closes. Runs on its own goroutine so neither the capture loop
// nor a writer ever blocks on the filesystem to find out.
func (in *intent) poll(stop <-chan struct{}) {
	t := time.NewTicker(intentPoll)
	defer t.Stop()
	for {
		select {
		case <-stop:
			return
		case <-t.C:
			in.writing.Store(in.read())
		}
	}
}
