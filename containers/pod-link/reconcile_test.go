package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fakeProc builds a /proc-shaped directory: pid -> comm.
func fakeProc(t *testing.T, comms map[int]string) string {
	t.Helper()
	root := t.TempDir()
	for pid, comm := range comms {
		dir := filepath.Join(root, fmt.Sprint(pid))
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "comm"), []byte(comm+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// Non-numeric entries are what /proc is actually full of.
	for _, name := range []string{"self", "uptime", "meminfo"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("0\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func withProc(t *testing.T, root string) {
	t.Helper()
	old := procRoot
	procRoot = root
	t.Cleanup(func() { procRoot = old })
}

// The agent must not be in the set it manages. If pod-link's own init were
// included, stopping the stack would kill the process performing the stop -- and
// then nothing is left to take the pod back the other way.
func TestStackInitsExcludesOurOwnInit(t *testing.T) {
	own := 41
	withProc(t, fakeProc(t, map[int]string{
		own:         initComm, // ours
		77:          initComm, // the camera container
		78:          initComm, // the accel container
		99:          "python3",
		os.Getpid(): "pod-link",
	}))

	pids, err := stackInits(own)
	if err != nil {
		t.Fatalf("stackInits: %v", err)
	}
	if len(pids) != 2 {
		t.Fatalf("got %v, want exactly the two sibling inits", pids)
	}
	for _, pid := range pids {
		if pid == own {
			t.Error("our own init is in the list; stopping the stack would kill us")
		}
		if pid == os.Getpid() {
			t.Error("our own pid is in the list")
		}
	}
}

// A pid directory with no readable comm is a process that exited between the
// listing and the read. That is ordinary, not an error -- treating it as one would
// make the whole observation fail on a busy box.
func TestStackInitsToleratesAVanishedProcess(t *testing.T) {
	root := fakeProc(t, map[int]string{77: initComm})
	if err := os.Remove(filepath.Join(root, "77", "comm")); err != nil {
		t.Fatal(err)
	}
	withProc(t, root)

	pids, err := stackInits(1)
	if err != nil {
		t.Fatalf("stackInits: %v", err)
	}
	if len(pids) != 0 {
		t.Errorf("got %v, want none", pids)
	}
}

// With no inits but ours, the stack is stopped -- which is the observation that
// replaces a wait loop. `pkill` returns before the processes are gone, so anything
// that asserted success from the signal would be lying for up to 19 s.
func TestStackReadsStoppedOnlyWhenTheInitsAreActuallyGone(t *testing.T) {
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"), dataDir: dir}
	want := newWanted()
	want.set("stack", stackRunning) // a desire, so the observation is taken at all

	withProc(t, fakeProc(t, map[int]string{1: initComm, 2: "sshd"}))
	doc := reconcile(c, &host{}, want)
	if doc.Stack != stackRunning || doc.StackInits == nil || *doc.StackInits != 1 {
		t.Errorf("stack=%q inits=%v, want running/1 while an init is alive", doc.Stack, doc.StackInits)
	}

	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))
	doc = reconcile(c, &host{}, want)
	if doc.Stack != stackStopped || doc.StackInits == nil || *doc.StackInits != 0 {
		t.Errorf("stack=%q inits=%v, want stopped/0 once they are gone", doc.Stack, doc.StackInits)
	}
}

// Nobody asked about the stack, so nothing walks /proc. The walk touches no card but
// it is still work on a box whose failure mode is not making progress, and it tells us
// nothing we do not already have: this document existing proves pod-link is up, and
// the camera and accel sections carry the other containers' liveness.
func TestNoStackDesireMeansNoProcWalk(t *testing.T) {
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"), dataDir: dir}
	// A /proc that would fail loudly if it were read at all.
	withProc(t, filepath.Join(dir, "no-such-proc"))

	doc := reconcile(c, &host{}, newWanted())
	if doc.Stack != unknown {
		t.Errorf("stack=%q, want %q when nobody asked", doc.Stack, unknown)
	}
	if doc.StackInits != nil {
		t.Errorf("stack_inits=%v, want absent when nobody looked", *doc.StackInits)
	}
	if len(doc.Errors) != 0 {
		t.Errorf("errors=%v, want none -- an unread /proc is not a failure", doc.Errors)
	}
}

// A desired value we do not understand is reported, not guessed at. Silently
// treating "off" as "stopped" would mean an operator's typo reads as success.
func TestUnknownDesiredValueIsReportedNotGuessed(t *testing.T) {
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"), dataDir: dir}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	want := newWanted()
	want.set("stack", "off")
	want.set("radio", "on")

	doc := reconcile(c, &host{}, want)
	joined := strings.Join(doc.Errors, " | ")
	if !strings.Contains(joined, `"off"`) {
		t.Errorf("no error naming the bad stack value: %s", joined)
	}
	if !strings.Contains(joined, `"on"`) {
		t.Errorf("no error naming the bad radio value: %s", joined)
	}
}

// Desired and observed travel together, so "did it take" is one read.
func TestStatusCarriesDesiredBesideObserved(t *testing.T) {
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"), dataDir: dir}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	want := newWanted()
	want.set("stack", stackStopped)
	doc := reconcile(c, &host{}, want)

	if doc.Desired["stack"] != stackStopped {
		t.Errorf("desired stack missing from the document: %+v", doc.Desired)
	}
	if doc.Node != "campod-sw" || doc.State != "ok" {
		t.Errorf("node/state wrong: %+v", doc)
	}
}

// A capture flag write that failed earlier must be healed by a later pass, or a
// transient error leaves the pod dark for a flight the broker still has intent for.
func TestCaptureIsReassertedWhenObservationDisagrees(t *testing.T) {
	dir := t.TempDir()
	flag := filepath.Join(dir, "flag")
	c := config{role: "subscriber", node: "campod-sw", flagFile: flag, dataDir: dir}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	want := newWanted()
	want.set("capture", "true") // as if the handler recorded it and the write failed

	doc := reconcile(c, &host{}, want)
	if !doc.Capture {
		t.Error("reconcile did not re-assert capture")
	}
	if !readFlagFile(flag) {
		t.Error("the flag file was not written")
	}
}

// A missing D-Bus socket must cost the radio verb and nothing else. The reason
// these are handlers in one process is fault isolation, not least privilege: a pod
// that cannot reach NetworkManager still has to fly.
func TestAMissingBusDoesNotStopTheRestOfThePass(t *testing.T) {
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"), dataDir: dir}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))
	t.Setenv("DBUS_SYSTEM_BUS_ADDRESS", "unix:path="+filepath.Join(dir, "no-such-socket"))

	want := newWanted()
	want.set("radio", radioOpen) // asked for, so the failure is worth reporting

	doc := reconcile(c, &host{}, want)
	if doc.Radio != unknown {
		t.Errorf("radio=%q, want %q with no bus", doc.Radio, unknown)
	}
	if doc.Stack != unknown {
		t.Errorf("stack=%q, want %q -- nothing asked about it here", doc.Stack, unknown)
	}
	if doc.DataFreeBytes <= 0 {
		t.Error("free space was lost along with the bus")
	}
	if len(doc.Errors) == 0 {
		t.Error("the bus failure is not reported anywhere")
	}
}

// Unasked, an unreachable bus reports `unknown` and stays quiet. Repeating it as an
// error every pass would bury the errors that mean something.
func TestAnUnreachableBusIsNotAnErrorIfNobodyAskedForTheRadio(t *testing.T) {
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"), dataDir: dir}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))
	t.Setenv("DBUS_SYSTEM_BUS_ADDRESS", "unix:path="+filepath.Join(dir, "no-such-socket"))

	doc := reconcile(c, &host{}, newWanted())
	if doc.Radio != unknown {
		t.Errorf("radio=%q, want %q", doc.Radio, unknown)
	}
	if len(doc.Errors) != 0 {
		t.Errorf("errors=%v, want none when nothing was asked for", doc.Errors)
	}
}

// No wall clock in the published document, for the same reason as the other two:
// this board has no RTC, so a wall stamp is wrong by the size of a step that has
// not happened yet and nothing says which side of it produced the number.
func TestPublishedStatusCarriesNoWallClock(t *testing.T) {
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"), dataDir: dir}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	body, err := json.Marshal(reconcile(c, &host{}, newWanted()))
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	if err := json.Unmarshal(body, &raw); err != nil {
		t.Fatal(err)
	}
	if _, ok := raw["as_of_boot_s"]; !ok {
		t.Error("no as_of_boot_s; a consumer cannot tell a stale document from a fresh one")
	}
	for k := range raw {
		if strings.Contains(k, "wall") || strings.Contains(k, "utc") || strings.Contains(k, "unix") {
			t.Errorf("published status carries a wall clock field %q", k)
		}
	}
}

// The two capture documents are forwarded verbatim. Parsing them here would mean a
// schema maintained in two languages, and a field added to capture.py would need a
// matching change in Go before it could be seen.
func TestCaptureDocumentsArePassedThroughUntouched(t *testing.T) {
	dir := t.TempDir()
	cam := filepath.Join(dir, "camera")
	body := `{"frames":412,"ready":true,"something_new":[1,2,3]}`
	if err := os.WriteFile(cam, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"),
		dataDir: dir, cameraStatusFile: cam}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	doc := reconcile(c, &host{}, newWanted())
	if strings.TrimSpace(string(doc.Camera)) != body {
		t.Errorf("camera document altered:\n got %s\nwant %s", doc.Camera, body)
	}
}

// Absent is normal (a camera-only stack, a bench run); malformed is a broken
// writer and must be said out loud rather than embedded and breaking the document.
func TestPassThroughSeparatesAbsentFromMalformed(t *testing.T) {
	dir := t.TempDir()

	raw, err := passThrough(filepath.Join(dir, "absent"))
	if raw != nil || err != nil {
		t.Errorf("absent file gave (%s, %v), want (nil, nil)", raw, err)
	}

	bad := filepath.Join(dir, "bad")
	if err := os.WriteFile(bad, []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if raw, err := passThrough(bad); err == nil {
		t.Errorf("malformed file gave (%s, nil), want an error", raw)
	}

	if raw, err := passThrough(""); raw != nil || err != nil {
		t.Errorf("disabled path gave (%s, %v), want (nil, nil)", raw, err)
	}
}

// The desired-state topic's last segment is what says which concern it is.
func TestConcernIsTheLastTopicSegment(t *testing.T) {
	node := "campod-sw"
	for topic, want := range map[string]string{
		"rekon/pod/campod-sw/desired/radio": "radio",
		"rekon/pod/campod-sw/desired/stack": "stack",
		"rekon/pod/campod-sw/status":        "",
		"rekon/pod/campod-ne/desired/radio": "", // another pod's, not ours
		"rekon/capture/intent":              "",
	} {
		if got := concern(node, topic); got != want {
			t.Errorf("concern(%q) = %q, want %q", topic, got, want)
		}
	}
}

// Seconds since boot, the clock the other two documents are on.
func TestUptimeReadsSecondsSinceBoot(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "uptime"), []byte("12345.67 98765.43\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	withProc(t, root)
	if got := uptimeS(); got != 12345.67 {
		t.Errorf("uptimeS() = %v, want 12345.67", got)
	}

	withProc(t, t.TempDir()) // no uptime file at all
	if got := uptimeS(); got != 0 {
		t.Errorf("uptimeS() with no file = %v, want 0", got)
	}
}
