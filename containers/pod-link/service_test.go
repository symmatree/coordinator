package main

import (
	"fmt"
	"net"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// fakeDocker answers the four calls service.go makes, over a real unix socket, so
// the transport is exercised rather than stubbed.
type fakeDocker struct {
	mu      sync.Mutex
	running map[string]bool
	calls   []string
	socket  string
}

func newFakeDocker(t *testing.T, running map[string]bool) *fakeDocker {
	t.Helper()
	// Socket paths are capped near 108 bytes, and t.TempDir() under a long scratch
	// path can exceed it -- hence the short name rather than a descriptive one.
	socket := filepath.Join(t.TempDir(), "d.sock")
	f := &fakeDocker{running: running, socket: socket}

	l, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatalf("listening on %s: %v", socket, err)
	}
	srv := &http.Server{Handler: f}
	go func() { _ = srv.Serve(l) }()
	t.Cleanup(func() { _ = srv.Close() })
	return f
}

func (f *fakeDocker) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, r.Method+" "+r.URL.Path)

	parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	if len(parts) < 3 || parts[0] != "containers" {
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	name, verb := parts[1], parts[2]
	state, known := f.running[name]
	if !known {
		w.WriteHeader(http.StatusNotFound)
		return
	}
	switch verb {
	case "json":
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"State":{"Running":%t}}`, state)
	case "stop", "start":
		wantRunning := verb == "start"
		if state == wantRunning {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		f.running[name] = wantRunning
		w.WriteHeader(http.StatusNoContent)
	default:
		w.WriteHeader(http.StatusBadRequest)
	}
}

func (f *fakeDocker) isRunning(name string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.running[name]
}

// The case this granularity exists for: the FC log pull needs coordinator-mavlink to
// release /dev/ttyAMA0, and the broker carrying the instruction is in the same stack,
// so stopping the whole stack cannot express it.
func TestStoppingOneServiceLeavesTheOthersAlone(t *testing.T) {
	f := newFakeDocker(t, map[string]bool{
		"coordinator_mavlink":    true,
		"coordinator_mochi_mqtt": true,
	})
	dir := t.TempDir()
	c := config{role: "publisher", node: "coordinator", armFile: filepath.Join(dir, "arm"),
		dataDir: dir, dockerSocket: f.socket}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	want := newWanted()
	want.set("service/coordinator_mavlink", stackStopped)

	doc := reconcile(c, &host{}, newDockerClient(f.socket), want)
	if len(doc.Errors) != 0 {
		t.Fatalf("unexpected errors: %v", doc.Errors)
	}
	if f.isRunning("coordinator_mavlink") {
		t.Error("the fc-talker is still running")
	}
	if !f.isRunning("coordinator_mochi_mqtt") {
		t.Error("the broker was stopped too, which would cut the instruction's own path")
	}

	// And the state observed on the NEXT pass is what says it took.
	doc = reconcile(c, &host{}, newDockerClient(f.socket), want)
	if doc.Services["coordinator_mavlink"] != stackStopped {
		t.Errorf("services=%v, want coordinator_mavlink stopped", doc.Services)
	}
}

// Back up again without a reboot, which is the half that makes the log-pull workflow
// usable rather than a one-way trip.
func TestAServiceCanBeStartedAgain(t *testing.T) {
	f := newFakeDocker(t, map[string]bool{"coordinator_mavlink": false})
	dir := t.TempDir()
	c := config{role: "publisher", node: "coordinator", armFile: filepath.Join(dir, "arm"),
		dataDir: dir, dockerSocket: f.socket}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	want := newWanted()
	want.set("service/coordinator_mavlink", stackRunning)

	doc := reconcile(c, &host{}, newDockerClient(f.socket), want)
	if len(doc.Errors) != 0 {
		t.Fatalf("unexpected errors: %v", doc.Errors)
	}
	if !f.isRunning("coordinator_mavlink") {
		t.Error("the fc-talker was not started")
	}
}

// Already in the desired state is success, not an error: the daemon answers 304 and
// a reconciler has nothing to do.
func TestAlreadyInTheDesiredStateIsNotAnError(t *testing.T) {
	f := newFakeDocker(t, map[string]bool{"campod_camera": true})
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"),
		dataDir: dir, dockerSocket: f.socket}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	want := newWanted()
	want.set("service/campod_camera", stackRunning)

	for i := 0; i < 3; i++ {
		if doc := reconcile(c, &host{}, newDockerClient(f.socket), want); len(doc.Errors) != 0 {
			t.Fatalf("pass %d reported %v", i, doc.Errors)
		}
	}
	if !f.isRunning("campod_camera") {
		t.Error("a no-op reconcile changed the state")
	}
}

// A name the daemon does not know is reported as what it is. There is no allowlist
// here on purpose -- the 404 answers the question in the step we were taking anyway.
func TestAnUnknownContainerNameIsReported(t *testing.T) {
	f := newFakeDocker(t, map[string]bool{"campod_camera": true})
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"),
		dataDir: dir, dockerSocket: f.socket}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	want := newWanted()
	want.set("service/typo_mavlink", stackStopped)

	doc := reconcile(c, &host{}, newDockerClient(f.socket), want)
	if len(doc.Errors) == 0 || !strings.Contains(strings.Join(doc.Errors, " "), "typo_mavlink") {
		t.Errorf("errors=%v, want one naming typo_mavlink", doc.Errors)
	}
}

// No socket mounted costs the service concern and nothing else.
func TestNoDockerSocketIsReportedNotFatal(t *testing.T) {
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"),
		dataDir: dir}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	want := newWanted()
	want.set("service/campod_camera", stackStopped)

	doc := reconcile(c, &host{}, nil, want)
	if len(doc.Errors) == 0 {
		t.Error("a missing docker socket was not reported")
	}
	if doc.Stack == unknown {
		t.Error("the stack observation was lost with it")
	}
	if doc.DataFreeBytes <= 0 {
		t.Error("free space was lost with it")
	}
}

// Only containers somebody asked about are inspected. Listing every container every
// pass would be an inspect per container for answers nobody wanted.
func TestOnlyRequestedServicesAreInspected(t *testing.T) {
	f := newFakeDocker(t, map[string]bool{"a": true, "b": true, "c": true})
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw", flagFile: filepath.Join(dir, "flag"),
		dataDir: dir, dockerSocket: f.socket}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	want := newWanted()
	want.set("service/b", stackRunning)
	reconcile(c, &host{}, newDockerClient(f.socket), want)

	f.mu.Lock()
	defer f.mu.Unlock()
	for _, call := range f.calls {
		if strings.Contains(call, "/containers/a/") || strings.Contains(call, "/containers/c/") {
			t.Errorf("inspected a container nobody asked about: %s", call)
		}
	}
	if len(f.calls) == 0 {
		t.Error("no calls were made at all")
	}
}
