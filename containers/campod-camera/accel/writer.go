package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"syscall"
)

// SYNC_FILE_RANGE_WRITE: start writeback on the range, do not wait for it.
const syncFileRangeWrite = 2

// writer owns a JSONL file and the only blocking I/O in the program.
//
// Two different operations get called "sync" and conflating them is what made
// the Python reader stall. fsync() forces data to the card AND WAITS -- on SD
// that also flushes the FTL mapping, which is where tens of milliseconds come
// from. sync_file_range(WRITE) merely STARTS writeback and returns.
//
// So: buffer, and call sync_file_range as we go to keep dirty pages flowing and
// bounded. Never fsync on a timer. The kernel's dirty_ratio default of 20% is
// ~83 MB on a 416 MB campod, and reaching it makes the KERNEL throttle the
// writer synchronously at a moment nobody chose -- deferring syncs without
// bounding dirty pages just relocates the stall somewhere worse.
//
// fsync happens exactly once, at Close, which is the moment durability is
// actually wanted: the operator is about to pull the plug.
type writer struct {
	f         *os.File
	bw        *bufio.Writer
	enc       *json.Encoder
	written   int64 // bytes handed to the kernel
	synced    int64 // bytes we have asked it to start writing back
	syncEvery int64
}

func newWriter(path string, header any, syncEvery int64) (*writer, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, err
	}
	bw := bufio.NewWriterSize(f, 256*1024)
	w := &writer{f: f, bw: bw, enc: json.NewEncoder(bw), syncEvery: syncEvery}
	if err := w.enc.Encode(header); err != nil {
		f.Close()
		return nil, err
	}
	return w, nil
}

func (w *writer) record(v any) error {
	if err := w.enc.Encode(v); err != nil {
		return err
	}
	w.written = int64(w.bw.Size()-w.bw.Available()) + w.flushedBytes()
	return w.maybeStartWriteback()
}

// bufio does not expose how much it has flushed, so track it by draining the
// buffer on a fixed boundary instead of guessing.
func (w *writer) flushedBytes() int64 { return w.synced }

func (w *writer) maybeStartWriteback() error {
	if w.bw.Buffered() < int(w.syncEvery) {
		return nil
	}
	if err := w.bw.Flush(); err != nil {
		return err
	}
	off, err := w.f.Seek(0, 1)
	if err != nil {
		return err
	}
	// Kick writeback for everything we have not kicked yet. Non-blocking: this
	// queues the pages, it does not wait for the card.
	if off > w.synced {
		if err := syscall.SyncFileRange(int(w.f.Fd()), w.synced, off-w.synced, syncFileRangeWrite); err != nil {
			return fmt.Errorf("sync_file_range: %w", err)
		}
		w.synced = off
	}
	return nil
}

// Close flushes, then fsyncs once. This is the only fsync in the program.
func (w *writer) Close() error {
	if err := w.bw.Flush(); err != nil {
		w.f.Close()
		return err
	}
	if err := w.f.Sync(); err != nil {
		w.f.Close()
		return err
	}
	return w.f.Close()
}
