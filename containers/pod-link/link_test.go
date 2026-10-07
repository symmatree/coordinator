package main

// End-to-end over a real broker, with no hardware and no network.
//
// The MQTT round trip is the one part of this that must work in flight and was
// the one part nothing exercised: the unit tests cover the flag file, the
// reconciler and the topic arithmetic, all of which could be perfect while a
// publisher and a subscriber failed to agree on a wire. Arm to first frame runs
// through this path and nothing else.
//
// THE BROKER IS THE ONE WE DEPLOY. mochi-mqtt is importable as a library, and the
// version here tracks the 2.7 image in stacks/coordinator/compose.yaml -- so this
// is not a stand-in whose behaviour has to be assumed comparable. Retained
// delivery and the last-will are broker behaviour, which is exactly why asserting
// them against a mock would prove nothing.
//
// Runs at image build time, so a wire-level regression fails the build rather than
// a flight.

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
	mqttserver "github.com/mochi-mqtt/server/v2"
	"github.com/mochi-mqtt/server/v2/hooks/auth"
	"github.com/mochi-mqtt/server/v2/listeners"
)

// freePort asks the kernel for one rather than picking a number, so parallel runs
// on a build machine do not collide.
func freePort(t *testing.T) int {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port
}

// broker starts an in-process mochi-mqtt and returns its URL.
func broker(t *testing.T) string {
	t.Helper()
	port := freePort(t)
	s := mqttserver.New(&mqttserver.Options{InlineClient: true})
	if err := s.AddHook(new(auth.AllowHook), nil); err != nil {
		t.Fatal(err)
	}
	addr := fmt.Sprintf("127.0.0.1:%d", port)
	if err := s.AddListener(listeners.NewTCP(listeners.Config{ID: "t", Address: addr})); err != nil {
		t.Fatal(err)
	}
	go func() {
		if err := s.Serve(); err != nil {
			t.Logf("broker stopped: %v", err)
		}
	}()
	t.Cleanup(func() { _ = s.Close() })
	return "tcp://" + addr
}

// eventually polls until cond holds, so the test waits on the thing it cares
// about instead of on a sleep long enough to be flaky under load.
func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

// The whole path: the router's arm file -> publisher -> broker -> subscriber ->
// the capture flag capture.py reads. This is arm to capture, and it is the only
// reason either role exists.
func TestArmFileReachesTheCaptureFlagThroughTheBroker(t *testing.T) {
	url := broker(t)
	dir := t.TempDir()
	armFile := filepath.Join(dir, "fc_armed")
	flagFile := filepath.Join(dir, "campod_capture")

	if err := os.WriteFile(armFile, []byte("0\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	pubStop, subStop := make(chan struct{}), make(chan struct{})
	pub := config{role: "publisher", broker: url, node: "coordinator",
		armFile: armFile, dataDir: dir,
		period: 50 * time.Millisecond, statusPeriod: 50 * time.Millisecond}
	sub := config{role: "subscriber", broker: url, node: "campod-sw",
		flagFile: flagFile, dataDir: dir,
		period: 50 * time.Millisecond, statusPeriod: 50 * time.Millisecond}

	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	go func() {
		if err := run(pub, pubStop); err != nil {
			t.Logf("publisher: %v", err)
		}
	}()
	go func() {
		if err := run(sub, subStop); err != nil {
			t.Logf("subscriber: %v", err)
		}
	}()
	t.Cleanup(func() { close(pubStop); close(subStop) })

	eventually(t, "disarmed intent to reach the flag", func() bool {
		_, err := os.Stat(flagFile)
		return err == nil && !readFlagFile(flagFile)
	})

	// Arm. The publisher polls the file; the subscriber writes the flag in its
	// handler rather than on its own tick, because this latency is in the
	// arm-to-takeoff window.
	if err := os.WriteFile(armFile, []byte("1\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	eventually(t, "armed intent to open the gate", func() bool { return readFlagFile(flagFile) })

	// Disarm closes it again, which is what keeps a bench pod from filling a card.
	if err := os.WriteFile(armFile, []byte("0\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	eventually(t, "disarm to close the gate", func() bool { return !readFlagFile(flagFile) })
}

// A pod that connects after the vehicle armed must not sit idle until the next
// change. Retained delivery is what makes that true, and it is broker behaviour,
// so this is the assertion that could not be made against a mock.
func TestALateSubscriberGetsTheCurrentIntent(t *testing.T) {
	url := broker(t)
	dir := t.TempDir()
	armFile := filepath.Join(dir, "fc_armed")
	flagFile := filepath.Join(dir, "campod_capture")
	if err := os.WriteFile(armFile, []byte("1\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	pubStop := make(chan struct{})
	go func() {
		_ = run(config{role: "publisher", broker: url, node: "coordinator",
			armFile: armFile, dataDir: dir,
			period: 50 * time.Millisecond, statusPeriod: 50 * time.Millisecond}, pubStop)
	}()
	t.Cleanup(func() { close(pubStop) })

	// Let the armed intent be published and retained before anything subscribes.
	time.Sleep(300 * time.Millisecond)

	subStop := make(chan struct{})
	go func() {
		_ = run(config{role: "subscriber", broker: url, node: "campod-ne",
			flagFile: flagFile, dataDir: dir,
			period: 50 * time.Millisecond, statusPeriod: 50 * time.Millisecond}, subStop)
	}()
	t.Cleanup(func() { close(subStop) })

	eventually(t, "a late pod to pick up the retained intent", func() bool {
		return readFlagFile(flagFile)
	})
}

// Desired state travels per pod, and a pod must ignore another pod's instructions.
// One shared topic would make the last publisher the only one anybody could see.
func TestDesiredStateIsPerPodAndOtherPodsAreIgnored(t *testing.T) {
	url := broker(t)
	dir := t.TempDir()
	withProc(t, fakeProc(t, map[int]string{2: "sshd"}))

	stop := make(chan struct{})
	sub := config{role: "subscriber", broker: url, node: "campod-sw",
		flagFile: filepath.Join(dir, "flag"), dataDir: dir,
		period: 50 * time.Millisecond, statusPeriod: 50 * time.Millisecond}
	go func() {
		_ = run(sub, stop)
	}()
	t.Cleanup(func() { close(stop) })

	cl := clientFor(config{role: "test", broker: url, node: "tester"}, nil)
	if tok := cl.Connect(); tok.Wait() && tok.Error() != nil {
		t.Fatal(tok.Error())
	}
	t.Cleanup(func() { cl.Disconnect(100) })

	// Addressed to a different pod.
	publish(cl, desiredPrefix("campod-ne")+"stack", stackStopped)
	// Addressed to this one.
	publish(cl, desiredPrefix("campod-sw")+"radio", radioOpen)

	var last podStatus
	seen := make(chan podStatus, 32)
	tok := cl.Subscribe(topicFor("campod-sw"), 1, func(_ mqtt.Client, m mqtt.Message) {
		var s podStatus
		if json.Unmarshal(m.Payload(), &s) == nil {
			select {
			case seen <- s:
			default:
			}
		}
	})
	if !tok.WaitTimeout(5*time.Second) || tok.Error() != nil {
		t.Fatal(tok.Error())
	}

	deadline := time.After(10 * time.Second)
	for {
		select {
		case s := <-seen:
			last = s
			if last.Desired["radio"] == radioOpen {
				if _, ours := last.Desired["stack"]; ours {
					t.Fatalf("this pod took another pod's stack instruction: %+v", last.Desired)
				}
				return
			}
		case <-deadline:
			t.Fatalf("never saw our own desired radio; last status %+v", last)
		}
	}
}
