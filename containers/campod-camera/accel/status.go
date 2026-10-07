package main

// Per-sensor presence and liveness, written where another container can read it.
//
// The question this answers is the pre-flight one -- are both accelerometers
// there, and is each one producing -- and it is answerable only in here. The
// labels, the DEVID probe and the self-test verdict all live in this process,
// and until now the only place any of it appeared was the container log. A log is
// not reachable from a bus, and reading one costs an ssh into a board that
// cannot spare the fork.
//
// A FILE, not an MQTT publish from here. One resident broker client per pod is
// the footprint budget and it is pod-link's; and a small file another container
// reads is already the idiom for state that has to cross a container boundary
// here (the arm file, the capture flag). One pattern rather than three.
//
// A SKIPPED DEVICE STILL APPEARS. An ADXL345 whose DEVID read does not come back
// is dropped from the capture set, and would otherwise vanish from the record
// entirely -- indistinguishable from a pod built with one sensor. The field fault
// these parts actually have is a half-seated header, so "the arm end is absent,
// DEVID read 0x00" is the sentence worth getting off the vehicle before a flight
// rather than after one.

import (
	"encoding/json"
	"fmt"
	"os"
	"time"
)

// How often the status file is rewritten. Fixed rather than configurable: it is a
// json.Marshal and a rename onto tmpfs, the file has exactly one consumer, and
// that consumer polls on its own period anyway. A knob here would be a second
// place to set a rate nothing reads.
const statusInterval = 2 * time.Second

// deviceStatus is one chip select's worth of answer.
//
// Counters are cumulative for the session, deliberately. A rate computed here
// would be this process's opinion about an interval; two cumulative reads with
// the timestamps beside them let the consumer pick its own window. Same reason
// the sample stream carries counters rather than rates.
type deviceStatus struct {
	Label string `json:"label"`
	// Present means the part answered its DEVID read and is in the capture set.
	Present bool `json:"present"`
	// Detail says why not, when not: the open error, or the DEVID that came back.
	Detail string `json:"detail,omitempty"`
	// SelfTest is pass or fail from the datasheet's electrostatic test, run once
	// at startup. Empty for a device that never got that far.
	SelfTest string `json:"self_test,omitempty"`
	Samples  int64  `json:"samples"`
	Batches  int64  `json:"batches"`
	Drops    int64  `json:"drops"`
	Errs     int64  `json:"errs"`
	// LastSampleBootNS is CLOCK_BOOTTIME at the most recent drain. Present and
	// not advancing is a different fault from absent, and this is what separates
	// them -- the consumer differences it against the document's own as_of.
	LastSampleBootNS int64 `json:"last_sample_boot_ns"`
}

// accelStatus is the whole document.
//
// No wall clock anywhere in it. This board has no RTC, so a wall stamp written
// before time service arrives is wrong by however large the step turns out to be,
// and nothing in the document would say which side of the step it came from.
// Everything here is time since boot, which is true at every moment of a session.
type accelStatus struct {
	Session    string          `json:"session"`
	Reader     string          `json:"reader"`
	AsOfBootNS int64           `json:"as_of_boot_ns"`
	Devices    []*deviceStatus `json:"devices"`
}

// statusWriter owns the file and the set of devices it reports on.
//
// absent is fixed at startup (a chip select either answered or it did not);
// lives are read live, because their counters are what makes "capturing" a
// different statement from "present".
type statusWriter struct {
	path      string
	session   string
	reader    string
	lives     []*live
	selfTests map[string]string
	absent    []*deviceStatus
}

// absentDevice records a chip select that did not come up, with the reason.
func absentDevice(label string, err error) *deviceStatus {
	return &deviceStatus{Label: label, Detail: err.Error()}
}

// snapshot builds the document from the current counters.
//
// Devices come out in the wiring order of `devices`, present or not, so a reader
// diffing two pods sees the same two rows in the same places rather than a list
// whose length encodes a fault.
func (w *statusWriter) snapshot(nowBootNS int64) *accelStatus {
	s := &accelStatus{Session: w.session, Reader: w.reader, AsOfBootNS: nowBootNS}
	for _, d := range devices {
		if a := w.absentFor(d.label); a != nil {
			s.Devices = append(s.Devices, a)
			continue
		}
		if lv := w.liveFor(d.label); lv != nil {
			s.Devices = append(s.Devices, &deviceStatus{
				Label:            lv.s.label,
				Present:          true,
				SelfTest:         w.selfTests[lv.s.label],
				Samples:          lv.st.samples.Load(),
				Batches:          lv.st.batches.Load(),
				Drops:            lv.pool.drops.Load(),
				Errs:             lv.st.readErrs.Load(),
				LastSampleBootNS: lv.st.lastSampleNS.Load(),
			})
		}
	}
	return s
}

func (w *statusWriter) absentFor(label string) *deviceStatus {
	for _, a := range w.absent {
		if a.Label == label {
			return a
		}
	}
	return nil
}

func (w *statusWriter) liveFor(label string) *live {
	for _, lv := range w.lives {
		if lv.s.label == label {
			return lv
		}
	}
	return nil
}

// write replaces the file atomically.
//
// Temp plus rename, so a reader's single open-and-parse can never land on a
// half-written document -- the same discipline the capture flag and the arm file
// use, and the reason the consumer needs no locking.
func (w *statusWriter) write(nowBootNS int64) error {
	body, err := json.Marshal(w.snapshot(nowBootNS))
	if err != nil {
		return err
	}
	tmp := w.path + ".tmp"
	if err := os.WriteFile(tmp, append(body, '\n'), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, w.path)
}

// loop rewrites the file until done is closed.
//
// Its own goroutine, because the capture loop must do nothing but drain: the FIFO
// holds 32 samples, 10 ms at 3200 Hz, and anything that delays a cycle is lost in
// silicon before any of our code runs. Marshalling JSON does not belong on that
// path, for the same reason the clock-pair record is emitted from the writer
// goroutine instead.
//
// A write failure is logged once per distinct message and is never fatal. A pod
// that cannot write its status is still a pod collecting data, and the data is
// the deliverable.
func (w *statusWriter) loop(done <-chan struct{}) {
	t := time.NewTicker(statusInterval)
	defer t.Stop()
	var lastErr string
	for {
		if err := w.write(bootNS()); err != nil && err.Error() != lastErr {
			lastErr = err.Error()
			fmt.Fprintf(os.Stderr, "accel: cannot write status to %s: %v\n", w.path, err)
		}
		select {
		case <-done:
			return
		case <-t.C:
		}
	}
}
