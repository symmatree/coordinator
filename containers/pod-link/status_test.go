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
// fleet-control table both read it rather than assembling their own (#577). What it adds
// to the camera's `ready` is the accelerometers, which the camera knows nothing about.
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
	got := func(c, a json.RawMessage) string {
		r := podReady(c, a)
		if r == nil {
			return "absent"
		}
		return fmt.Sprintf("%t", *r)
	}

	for _, tc := range []struct {
		name   string
		camera json.RawMessage
		accel  json.RawMessage
		want   string
	}{
		{"camera ready and both accels found", cam(true), accel(true, true), "true"},
		{"camera not ready", cam(false), accel(true, true), "false"},
		// The campod-se case on 2026-10-19: camera fine, one accel on a miswired CE1.
		{"one accel missing", cam(true), accel(true, false), "false"},
		{"no accels found at all", cam(true), accel(false, false), "false"},
		// An accel reader that started but listed nothing is not evidence of hardware.
		{"an empty device list", cam(true), json.RawMessage(`{"devices":[]}`), "false"},
		// A broken writer is not a ready pod. It is also already reported as an error.
		{"a camera document that will not parse", json.RawMessage(`{`), accel(true), "false"},
		// A pod with no payload subsystem -- the coordinator today -- asserts nothing.
		{"neither document", nil, nil, "absent"},
		// Camera-only and accel-only pods are each answered on what they have.
		{"camera only, ready", cam(true), nil, "true"},
		{"accel only, found", nil, accel(true), "true"},
		{"accel only, missing", nil, accel(false), "false"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if g := got(tc.camera, tc.accel); g != tc.want {
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

	if got, err := fcLink(""); got != nil || err != nil {
		t.Errorf("no file configured: got %s, %v; want nothing", got, err)
	}
	if got, err := fcLink(file); got != nil || err != nil {
		t.Errorf("file absent: got %s, %v; want nothing", got, err)
	}
	this := `{"boot_id":"this-boot","last_heartbeat_mono_s":12.5,"heartbeats":3}`
	write(this)
	if got, err := fcLink(file); err != nil || string(got) != this {
		t.Errorf("this boot's document: got %s, %v; want it verbatim", got, err)
	}
	write(`{"boot_id":"last-boot","last_heartbeat_mono_s":12.5,"heartbeats":3}`)
	if got, err := fcLink(file); got != nil || err != nil {
		t.Errorf("last boot's document: got %s, %v; want nothing", got, err)
	}
	write(`{"boot_id":`)
	if _, err := fcLink(file); err == nil {
		t.Error("a malformed document is a broken writer and should be reported")
	}
}
