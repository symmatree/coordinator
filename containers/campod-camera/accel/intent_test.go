package main

import (
	"os"
	"path/filepath"
	"testing"
)

// The vocabulary has to match capture.py exactly. Two readers on the same file that
// disagree about what "1" means would be worse than either default on its own: a pod
// would write images without accel, or the reverse, and the data would not say why.
func TestIntentVocabularyMatchesTheCamera(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "campod_capture")
	in := &intent{path: path}

	for _, tc := range []struct {
		body string
		want bool
	}{
		{"1", true}, {"true", true}, {"on", true}, {"yes", true},
		{"1\n", true}, {"  on  \n", true},
		{"0", false}, {"false", false}, {"off", false}, {"no", false},
		{"", false}, {"\n", false},
		// Not in the vocabulary, so not writing. The alternative -- treating anything
		// unrecognised as "go" -- is how a typo fills a card.
		{"On", false}, {"TRUE", false}, {"maybe", false}, {"2", false},
	} {
		if err := os.WriteFile(path, []byte(tc.body), 0o644); err != nil {
			t.Fatal(err)
		}
		if got := in.read(); got != tc.want {
			t.Errorf("read(%q) = %v, want %v", tc.body, got, tc.want)
		}
	}
}

// ABSENT MEANS NO. This is the whole point of the change: an unattended pod filled a
// 29 GB card to 99% overnight because nothing gated the writes. A gate that opens when
// its input is missing would reproduce that exactly.
func TestAMissingFlagMeansNotWriting(t *testing.T) {
	in := &intent{path: filepath.Join(t.TempDir(), "never-created")}
	if in.read() {
		t.Fatal("a missing flag file read as 'writing'; absent must mean paused")
	}
}

// An unreadable flag is also no -- same reason. A directory stands in for any OSError.
func TestAnUnreadableFlagMeansNotWriting(t *testing.T) {
	dir := t.TempDir()
	in := &intent{path: dir} // reading a directory fails
	if in.read() {
		t.Fatal("an unreadable flag read as 'writing'")
	}
}

// Empty path is the explicit opt-out: no gating, write continuously. That is what every
// deployment did before this existed and what a bench run with no coordinator wants, so
// it has to stay reachable rather than becoming a thing only old configs had.
func TestAnEmptyPathDisablesGating(t *testing.T) {
	in := &intent{path: ""}
	if !in.read() {
		t.Fatal("an empty gate path must mean write continuously, not never write")
	}
}

// newIntent must settle before it returns. If it left `writing` false until the first
// poll tick, a pod that is armed at startup would silently discard its first second.
func TestNewIntentReadsBeforeReturning(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "flag")
	if err := os.WriteFile(path, []byte("1\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if in := newIntent(path); !in.writing.Load() {
		t.Fatal("newIntent returned with writing=false on an already-armed pod; " +
			"the first second of an armed capture would be dropped")
	}
	if in := newIntent(filepath.Join(dir, "absent")); in.writing.Load() {
		t.Fatal("newIntent returned writing=true with no flag present")
	}
}
