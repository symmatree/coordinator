package main

// What a pod says about itself.
//
// One document, retained, so a late subscriber -- a front panel that just booted,
// a ground screen on reconnect -- gets the whole current picture in one subscribe
// with nothing to assemble from a stream it missed.
//
// It carries BOTH the desired state and the observed state. That is what makes
// "did it take" a single read: no cross-referencing two topics, no inferring
// success from the absence of an error. If they disagree, either the pod is
// mid-transition or the errors list says why it cannot get there.
//
// It also carries, verbatim, the documents the two capture processes write. This
// process deliberately does not parse them: adding a field to capture.py's status
// must not require a matching change here, and a schema repeated in two languages
// is a schema that drifts. The only thing checked is that each is valid JSON,
// because embedding a malformed fragment would take the whole document down with
// it.
//
// EVERY CLOCK IN HERE IS SECONDS SINCE BOOT. The pod has no RTC, so its wall clock
// is wrong by an unknown amount until time service arrives and then steps -- and
// nothing in a document can say which side of that step it was written on. The
// camera's monotonic, the accel's boot_ns and this process's uptime are all the
// same family and can be differenced against each other; a wall stamp could not.
// A consumer that wants absolute time gets it from the coordinator, which is the
// machine with an upstream.

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

// podStatus is the published shape. Field names are the contract; see
// docs/pod-bus.md.
type podStatus struct {
	// State is liveness, and "gone" is published by the broker as our will when
	// the connection drops. Without it, absence and silence look identical.
	State string `json:"state"`
	Node  string `json:"node"`
	Build string `json:"build,omitempty"`
	// AsOfBootS is this document's age, as seconds since boot. A document whose
	// as_of stops advancing is a dead publisher, which is a different fault from
	// anything it reports inside.
	AsOfBootS float64 `json:"as_of_boot_s"`

	// Observed.
	Capture bool   `json:"capture"`
	Stack   string `json:"stack"`
	// Nil unless the container inits were actually counted, which happens only when
	// there is a stack desire to reconcile against. Absent means nobody looked.
	StackInits *int   `json:"stack_inits,omitempty"`
	Radio      string `json:"radio"`
	// Per-container state, for the containers somebody has expressed a desire about.
	// Only those: listing every container would mean an inspect per container per
	// pass for answers nobody asked for.
	Services map[string]string `json:"services,omitempty"`

	// What this pod was last told to be. Present only for concerns somebody has
	// actually published a desire for.
	Desired map[string]string `json:"desired,omitempty"`

	DataFreeBytes int64 `json:"data_free_bytes,omitempty"`

	// The capture processes' own documents, passed through untouched.
	Camera json.RawMessage `json:"camera,omitempty"`
	Accel  json.RawMessage `json:"accel,omitempty"`

	// Why the pod is not in the state it was asked for. A refused polkit
	// authorisation and an unmounted D-Bus socket both land here rather than in a
	// log nobody can reach from the bus.
	Errors []string `json:"errors,omitempty"`
}

// uptimeS is seconds since boot, from /proc/uptime.
//
// Go's time package has no CLOCK_MONOTONIC read that is comparable across
// processes -- time.Since measures from an arbitrary start. /proc/uptime is the
// same clock capture.py stamps with time.monotonic(), so the three documents in
// this status share one timeline.
func uptimeS() float64 {
	body, err := os.ReadFile(filepath.Join(procRoot, "uptime"))
	if err != nil {
		return 0
	}
	first, _, _ := strings.Cut(strings.TrimSpace(string(body)), " ")
	v, err := strconv.ParseFloat(first, 64)
	if err != nil {
		return 0
	}
	return v
}

// freeBytes is what a writer can actually use at path.
//
// Bavail, not Bfree: the difference is the filesystem's own reserve, which is not
// ours to fill. The number is here because a card that fills does not present as
// a disk error -- it presents as a pod that cannot capture and stops answering,
// which has invited several wrong explanations. It fills in about 4.2 hours and
// nothing caps it, so "this pod cannot hold another flight" is worth saying before
// the flight rather than diagnosing after it.
func freeBytes(path string) (int64, error) {
	var st syscall.Statfs_t
	if err := syscall.Statfs(path, &st); err != nil {
		return 0, fmt.Errorf("statfs %s: %w", path, err)
	}
	return int64(st.Bavail) * int64(st.Bsize), nil
}

// passThrough reads one of the capture processes' status documents.
//
// Absent is not an error worth reporting: a stack running only the camera has no
// accel document, and a bench run has neither. Present-but-malformed IS worth
// reporting, because it means a writer is broken rather than absent.
func passThrough(path string) (json.RawMessage, error) {
	if path == "" {
		return nil, nil
	}
	body, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("reading %s: %w", path, err)
	}
	if !json.Valid(body) {
		return nil, fmt.Errorf("%s is not valid JSON (%d bytes)", path, len(body))
	}
	return json.RawMessage(body), nil
}
