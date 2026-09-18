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

// How much to buffer before kicking writeback.
//
// WHY THERE IS ANY KICKING: to keep the dirty page pool bounded, rather than
// letting it build until the kernel's own thresholds force writeback in a lump.
// That is a prospective concern and was never a measured one -- no stall here
// was traced to it. It is an addition, so "off" is the simpler behaviour, not a
// deviation from it.
//
// The size is a knob, not a measured optimum. CAMPOD_ACCEL_SYNC_KIB overrides it
// so values can be compared on a device without an image build each time, and 0
// stops kicking altogether, leaving bufio to flush when its buffer fills and the
// kernel to decide when the disk sees it. The deployed stack sets 0
// (coordinator#307). Whether kicking earns its cost at all is open --
// coordinator#319.
const syncEveryBytes = 128 * 1024

// writer owns a JSONL file and the only blocking I/O in the program.
//
// Two operations both get called "sync" and conflating them stalls the sample
// loop: fsync() forces data out AND WAITS, while sync_file_range(WRITE) only
// starts writeback and returns.
//
// This program never fsyncs. It buffers, and optionally kicks writeback so
// dirty pages keep moving without the sample loop waiting on I/O.
type writer struct {
	f         *os.File
	bw        *bufio.Writer
	enc       *json.Encoder
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
	return w.maybeStartWriteback()
}

func (w *writer) maybeStartWriteback() error {
	if w.syncEvery == 0 {
		return nil // 0 means do not kick at all; bufio still flushes when full
	}
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
	// queues the pages, it does not wait for them.
	if off > w.synced {
		if err := syscall.SyncFileRange(int(w.f.Fd()), w.synced, off-w.synced, syncFileRangeWrite); err != nil {
			return fmt.Errorf("sync_file_range: %w", err)
		}
		w.synced = off
	}
	return nil
}

// Close hands the buffer to the kernel and closes the file. No fsync: the pod
// dies by power pull, which never reaches Close, so an fsync here would only
// add a wait while something is trying to stop the container.
func (w *writer) Close() error {
	if err := w.bw.Flush(); err != nil {
		w.f.Close()
		return err
	}
	return w.f.Close()
}
