package main

import (
	"os"
	"strings"
	"testing"
)

// unitsFromQuadlet reads POD_LINK_COLLECTION_UNITS out of a deployed quadlet file, so the
// test checks what ships rather than a copy of it kept here.
func unitsFromQuadlet(t *testing.T, path string) []string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading %s: %v", path, err)
	}
	const key = "Environment=POD_LINK_COLLECTION_UNITS="
	for _, ln := range strings.Split(string(b), "\n") {
		if ln = strings.TrimSpace(ln); strings.HasPrefix(ln, key) {
			t.Setenv("POD_LINK_COLLECTION_UNITS", strings.TrimPrefix(ln, key))
			return collectionUnits()
		}
	}
	t.Fatalf("%s sets no POD_LINK_COLLECTION_UNITS", path)
	return nil
}

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

// pod-link must never be in the set it stops -- it is the thing that would carry the
// instruction to undo it, and a device that signals its own foothold drops off the bus
// until someone reboots it by hand. That is what the old fleet-wide pkill did.
//
// Configuration cannot be checked at runtime without second-guessing the operator, so this
// asserts the deployed quadlets instead: whatever they set, it is not pod-link.
func TestTheDeployedSetsNeverIncludePodLink(t *testing.T) {
	for _, q := range []string{
		"../../stacks/campod/pod-link.container",
		"../../stacks/coordinator/pod-link.container",
	} {
		for _, u := range unitsFromQuadlet(t, q) {
			if u == "pod-link.service" {
				t.Errorf("%s puts pod-link in POD_LINK_COLLECTION_UNITS", q)
			}
		}
	}
}
