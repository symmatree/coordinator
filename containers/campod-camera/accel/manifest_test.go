package main

import (
	"os"
	"path/filepath"
	"testing"
)

// A capture directory has to say what wrote it. Assert the files land with the
// right names and contents, including that NAME= drives the output filename.
func TestCopyManifestsRecordsProvenance(t *testing.T) {
	src := t.TempDir()
	container := filepath.Join(src, "container-image")
	fleet := filepath.Join(src, "fleet-image")
	body := "# comment\nNAME=campod-camera\nREVISION=deadbeef\n"
	if err := os.WriteFile(container, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(fleet, []byte("IMAGE=campod-pi-20260918.img\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	old := manifestSources
	manifestSources = []string{container, fleet}
	defer func() { manifestSources = old }()

	session := t.TempDir()
	copyManifests(session)

	got, err := os.ReadFile(filepath.Join(session, "manifests", "campod-camera"))
	if err != nil {
		t.Fatalf("NAME= did not drive the output filename: %v", err)
	}
	if string(got) != body {
		t.Errorf("content differs:\n got %q\nwant %q", got, body)
	}
	if _, err := os.ReadFile(filepath.Join(session, "manifests", "fleet-image")); err != nil {
		t.Errorf("fleet-image not recorded: %v", err)
	}
	// No temp files left behind by the rename.
	ents, _ := os.ReadDir(filepath.Join(session, "manifests"))
	for _, e := range ents {
		if e.Name()[0] == '.' {
			t.Errorf("temp file left behind: %s", e.Name())
		}
	}
}

// A missing manifest must not stop capture -- losing frames is worse than an
// unattributed session.
func TestCopyManifestsSurvivesMissingSources(t *testing.T) {
	old := manifestSources
	manifestSources = []string{filepath.Join(t.TempDir(), "absent")}
	defer func() { manifestSources = old }()
	session := t.TempDir()
	copyManifests(session) // must not panic or exit
	if _, err := os.Stat(filepath.Join(session, "manifests")); err != nil {
		t.Errorf("manifests dir should still exist: %v", err)
	}
}
