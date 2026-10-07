package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

// decode reads the status file back the way pod-link will.
func decode(t *testing.T, path string) *accelStatus {
	t.Helper()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading status: %v", err)
	}
	var s accelStatus
	if err := json.Unmarshal(body, &s); err != nil {
		t.Fatalf("status is not valid JSON: %v\n%s", err, body)
	}
	return &s
}

// A sensor that did not answer must still have a row. The whole reason this file
// exists is that a missing sensor used to be indistinguishable from a pod built
// with one, so a shorter list is the one failure mode that must not happen.
func TestAbsentDeviceStillAppearsWithItsReason(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "status")

	lv := &live{s: newSensor("camera", "/dev/null", newFake(), 3200, 16),
		pool: newPool(2), st: &stats{}}
	lv.st.samples.Store(4096)
	lv.st.lastSampleNS.Store(1234)

	w := &statusWriter{
		path: path, session: "boot-id", reader: "test",
		lives:     []*live{lv},
		selfTests: map[string]string{"camera": "pass"},
		absent:    []*deviceStatus{absentDevice("arm", errors.New("DEVID 0x00, expected 0xE5"))},
	}
	if err := w.write(9999); err != nil {
		t.Fatalf("write: %v", err)
	}

	got := decode(t, path)
	if len(got.Devices) != len(devices) {
		t.Fatalf("got %d device rows, want one per chip select (%d)", len(got.Devices), len(devices))
	}
	// Wiring order, so two pods diff row-for-row.
	if got.Devices[0].Label != "camera" || got.Devices[1].Label != "arm" {
		t.Fatalf("rows out of wiring order: %q then %q", got.Devices[0].Label, got.Devices[1].Label)
	}
	cam, arm := got.Devices[0], got.Devices[1]
	if !cam.Present || cam.SelfTest != "pass" || cam.Samples != 4096 || cam.LastSampleBootNS != 1234 {
		t.Errorf("present device reported wrong: %+v", cam)
	}
	if arm.Present {
		t.Error("the absent device is reported present")
	}
	if arm.Detail == "" {
		t.Error("the absent device carries no reason, which is the only useful part")
	}
	if got.AsOfBootNS != 9999 {
		t.Errorf("as_of_boot_ns = %d, want the value passed in", got.AsOfBootNS)
	}
}

// No wall clock in the document, ever. This board has no RTC, so a wall stamp is
// wrong by the size of the NTP step and nothing in the document says which side
// of the step produced it.
func TestStatusCarriesNoWallClock(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "status")
	w := &statusWriter{path: path, session: "s", reader: "r"}
	if err := w.write(7); err != nil {
		t.Fatalf("write: %v", err)
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	if err := json.Unmarshal(body, &raw); err != nil {
		t.Fatal(err)
	}
	for k := range raw {
		switch k {
		case "wall_ns", "wall_clock_unix", "wall_clock_utc", "started_utc":
			t.Errorf("status carries a wall clock field %q", k)
		}
	}
}

// A reader's single open-and-parse must never see a partial document, so the
// write goes to a temp name and renames. The tell is that no leftover temp
// survives a successful write.
func TestWriteLeavesNoTempBehind(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "status")
	w := &statusWriter{path: path, session: "s", reader: "r"}
	for i := 0; i < 3; i++ {
		if err := w.write(int64(i)); err != nil {
			t.Fatalf("write %d: %v", i, err)
		}
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != "status" {
		names := []string{}
		for _, e := range entries {
			names = append(names, e.Name())
		}
		t.Errorf("directory holds %v, want only the status file", names)
	}
}

// An unwritable path must not be fatal and must not panic: a pod that cannot
// write its status is still a pod collecting data, and the data is the point.
func TestWriteFailureIsReturnedNotFatal(t *testing.T) {
	w := &statusWriter{path: filepath.Join(t.TempDir(), "no-such-dir", "status")}
	if err := w.write(1); err == nil {
		t.Error("writing into a missing directory reported success")
	}
}
