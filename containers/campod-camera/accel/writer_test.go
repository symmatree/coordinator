package main

import (
	"os"
	"path/filepath"
	"testing"
)

type flushRec struct {
	T   string `json:"t"`
	Seq int    `json:"seq"`
	XYZ []int  `json:"xyz"`
}

// growthSteps writes records through a writer with the given threshold and
// returns the on-disk size increments -- i.e. where writeback boundaries fell.
func growthSteps(t *testing.T, threshold int64, records int) []int64 {
	t.Helper()
	path := filepath.Join(t.TempDir(), "accel-test.jsonl")
	w, err := newWriter(path, map[string]any{"t": "hdr"}, threshold)
	if err != nil {
		t.Fatalf("newWriter: %v", err)
	}
	defer w.Close()

	var steps []int64
	last := int64(-1)
	for i := 0; i < records; i++ {
		if err := w.record(flushRec{T: "b", Seq: i, XYZ: []int{i, -i, i * 2}}); err != nil {
			t.Fatalf("record %d: %v", i, err)
		}
		st, err := os.Stat(path)
		if err != nil {
			t.Fatalf("stat: %v", err)
		}
		if st.Size() != last {
			if last >= 0 {
				steps = append(steps, st.Size()-last)
			}
			last = st.Size()
		}
	}
	return steps
}

// syncEvery has to be what actually controls the flush boundary. Two different
// thresholds must produce two different step sizes -- a test that used the
// production constant on both sides would pass no matter what the value was,
// and would tell us nothing about whether the field is wired up at all.
//
// This deliberately does not assert the production value. Which byte count is
// right is a question for the card, not for a unit test.
func TestThresholdControlsFlushBoundary(t *testing.T) {
	for _, threshold := range []int64{32 * 1024, 128 * 1024} {
		steps := growthSteps(t, threshold, 30000)
		if len(steps) < 3 {
			t.Fatalf("threshold %d: expected several flushes, got steps %v", threshold, steps)
		}
		for i, step := range steps {
			if step < threshold {
				t.Errorf("threshold %d: step %d was %d bytes, below the threshold", threshold, i, step)
			}
			if step > threshold*2 {
				t.Errorf("threshold %d: step %d was %d bytes, more than double the threshold", threshold, i, step)
			}
		}
	}
}
