package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// countRecords returns how many "b" and "clk" records a file holds, skipping the header.
func countRecords(t *testing.T, path string) (batches, clks int) {
	t.Helper()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	for i, ln := range strings.Split(strings.TrimSpace(string(body)), "\n") {
		if i == 0 || ln == "" {
			continue // the header record
		}
		var r map[string]any
		if err := json.Unmarshal([]byte(ln), &r); err != nil {
			t.Fatalf("line %d is not JSON: %v", i, err)
		}
		switch r["t"] {
		case "b":
			batches++
		case "clk":
			clks++
		}
	}
	return
}

// waitBatches blocks until the writer has counted `want` batches.
//
// NEEDED because p.put() is a buffered, non-blocking handoff: feeding and then flipping
// the flag immediately is a race, and it is the race my first version of this test lost --
// all ten batches were written because the writer had not read any of them before the flag
// changed. Waiting on the counter is the synchronisation point the writer itself provides.
func waitBatches(t *testing.T, st *stats, want int64) {
	t.Helper()
	for i := 0; i < 2000; i++ {
		if st.batches.Load() >= want {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("writer counted %d batches, waited for %d: either it is not consuming, or "+
		"the counting is gated with the write when it must not be", st.batches.Load(), want)
}

// feed pushes n batches through the writer loop.
func feed(t *testing.T, p *pool, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		b := p.get()
		if b == nil {
			t.Fatalf("pool exhausted after %d batches; the test's pool depth is too small, "+
				"which is a fault in the test rather than in the gate", i)
		}
		b.N = 3
		b.X = append(b.X[:0], 1, 2, 3)
		b.Y = append(b.Y[:0], 4, 5, 6)
		b.Z = append(b.Z[:0], 7, 8, 9)
		p.put(b)
	}
}

// THE TEST THE GATE SHIPPED WITHOUT. Ungating the write -- replacing the intent check
// with `if true` -- passed every other test in this package, which means nothing
// asserted the one behaviour the change exists for.
func TestTheGateActuallyGatesTheWrite(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "accel-test.jsonl")
	w, err := newWriter(path, map[string]any{"t": "hdr"}, 0)
	if err != nil {
		t.Fatal(err)
	}
	p := newPool(64)
	st := &stats{}
	in := &intent{} // path "" would mean write-always, so set the bool directly
	in.writing.Store(false)

	done := make(chan struct{})
	go func() { writeLoop(p, w, st, in); close(done) }()

	feed(t, p, 5)
	waitBatches(t, st, 5) // the writer has seen all five WHILE PAUSED
	in.writing.Store(true)
	feed(t, p, 5)
	waitBatches(t, st, 10)

	close(p.filled)
	<-done
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}

	batches, _ := countRecords(t, path)
	if batches != 5 {
		t.Fatalf("file holds %d batch records, want 5: the five fed while writing=false "+
			"must not reach the card, and the five fed after must", batches)
	}

	// AND THE COUNTERS RAN THROUGHOUT. "Keep reading the buffers and counting but don't
	// write anything to disk until turned on" -- so all ten are counted even though
	// five were not written. A paused pod must look alive, not dead.
	if got := st.batches.Load(); got != 10 {
		t.Fatalf("st.batches = %d, want 10: counting must not be gated with the write", got)
	}
	if got := st.samples.Load(); got != 30 {
		t.Fatalf("st.samples = %d, want 30 (10 batches x n=3)", got)
	}
}

// A clk record in a file with no batches would be a session that looks like it captured
// something. They are gated together.
func TestClockPairsAreGatedWithTheBatches(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "accel-test.jsonl")
	w, err := newWriter(path, map[string]any{"t": "hdr"}, 0)
	if err != nil {
		t.Fatal(err)
	}
	p := newPool(64)
	in := &intent{}
	in.writing.Store(false)

	done := make(chan struct{})
	go func() { writeLoop(p, w, &stats{}, in); close(done) }()
	feed(t, p, 4)
	close(p.filled)
	<-done
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}

	batches, clks := countRecords(t, path)
	if batches != 0 || clks != 0 {
		t.Fatalf("a paused writer emitted %d batch and %d clk records; want none of either",
			batches, clks)
	}
}
