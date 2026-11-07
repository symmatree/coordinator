package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// podReady is the pod's own answer to "will arming work", and the front panel and the
// fleet-control table both read it rather than assembling their own (#577). It counts only
// the sensors the pod detected, and adds chrony on the 1 Hz, which the camera cannot see.
func TestPodReady(t *testing.T) {
	cam := func(ready bool) json.RawMessage {
		return json.RawMessage(fmt.Sprintf(`{"warm":true,"sync_ready":true,"ready":%t}`, ready))
	}
	accel := func(present ...bool) json.RawMessage {
		parts := make([]string, 0, len(present))
		for i, p := range present {
			parts = append(parts, fmt.Sprintf(`{"label":"d%d","present":%t}`, i, p))
		}
		return json.RawMessage(`{"devices":[` + strings.Join(parts, ",") + `]}`)
	}
	onPPS := json.RawMessage(`{"boot_id":"b","edge":true,"pps_selected":true}`)
	got := func(c, a, clock json.RawMessage) string {
		r := podReady(c, a, clock)
		if r == nil {
			return "absent"
		}
		return fmt.Sprintf("%t", *r)
	}

	for _, tc := range []struct {
		name   string
		camera json.RawMessage
		accel  json.RawMessage
		clock  json.RawMessage
		want   string
	}{
		{"camera ready and both accels found", cam(true), accel(true, true), onPPS, "true"},
		{"camera not ready", cam(false), accel(true, true), onPPS, "false"},
		// A sensor not detected is left out: ne and nw on 2026-11-07, both ADXLs reading
		// DEVID 0x00 and their cameras ready.
		{"one accel not detected", cam(true), accel(true, false), onPPS, "true"},
		{"no accels detected", cam(true), accel(false, false), onPPS, "true"},
		{"an empty device list", cam(true), json.RawMessage(`{"devices":[]}`), onPPS, "true"},
		{"no camera detected, accels found", json.RawMessage(`{"camera":"absent","ready":false}`),
			accel(true, true), onPPS, "true"},
		// Nothing detected is nothing to arm, so no answer rather than a bare true.
		{"no camera and no accels detected", json.RawMessage(`{"camera":"absent","ready":false}`),
			accel(false, false), onPPS, "absent"},
		// A camera that is there and not ready still holds the pod.
		{"camera present and not ready", json.RawMessage(`{"camera":"present","ready":false}`),
			accel(true, true), onPPS, "false"},
		// A broken writer is not a ready pod. It is also already reported as an error.
		{"a camera document that will not parse", json.RawMessage(`{`), accel(true), onPPS, "false"},
		// A pod with no payload subsystem -- the coordinator today -- asserts nothing.
		{"neither document", nil, nil, onPPS, "absent"},
		// Camera-only and accel-only pods are each answered on what they have.
		{"camera only, ready", cam(true), nil, onPPS, "true"},
		{"accel only, found", nil, accel(true), onPPS, "true"},
		{"accel only, not detected", nil, accel(false), onPPS, "absent"},
		{"an accel document that will not parse", cam(true), json.RawMessage(`{`), onPPS, "false"},
		// The clock: arriving is not enough, and chrony on NTP alone is the camera synced
		// to its own wall clock -- nw and ne on 2026-11-07, 191 and 197 ms off sw.
		{"chrony not yet on the 1 Hz", cam(true), accel(true, true),
			json.RawMessage(`{"edge":true,"pps_selected":false}`), "false"},
		{"chrony could not be asked", cam(true), accel(true, true),
			json.RawMessage(`{"edge":true,"pps_selected":null}`), "false"},
		{"no clock document", cam(true), accel(true, true), nil, "false"},
		{"a clock document that will not parse", cam(true), accel(true, true),
			json.RawMessage(`{`), "false"},
		// The coordinator: a clock document and no payload still asserts nothing.
		{"a clock and no payload", nil, nil, onPPS, "absent"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if g := got(tc.camera, tc.accel, tc.clock); g != tc.want {
				t.Fatalf("podReady = %s, want %s", g, tc.want)
			}
		})
	}
}

// The FC-link document is passed through only when it is this boot's (#669): the file is
// on the card and its heartbeat time is seconds since the boot that wrote it, so an old
// one read against this boot's uptime would show a link that is not there.
func TestFCLinkOnlyThisBoot(t *testing.T) {
	root := t.TempDir()
	withProc(t, root)
	if err := os.MkdirAll(filepath.Join(root, "sys", "kernel", "random"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "sys", "kernel", "random", "boot_id"),
		[]byte("this-boot\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), "fc_link")
	write := func(body string) {
		if err := os.WriteFile(file, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	if got, err := thisBootDoc(""); got != nil || err != nil {
		t.Errorf("no file configured: got %s, %v; want nothing", got, err)
	}
	if got, err := thisBootDoc(file); got != nil || err != nil {
		t.Errorf("file absent: got %s, %v; want nothing", got, err)
	}
	this := `{"boot_id":"this-boot","last_heartbeat_mono_s":12.5,"heartbeats":3}`
	write(this)
	if got, err := thisBootDoc(file); err != nil || string(got) != this {
		t.Errorf("this boot's document: got %s, %v; want it verbatim", got, err)
	}
	write(`{"boot_id":"last-boot","last_heartbeat_mono_s":12.5,"heartbeats":3}`)
	if got, err := thisBootDoc(file); got != nil || err != nil {
		t.Errorf("last boot's document: got %s, %v; want nothing", got, err)
	}
	write(`{"boot_id":`)
	if _, err := thisBootDoc(file); err == nil {
		t.Error("a malformed document is a broken writer and should be reported")
	}
}
