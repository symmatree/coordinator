package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// The payload that was mis-read on hardware, kept verbatim as the first case.
//
// campod-se, 2026-10-07: published by hand to `rekon/capture/intent` as standard JSON
// with a space after the colon. The old substring match, `Contains(payload,
// "capture":true)`, read it as FALSE, wrote the gate closed, logged nothing because the
// applied value was already false, and reported `capture:false` in its status as though
// that were what had been asked. It took comparing it against a topic that *did* work
// to find, from outside the process.
func TestIntentFromTheHardwareIncident(t *testing.T) {
	const payload = `{"capture": true, "reason": "bench-os-driver"}`
	got, err := parseIntent(payload)
	if err != nil {
		t.Fatalf("parseIntent(%s) errored: %v", payload, err)
	}
	if !got {
		t.Errorf("parseIntent(%s) = false, want true -- this is the regression", payload)
	}
}

func TestIntentFormsThatMustAllWork(t *testing.T) {
	for payload, want := range map[string]bool{
		// What our own publisher emits.
		`{"capture":true,"reason":"armed"}`:     true,
		`{"capture":false,"reason":"disarmed"}`: false,
		// Standard JSON spacing, which a person or any other producer writes.
		`{"capture": true, "reason": "bench"}`:   true,
		`{"capture": false}`:                     false,
		`{ "reason": "armed", "capture": true }`: true,
		// Newline-terminated, as a shell heredoc or a file would give.
		"{\"capture\": true}\n": true,
		// Bare words, because the bench case that found the bug was mosquitto_pub and
		// the desired-state topics already take bare words.
		"true":   true,
		"1":      true,
		"on":     true,
		"yes":    true,
		"TRUE":   true,
		" true ": true,
		"false":  false,
		"0":      false,
		"off":    false,
		"no":     false,
	} {
		got, err := parseIntent(payload)
		if err != nil {
			t.Errorf("parseIntent(%q) errored: %v", payload, err)
			continue
		}
		if got != want {
			t.Errorf("parseIntent(%q) = %t, want %t", payload, got, want)
		}
	}
}

// Unparseable must fail CLOSED and SAY SO. Closed because a gate whose failure mode is
// "capture everything forever" is the behaviour #439 removed; say so because a gate
// whose failure mode is silence is what cost an afternoon on hardware.
func TestUnparseableIntentFailsClosedAndLoudly(t *testing.T) {
	for _, payload := range []string{
		``,
		`{`,
		`not json at all`,
		`{"reason":"armed"}`, // no capture key
		`{"capture":"true"}`, // a string, not a boolean
		`{"capture":1}`,      // a number, not a boolean
	} {
		got, err := parseIntent(payload)
		if got {
			t.Errorf("parseIntent(%q) = true; unparseable must fail closed", payload)
		}
		if err == nil {
			t.Errorf("parseIntent(%q) returned no error; the failure must be visible", payload)
		}
	}
}

// The status document carries the payload AS RECEIVED, so a mismatch between what a
// publisher meant and what the pod read is visible off the bus. Reporting only the
// pod's own interpretation is what made the original bug undiagnosable remotely.
func TestStatusCarriesTheIntentVerbatim(t *testing.T) {
	seen := &seenIntent{}
	const payload = `{"capture": true, "reason": "bench"}`
	seen.set(payload, nil)

	body, at, errText := seen.snapshot()
	if body != payload {
		t.Errorf("payload = %q, want it verbatim: %q", body, payload)
	}
	if at <= 0 {
		t.Error("no arrival time recorded")
	}
	if errText != "" {
		t.Errorf("error text = %q, want empty", errText)
	}

	_, err := parseIntent(`{"reason":"armed"}`)
	seen.set(`{"reason":"armed"}`, err)
	if _, _, errText = seen.snapshot(); errText == "" {
		t.Error("a parse failure is not reported in the status")
	}
}

// End to end over a real broker with the payload that failed on hardware, so the fix is
// exercised through the delivery path rather than only at the parser.
func TestSpacedJSONIntentOpensTheGateThroughTheBroker(t *testing.T) {
	url := broker(t)
	dir := t.TempDir()
	flagFile := filepath.Join(dir, "campod_capture")
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	stop := make(chan struct{})
	go func() {
		_ = run(config{role: "subscriber", broker: url, node: "campod-se",
			flagFile: flagFile, dataDir: dir,
			period: 50 * time.Millisecond, statusPeriod: 50 * time.Millisecond}, stop)
	}()
	t.Cleanup(func() { close(stop) })

	cl := clientFor(config{role: "test", broker: url, node: "tester"}, nil)
	if tok := cl.Connect(); tok.Wait() && tok.Error() != nil {
		t.Fatal(tok.Error())
	}
	t.Cleanup(func() { cl.Disconnect(100) })

	publish(cl, topicIntent, `{"capture": false, "reason": "disarmed"}`)
	eventually(t, "the gate to be closed", func() bool {
		_, err := os.Stat(flagFile)
		return err == nil && !readFlagFile(flagFile)
	})

	// The live publish that did not take effect on hardware.
	publish(cl, topicIntent, `{"capture": true, "reason": "bench-os-driver"}`)
	eventually(t, "spaced JSON to open the gate", func() bool { return readFlagFile(flagFile) })
}
