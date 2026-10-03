package main

import (
	"os"
	"path/filepath"
	"testing"
)

// The gate's failure direction is the whole point. capture.py treats absent or
// unreadable as PAUSED, and this must agree with it: if the two disagree, a pod
// captures when nothing told it to, which is the behaviour being removed.
func TestReadFlagFileFailsClosed(t *testing.T) {
	dir := t.TempDir()
	missing := filepath.Join(dir, "nope")
	if readFlagFile(missing) {
		t.Error("absent file must read as false")
	}
	if readFlagFile(dir) {
		t.Error("a directory must read as false, not panic or true")
	}
	for _, tc := range []struct {
		body string
		want bool
	}{
		{"1", true}, {"true", true}, {"on", true}, {"yes", true},
		{" 1\n", true}, {"1\r\n", true},
		{"0", false}, {"", false}, {"off", false}, {"nonsense", false},
		{"2", false}, {"TRUE", false},
	} {
		p := filepath.Join(dir, "flag")
		if err := os.WriteFile(p, []byte(tc.body), 0o644); err != nil {
			t.Fatal(err)
		}
		if got := readFlagFile(p); got != tc.want {
			t.Errorf("readFlagFile(%q) = %t, want %t", tc.body, got, tc.want)
		}
	}
}

// Written atomically, because capture.py reads this file once per tick with no
// locking: a torn read would be a frame captured or missed on a partial write.
func TestWriteFlagFileIsAtomicAndRoundTrips(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "capture")

	for _, want := range []bool{true, false, true} {
		if err := writeFlagFile(p, want); err != nil {
			t.Fatal(err)
		}
		if got := readFlagFile(p); got != want {
			t.Errorf("round trip %t -> %t", want, got)
		}
	}

	// No temp file left behind: a stray .tmp beside the flag on a card we are
	// about to lose power on is litter at best and confusing at worst.
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if filepath.Ext(e.Name()) == ".tmp" {
			t.Errorf("left a temp file behind: %s", e.Name())
		}
	}
}

func TestLoadConfigRejectsAnUnsetRole(t *testing.T) {
	t.Setenv("POD_LINK_ROLE", "")
	if _, err := loadConfig(); err == nil {
		t.Error("an unset role must be an error, not a default -- a device that picks" +
			" a role for itself is one that can publish when it meant to subscribe")
	}
	t.Setenv("POD_LINK_ROLE", "sometimes")
	if _, err := loadConfig(); err == nil {
		t.Error("an unknown role must be rejected")
	}
	t.Setenv("POD_LINK_ROLE", "subscriber")
	if _, err := loadConfig(); err != nil {
		t.Errorf("subscriber must be accepted: %v", err)
	}
}

func TestLoadConfigRejectsANonsensePeriod(t *testing.T) {
	t.Setenv("POD_LINK_ROLE", "publisher")
	for _, bad := range []string{"0", "-1", "soon"} {
		t.Setenv("POD_LINK_PERIOD_S", bad)
		if _, err := loadConfig(); err == nil {
			t.Errorf("POD_LINK_PERIOD_S=%q must be rejected rather than silently defaulted", bad)
		}
	}
}

// The topic a pod reports on must be its own. One shared status topic would mean
// the last pod to publish is the only one the coordinator can see.
func TestStatusTopicIsPerNode(t *testing.T) {
	a := topicFor("campod-se")
	b := topicFor("campod-sw")
	if a == b {
		t.Errorf("two pods share a status topic: %s", a)
	}
}
