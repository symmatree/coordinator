package main

import (
	"path/filepath"
	"strings"
	"testing"
)

// A bare name, a .service name and a .container name are all things a reader of the
// stack directory might reasonably publish, and the quadlet file for
// coordinator-mavlink.container generates coordinator-mavlink.service.
func TestUnitNameAcceptsEveryFormSomebodyWouldType(t *testing.T) {
	for in, want := range map[string]string{
		"coordinator-mavlink":           "coordinator-mavlink.service",
		"coordinator-mavlink.service":   "coordinator-mavlink.service",
		"coordinator-mavlink.container": "coordinator-mavlink.service",
		"  campod-camera  ":             "campod-camera.service",
	} {
		if got := unitName(in); got != want {
			t.Errorf("unitName(%q) = %q, want %q", in, got, want)
		}
	}
}

// systemd's vocabulary is wider than this bus's two words, and the transitional states
// must report as NOT yet at their destination -- a unit still coming up is not running,
// so desired and actual should still disagree rather than reading as early success.
func TestTransitionalStatesDoNotReadAsSuccess(t *testing.T) {
	for state, want := range map[string]string{
		"active":       stackRunning,
		"inactive":     stackStopped,
		"failed":       stackStopped,
		"activating":   stackStopped,
		"deactivating": stackStopped,
		"not-found":    stackStopped,
	} {
		if got := activeFromState(state); got != want {
			t.Errorf("activeFromState(%q) = %q, want %q", state, got, want)
		}
	}
}

// With no bus reachable the service concern is lost and nothing else is. The reason
// these are handlers in one process is fault isolation, not least privilege: a pod that
// cannot reach systemd still has to fly.
func TestNoBusCostsTheServiceConcernAndNothingElse(t *testing.T) {
	dir := t.TempDir()
	c := config{role: "subscriber", node: "campod-sw",
		flagFile: filepath.Join(dir, "flag"), dataDir: dir}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))
	t.Setenv("DBUS_SYSTEM_BUS_ADDRESS", "unix:path="+filepath.Join(dir, "no-such-socket"))

	want := newWanted()
	want.set("service/campod-camera", stackStopped)

	doc := reconcile(c, &host{}, want)
	if len(doc.Errors) == 0 {
		t.Error("an unreachable bus was not reported")
	}
	if doc.DataFreeBytes <= 0 {
		t.Error("free space was lost along with the bus")
	}
	if doc.Camera != nil || doc.Accel != nil {
		t.Error("pass-through sections appeared from nowhere")
	}
}

// A desired value we do not understand is reported, not guessed at, and the unit is
// never touched on the strength of a typo.
func TestUnknownServiceValueIsReportedNotGuessed(t *testing.T) {
	dir := t.TempDir()
	c := config{role: "publisher", node: "coordinator",
		armFile: filepath.Join(dir, "arm"), dataDir: dir}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	want := newWanted()
	want.set("service/coordinator-mavlink", "off")

	doc := reconcile(c, &host{}, want)
	if !strings.Contains(strings.Join(doc.Errors, " "), `"off"`) {
		t.Errorf("errors=%v, want one naming the bad value", doc.Errors)
	}
	if _, reported := doc.Services["coordinator-mavlink"]; reported {
		t.Error("a unit with an invalid desire was still queried")
	}
}
