package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The node name has to come from the HOST. A container's hostname is its
// container ID (measured: e2e7f038824a on campod-se), and it changes on every
// recreate -- so falling back to it scatters one pod's captures across a new
// directory per restart, which is worse than collecting them under one wrong
// name. #272 was the other failure: a literal in a stack file describing four
// pods, so every pod claimed to be campod-sw.
func TestNodeNamePrefersTheHostsName(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "host-hostname")
	orig := hostHostnamePath
	hostHostnamePath = path
	defer func() { hostHostnamePath = orig }()

	// /etc/hostname has a trailing newline; it must not end up in a path.
	if err := os.WriteFile(path, []byte("campod-se\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := nodeName(); got != "campod-se" {
		t.Fatalf("nodeName() = %q, want %q -- a trailing newline would end up in the capture path", got, "campod-se")
	}

	// An empty mount must not win: an empty node name would put captures at
	// /captures//session, which is a silently different place.
	if err := os.WriteFile(path, []byte("   \n"), 0o644); err != nil {
		t.Fatal(err)
	}
	host, _ := os.Hostname()
	if got := nodeName(); got != host {
		t.Fatalf("with a blank mount, nodeName() = %q, want the os hostname %q", got, host)
	}

	// Absent entirely -- running outside a container, where os.Hostname is right.
	hostHostnamePath = filepath.Join(dir, "does-not-exist")
	if got := nodeName(); got != host {
		t.Fatalf("with no mount, nodeName() = %q, want %q", got, host)
	}
}

// Which commit wrote a capture cannot be reconstructed afterwards, so the header
// records it. An unstamped build must SAY it is unstamped rather than imply a
// provenance it does not have.
func TestReaderIDReportsTheBuild(t *testing.T) {
	orig := buildSHA
	defer func() { buildSHA = orig }()

	buildSHA = ""
	got := readerID()
	if !strings.Contains(got, "local build") {
		t.Errorf("unstamped readerID() = %q, want it to admit it is a local build", got)
	}
	if strings.Contains(got, "  ") {
		t.Errorf("unstamped readerID() = %q, has a dangling separator", got)
	}

	buildSHA = "0123456789abcdef"
	got = readerID()
	if !strings.Contains(got, "0123456789abcdef") {
		t.Errorf("stamped readerID() = %q, want it to carry the SHA", got)
	}
	if strings.Contains(got, "local build") {
		t.Errorf("stamped readerID() = %q, still claims to be a local build", got)
	}
}
