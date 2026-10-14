package main

import (
	"testing"
)

// The set is configuration, so the risks are in reading it: an empty variable has to mean
// "nothing here collects" rather than "stop something", and the three spellings a quadlet
// file might use have to land on the same unit.
func TestCollectionUnitsReadsTheConfiguredSet(t *testing.T) {
	for _, tc := range []struct {
		name string
		env  string
		want []string
	}{
		{"unset is nothing to stop", "", nil},
		{"only separators is nothing to stop", " , ,, ", nil},
		{"one bare name", "campod-camera", []string{"campod-camera.service"}},
		{"the quadlet spelling resolves", "campod-camera.container",
			[]string{"campod-camera.service"}},
		{"the unit spelling is left alone", "campod-camera.service",
			[]string{"campod-camera.service"}},
		{"several, with the spaces a human leaves", " campod-camera , campod-accel ",
			[]string{"campod-camera.service", "campod-accel.service"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("POD_LINK_COLLECTION_UNITS", tc.env)
			got := collectionUnits()
			if len(got) != len(tc.want) {
				t.Fatalf("got %v, want %v", got, tc.want)
			}
			for i := range got {
				if got[i] != tc.want[i] {
					t.Fatalf("got %v, want %v", got, tc.want)
				}
			}
		})
	}
}

// An empty set must not reach for the system bus. A device with nothing to collect is
// usually a device with no /run/dbus mounted either, and "nothing to stop" should be
// silent rather than an error about a socket it never needed.
//
// h.conn is nil and h.bus() would dial; if quiesce touched it this would fail rather than
// return.
func TestQuiesceWithNothingConfiguredDoesNotTouchTheBus(t *testing.T) {
	h := &host{}
	if err := h.quiesce(nil); err != nil {
		t.Fatalf("quiesce(nil) = %v, want nil", err)
	}
	if h.conn != nil {
		t.Fatal("quiesce(nil) opened a D-Bus connection; it should not need one")
	}
}
