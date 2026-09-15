package main

import (
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

// Every graceful `coord stop` we have observed was with the PYTHON reader in the
// container; every hang has been with this binary in it. That correlation is not
// proof, but the shutdown path is the part of it that is ours, so it gets a test
// with a hard deadline rather than an argument.
//
// The path under test is run()'s teardown, in order: the capture loop returns on
// the stop signal, pool.filled is closed, the writer goroutine drains whatever is
// queued, wg.Wait() joins it, and writer.Close() flushes and fsyncs. A deadlock
// anywhere in that chain would present exactly as `docker compose stop` never
// returning, because the container's PID 1 waits on the process that never exits.

// drainWriter is the writer goroutine's loop, extracted verbatim in shape so the
// test exercises the same channel discipline run() uses.
func drainWriter(p *pool, w *writer, wg *sync.WaitGroup) {
	defer wg.Done()
	for b := range p.filled {
		_ = w.record(map[string]any{
			"t": "b", "n": b.N, "x": b.X, "y": b.Y, "z": b.Z,
		})
		p.recycle(b)
	}
}

func TestShutdownCompletesWithinADeadline(t *testing.T) {
	dir := t.TempDir()
	w, err := newWriter(filepath.Join(dir, "accel-camera.jsonl"),
		map[string]any{"type": "header"}, 64*1024)
	if err != nil {
		t.Fatal(err)
	}
	p := newPool(256)
	var wg sync.WaitGroup
	wg.Add(1)
	go drainWriter(p, w, &wg)

	// Fill the pool completely, so teardown has the worst case to drain: every
	// batch queued, none recycled. This is the state a busy reader is in when a
	// stop arrives.
	for i := 0; i < 256; i++ {
		b := p.get()
		if b == nil {
			break
		}
		b.N = 2
		b.X = append(b.X[:0], 1, 2)
		b.Y = append(b.Y[:0], 3, 4)
		b.Z = append(b.Z[:0], 5, 6)
		p.put(b)
	}

	done := make(chan struct{})
	go func() {
		close(p.filled)
		wg.Wait()
		_ = w.Close()
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(20 * time.Second):
		t.Fatal("shutdown did not complete in 20s with a full queue -- this is what " +
			"`docker compose stop` hanging looks like from the outside")
	}
}

// The capture loop must notice the stop signal promptly. If it does not, SIGTERM
// is effectively ignored and docker falls back to SIGKILL after its timeout.
func TestCaptureLoopReturnsPromptlyOnStop(t *testing.T) {
	f := newFake()
	for i := 0; i < 8; i++ {
		f.fifo = append(f.fifo, [3]int16{1, 2, 3})
	}
	s := newSensor("camera", "/dev/null", f, 3200, 16)
	if err := s.configure(); err != nil {
		t.Fatal(err)
	}
	p := newPool(8)
	var wg sync.WaitGroup
	wg.Add(1)
	w, err := newWriter(filepath.Join(t.TempDir(), "a.jsonl"), map[string]any{"type": "header"}, 4096)
	if err != nil {
		t.Fatal(err)
	}
	go drainWriter(p, w, &wg)

	stop := make(chan os.Signal, 1)
	lives := []*live{{s: s, pool: p, st: &stats{}}}

	returned := make(chan struct{})
	go func() { capture(lives, config{odrHz: 3200}, stop); close(returned) }()

	time.Sleep(200 * time.Millisecond)
	stop <- os.Interrupt

	select {
	case <-returned:
	case <-time.After(5 * time.Second):
		t.Fatal("capture() did not return within 5s of the stop signal")
	}
	close(p.filled)
	wg.Wait()
	_ = w.Close()
}
