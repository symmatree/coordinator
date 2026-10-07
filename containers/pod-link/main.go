// pod-link carries capture intent from the coordinator to the campods.
//
// One binary, two roles, selected by POD_LINK_ROLE:
//
//	publisher (coordinator)  watches the arm file the MAVLink router already
//	                         writes and publishes it as capture intent
//	subscriber (campod)      reconciles this pod to the states it is told to be
//	                         in -- capture, stack, radio -- and publishes what it
//	                         actually is, including the two capture processes'
//	                         own status documents
//
// THE COORDINATOR IS NOT MANAGED THROUGH THIS. Only the subscriber reconciles. The
// broker runs on the coordinator, so a bus instruction to stop its stack would
// take the bus down with it; and ssh to a Pi 4B is cheap in a way ssh to a Zero is
// not, which is the problem this exists to solve.
//
// WHY A RESIDENT PROCESS. On a campod, invoking a binary is the expensive
// operation: the docker CLI faults in ~71 MiB of mapped text on a 417 MiB board
// with no swap, evicting page cache that then refaults off a ~20 MiB/s card.
// Measured, `docker ps` 2.1-22.0 s with capture running against 136-223 ms
// without. A process already in memory that receives a packet and writes a file
// costs none of that, which is the whole reason this is not an ssh command.
//
// WHY A FILE AND NOT A SIGNAL. The state being carried is "keep running but stop
// retrieving", which no signal expresses. The camera must stay started: its
// buffers are 99.6 MiB of a 128 MiB CMA reservation, so a second set cannot be
// allocated even transiently and anything that stops the camera re-pays a ~90 s
// import. capture.py reads the flag once per tick; this writes it.
//
// WHY ONE PROCESS WITH TWO ROLES. Footprint. A campod has one of these, not one
// per concern, and the roles share a connection, a reconnect policy and a status
// topic. New concerns become new handlers inside it rather than new processes.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

// buildSHA is stamped at link time, as campod-accel does, so a device can say
// which build is carrying its intent.
var buildSHA = ""

const (
	// Capture intent, retained. RETAINED IS LOAD-BEARING: a pod whose connection
	// blips mid-flight gets the current intent on reconnect rather than sitting
	// idle until the next change. The broker holds the last value; a subscriber
	// that arrives late is not a subscriber that missed the flight.
	topicIntent = "rekon/capture/intent"
	// Per-pod status, retained, so the coordinator sees every pod's last word
	// without waiting for the next heartbeat.
	topicStatusFmt = "rekon/pod/%s/status"
	// Per-pod desired state, retained, one topic per concern. Subscribed as a
	// wildcard and dispatched on the last segment, so a new concern is a new case
	// rather than a new subscription.
	//
	// PER POD, where intent is fleet-wide, and the asymmetry has a reason: capture
	// intent is a property of the VEHICLE (it armed, so every pod should be
	// collecting), while stack and radio are bench operations on one device at a
	// time. Four publishes is the cost of stopping four pods, which is nothing.
	topicDesiredFmt = "rekon/pod/%s/desired/"
	// Reboot. NOT RETAINED, and that is load-bearing -- see reconcile.go. A
	// retained reboot request is a boot loop.
	topicRebootFmt = "rekon/pod/%s/reboot"
)

// topicFor is per node, deliberately. One shared status topic would mean the last
// pod to publish is the only one the coordinator can see, and "which pods are
// ready" is the question this exists to answer.
func topicFor(node string) string { return fmt.Sprintf(topicStatusFmt, node) }

// desiredPrefix is the per-pod desired-state prefix; concern() recovers the last
// segment of a received topic.
func desiredPrefix(node string) string { return fmt.Sprintf(topicDesiredFmt, node) }
func rebootTopic(node string) string   { return fmt.Sprintf(topicRebootFmt, node) }

// concern is the part of a desired-state topic that says what is being asked for.
// Returns "" for a topic that is not under the prefix at all.
func concern(node, topic string) string {
	prefix := desiredPrefix(node)
	if !strings.HasPrefix(topic, prefix) {
		return ""
	}
	return strings.TrimPrefix(topic, prefix)
}

type config struct {
	role     string
	broker   string
	node     string
	armFile  string // publisher: what to watch
	flagFile string // subscriber: what to write
	period   time.Duration
	// Where the two capture processes publish their own state, for pass-through.
	cameraStatusFile string
	accelStatusFile  string
	// Whose free space is reported. The captures mount, read-only.
	dataDir string
	// How often the subscriber reconciles and republishes. Separate from `period`
	// and slower by default: intent has to be fast because it sets the arm-to-first
	// -frame latency, while nothing is waiting on a status refresh. Each pass is a
	// /proc walk, two small tmpfs reads, a statfs and a D-Bus property read -- all
	// in-memory, no forks, no page cache -- but the board this runs on is one where
	// that distinction is the difference between 136 ms and 22 s.
	statusPeriod time.Duration
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func loadConfig() (config, error) {
	c := config{
		role:     env("POD_LINK_ROLE", ""),
		broker:   env("POD_LINK_BROKER", "tcp://10.55.0.1:1883"),
		node:     nodeName(),
		armFile:  env("POD_LINK_ARM_FILE", "/tmp/fc_armed"),
		flagFile: env("POD_LINK_FLAG_FILE", "/tmp/campod_capture"),

		cameraStatusFile: env("POD_LINK_CAMERA_STATUS_FILE", "/tmp/campod_camera_status"),
		accelStatusFile:  env("POD_LINK_ACCEL_STATUS_FILE", "/tmp/campod_accel_status"),
		dataDir:          env("POD_LINK_DATA_DIR", "/captures"),
	}
	secs, err := strconv.Atoi(env("POD_LINK_PERIOD_S", "2"))
	if err != nil || secs < 1 {
		return c, fmt.Errorf("POD_LINK_PERIOD_S=%q is not a positive integer", os.Getenv("POD_LINK_PERIOD_S"))
	}
	c.period = time.Duration(secs) * time.Second
	statusSecs, err := strconv.Atoi(env("POD_LINK_STATUS_PERIOD_S", "5"))
	if err != nil || statusSecs < 1 {
		return c, fmt.Errorf("POD_LINK_STATUS_PERIOD_S=%q is not a positive integer",
			os.Getenv("POD_LINK_STATUS_PERIOD_S"))
	}
	c.statusPeriod = time.Duration(statusSecs) * time.Second
	if c.role != "publisher" && c.role != "subscriber" {
		return c, fmt.Errorf("POD_LINK_ROLE must be publisher or subscriber, got %q", c.role)
	}
	return c, nil
}

// nodeName is the HOST's name, read from the mount the stack provides. Inside a
// container os.Hostname() is the container id, which changes on every recreate --
// the same reason campod-accel and capture.py read this file (#272).
func nodeName() string {
	if b, err := os.ReadFile("/etc/host-hostname"); err == nil {
		if n := strings.TrimSpace(string(b)); n != "" {
			return n
		}
	}
	h, _ := os.Hostname()
	return h
}

// readFlagFile reports whether a one-line boolean file says yes. Absent or
// unreadable is NO, matching capture.py: a control whose failure mode is "capture
// forever" is the behaviour being removed.
func readFlagFile(path string) bool {
	b, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	switch strings.TrimSpace(string(b)) {
	case "1", "true", "on", "yes":
		return true
	}
	return false
}

// writeFlagFile writes atomically -- temp then rename -- so capture.py's once-per-
// tick read can never see a half-written value. Same discipline as the arm file
// the router writes.
func writeFlagFile(path string, on bool) error {
	body := "0\n"
	if on {
		body = "1\n"
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(body), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func clientFor(c config, onConnect mqtt.OnConnectHandler) mqtt.Client {
	status := topicFor(c.node)
	opts := mqtt.NewClientOptions().
		AddBroker(c.broker).
		SetClientID(fmt.Sprintf("pod-link-%s-%s", c.role, c.node)).
		SetCleanSession(false).
		// Reconnect forever. A pod that loses the broker mid-flight is a fault,
		// not a case to design around -- but it must come back by itself when the
		// fault clears, because nothing on the vehicle is going to restart it.
		SetAutoReconnect(true).
		SetConnectRetry(true).
		SetConnectRetryInterval(5*time.Second).
		SetMaxReconnectInterval(30*time.Second).
		SetKeepAlive(10*time.Second).
		// A last will, retained, so a pod that drops off says so in the same place
		// it says everything else. Absence and silence look identical otherwise.
		SetWill(status, `{"state":"gone"}`, 1, true).
		SetOnConnectHandler(onConnect)
	opts.SetConnectionLostHandler(func(_ mqtt.Client, err error) {
		fmt.Fprintf(os.Stderr, "pod-link: connection lost: %v\n", err)
	})
	return mqtt.NewClient(opts)
}

// publish with QoS 1 and retain. QoS 1 because losing a capture command is losing
// the flight; at-least-once with an idempotent payload is the right trade.
func publish(cl mqtt.Client, topic, payload string) {
	t := cl.Publish(topic, 1, true, payload)
	if !t.WaitTimeout(5*time.Second) || t.Error() != nil {
		fmt.Fprintf(os.Stderr, "pod-link: publish %s failed: %v\n", topic, t.Error())
	}
}

// publishStatus is QoS 0, retained, and NOT waited on.
//
// Different trade from intent, on purpose. A status document is a snapshot that is
// replaced every few seconds, so one lost in flight costs nothing -- the next
// carries the same answer, and the retained value a late subscriber reads is
// whichever arrived last either way. Waiting on it would be worse than useless: a
// 5 s acknowledgement wait inside a 5 s tick is a loop that can fall behind the
// thing it is reporting.
func publishStatus(cl mqtt.Client, topic, payload string) {
	cl.Publish(topic, 0, true, payload)
}

// wanted is what this pod has been told to be, per concern.
//
// Guarded because paho delivers messages on its own goroutine while the reconcile
// tick reads them. Small and explicit beats clever: there are two scalars here and
// they change when a person publishes.
type wanted struct {
	mu sync.Mutex
	m  map[string]string
}

func newWanted() *wanted { return &wanted{m: map[string]string{}} }

func (w *wanted) set(concern, value string) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.m[concern] = value
}

// snapshot copies the map so the reconcile pass works from a stable view and
// never holds the lock across a syscall.
func (w *wanted) snapshot() map[string]string {
	w.mu.Lock()
	defer w.mu.Unlock()
	out := make(map[string]string, len(w.m))
	for k, v := range w.m {
		out[k] = v
	}
	return out
}

// runPublisher watches the arm file and republishes it when it changes.
//
// POLLED, not inotify. The file is written by a different container through a
// shared mount, where inotify's guarantees get thin, and a 2 s stat on a Pi 4B is
// free. Capture starting up to 2 s after arm is inside the arm-to-takeoff window.
func runPublisher(c config, stop <-chan struct{}) error {
	// GUARDED, and this is not pedantry. `last` is written by the on-connect
	// handler, which runs on paho's goroutine, and read and written by the loop
	// below. Unsynchronised, the reconnect's reset can be lost -- the loop
	// overwrites it with the payload it just published -- and then no republish
	// happens.
	//
	// The consequence is specific: the broker has no storage hook, so a broker
	// restart loses the retained intent, and this forced republish is the only thing
	// that puts it back. Losing it leaves every pod holding `disarmed` through a
	// flight. Found by link_test.go under -race, which is what that test is for.
	var mu sync.Mutex
	var last string
	cl := clientFor(c, func(cl mqtt.Client) {
		fmt.Printf("pod-link: connected to %s as publisher\n", c.broker)
		mu.Lock()
		last = "" // force a republish, so a reconnect re-establishes the retained value
		mu.Unlock()
	})
	if t := cl.Connect(); t.Wait() && t.Error() != nil {
		return t.Error()
	}

	tick := time.NewTicker(c.period)
	defer tick.Stop()

	for {
		armed := readFlagFile(c.armFile)
		payload := `{"capture":false,"reason":"disarmed"}`
		if armed {
			payload = `{"capture":true,"reason":"armed"}`
		}
		mu.Lock()
		changed := payload != last
		if changed {
			last = payload
		}
		mu.Unlock()
		if changed {
			publish(cl, topicIntent, payload)
			fmt.Printf("pod-link: intent -> %s\n", payload)
		}
		select {
		case <-stop:
			fmt.Println("pod-link: stop requested")
			cl.Disconnect(250)
			return nil
		case <-tick.C:
		}
	}
}

// runSubscriber reconciles this pod to what it is told to be, and says what it is.
//
// It writes the capture flag on EVERY intent message rather than on change: the
// file is the authority capture.py reads, and a retained message arriving after a
// reconnect must restore it even if this process believes nothing changed.
func runSubscriber(c config, stop <-chan struct{}) error {
	status := topicFor(c.node)
	want := newWanted()
	h := &host{}
	var applied string

	cl := clientFor(c, func(cl mqtt.Client) {
		fmt.Printf("pod-link: connected to %s as subscriber\n", c.broker)

		t := cl.Subscribe(topicIntent, 1, func(_ mqtt.Client, m mqtt.Message) {
			capture := strings.Contains(string(m.Payload()), `"capture":true`)
			// Recorded like every other desire, so the status document shows what was
			// asked for next to what is true and the tick can heal a failed write.
			want.set("capture", fmt.Sprintf("%t", capture))
			// But ALSO written right here, which is the one concern not left to the
			// tick. The operator arms and launches within a few seconds; the publisher
			// already spends up to its 2 s poll noticing the arm, and adding a status
			// period on top would put the first frame outside the arm-to-takeoff
			// window. Stack and radio are bench operations with no such budget.
			if err := writeFlagFile(c.flagFile, capture); err != nil {
				fmt.Fprintf(os.Stderr, "pod-link: cannot write %s: %v\n", c.flagFile, err)
				return
			}
			if got := fmt.Sprintf("%t", capture); got != applied {
				applied = got
				fmt.Printf("pod-link: capture flag -> %t\n", capture)
			}
		})
		if !t.WaitTimeout(10*time.Second) || t.Error() != nil {
			fmt.Fprintf(os.Stderr, "pod-link: subscribe %s failed: %v\n", topicIntent, t.Error())
		}

		// One wildcard for every desired-state concern. Recorded here and acted on
		// by the reconcile tick rather than in the handler, so the whole of this
		// pod's state is decided in one place from one view -- and so a message
		// arriving mid-reconcile cannot interleave with it.
		dt := cl.Subscribe(desiredPrefix(c.node)+"+", 1, func(_ mqtt.Client, m mqtt.Message) {
			k := concern(c.node, m.Topic())
			v := strings.TrimSpace(string(m.Payload()))
			if k == "" {
				return
			}
			fmt.Printf("pod-link: desired %s -> %q\n", k, v)
			want.set(k, v)
		})
		if !dt.WaitTimeout(10*time.Second) || dt.Error() != nil {
			fmt.Fprintf(os.Stderr, "pod-link: subscribe desired failed: %v\n", dt.Error())
		}

		// Reboot is the one message that is not a state, so it is handled here and
		// now rather than reconciled. See reconcile.go for why it cannot be
		// retained; this refuses a retained one rather than honouring it, because
		// honouring it is an unattended boot loop and the pod would come back only
		// to read the same instruction again.
		rt := cl.Subscribe(rebootTopic(c.node), 1, func(_ mqtt.Client, m mqtt.Message) {
			if m.Retained() {
				fmt.Fprintf(os.Stderr, "pod-link: REFUSING a retained reboot request "+
					"(it would re-apply on every boot); publish it with retain off\n")
				return
			}
			fmt.Printf("pod-link: reboot requested: %s\n", strings.TrimSpace(string(m.Payload())))
			if err := h.reboot(); err != nil {
				fmt.Fprintf(os.Stderr, "pod-link: reboot failed: %v\n", err)
			}
		})
		if !rt.WaitTimeout(10*time.Second) || rt.Error() != nil {
			fmt.Fprintf(os.Stderr, "pod-link: subscribe reboot failed: %v\n", rt.Error())
		}
	})
	if t := cl.Connect(); t.Wait() && t.Error() != nil {
		return t.Error()
	}

	tick := time.NewTicker(c.statusPeriod)
	defer tick.Stop()

	for {
		doc := reconcile(c, h, want)
		if body, err := json.Marshal(doc); err == nil {
			publishStatus(cl, status, string(body))
		} else {
			fmt.Fprintf(os.Stderr, "pod-link: cannot marshal status: %v\n", err)
		}
		select {
		case <-stop:
			fmt.Println("pod-link: stop requested")
			// Leave the flag as it is. A pod told to stop by being shut down is a pod
			// whose power is about to go; rewriting the file on the way out would be a
			// write we do not need on a card we are about to lose.
			cl.Disconnect(250)
			return nil
		case <-tick.C:
		}
	}
}

// reconcile does one pass: observe, act where observation and desire disagree,
// and return the document describing both.
//
// Acting every pass rather than once per change is deliberate -- it is what makes
// this self-healing against a change made some other way (somebody turning the
// radio on by hand) and against an action that failed. The cost of a no-op pass is
// a comparison.
func reconcile(c config, h *host, want *wanted) *podStatus {
	w := want.snapshot()
	doc := &podStatus{
		State:     "ok",
		Node:      c.node,
		Build:     buildSHA,
		AsOfBootS: uptimeS(),
		Capture:   readFlagFile(c.flagFile),
		Stack:     unknown,
		Radio:     unknown,
		Desired:   w,
	}
	fail := func(format string, args ...any) {
		doc.Errors = append(doc.Errors, fmt.Sprintf(format, args...))
	}

	// Capture. Written in the intent handler for latency; re-asserted here so a
	// write that failed transiently does not leave the pod dark for the flight
	// while the broker still holds the intent that would fix it.
	switch w["capture"] {
	case "true", "false":
		if got := fmt.Sprintf("%t", doc.Capture); got != w["capture"] {
			if err := writeFlagFile(c.flagFile, w["capture"] == "true"); err != nil {
				fail("writing %s: %v", c.flagFile, err)
			} else {
				doc.Capture = w["capture"] == "true"
			}
		}
	}

	// Stack. Observation is the list of container inits other than our own; there
	// is no waiting to implement, because `running` simply stays until they are
	// gone.
	own := ownInit()
	inits, err := stackInits(own)
	if err != nil {
		fail("listing container inits: %v", err)
	} else {
		doc.StackInits = len(inits)
		doc.Stack = stackStopped
		if len(inits) > 0 {
			doc.Stack = stackRunning
		}
		switch w["stack"] {
		case stackStopped:
			if len(inits) > 0 {
				if err := signalStack(inits); err != nil {
					fail("stopping the stack: %v", err)
				}
			}
		case stackRunning, "":
			// NOT reconciled, and this is the one edge the graph does not carry.
			// Starting the stack means `docker compose up`, and invoking the docker
			// CLI faults in ~71 MiB of mapped text on a 417 MiB board with no swap --
			// the exact cost this whole process exists to avoid paying. The way back
			// to `running` is a reboot: the boot unit's ExecStart is unconditional
			// (#256), so coming up IS starting the stack. That edge is the reboot
			// message, so the graph is connected; it just is not labelled "start".
		default:
			fail("desired stack %q is not %q or %q", w["stack"], stackRunning, stackStopped)
		}
	}

	// Radio.
	radio, err := h.radio()
	doc.Radio = radio
	if err != nil {
		fail("%v", err)
	}
	switch wantRadio := w["radio"]; wantRadio {
	case radioOpen, radioClosed:
		if err == nil && radio != wantRadio {
			if err := h.setRadio(wantRadio); err != nil {
				fail("%v", err)
			}
		}
	case "":
	default:
		fail("desired radio %q is not %q or %q", wantRadio, radioOpen, radioClosed)
	}

	// The two capture processes' own documents, verbatim.
	if cam, err := passThrough(c.cameraStatusFile); err != nil {
		fail("%v", err)
	} else {
		doc.Camera = cam
	}
	if acc, err := passThrough(c.accelStatusFile); err != nil {
		fail("%v", err)
	} else {
		doc.Accel = acc
	}

	if free, err := freeBytes(c.dataDir); err != nil {
		fail("%v", err)
	} else {
		doc.DataFreeBytes = free
	}

	return doc
}

// signalStop closes the returned channel on SIGTERM or SIGINT.
//
// The roles take a plain channel rather than installing handlers themselves, so
// the end-to-end test can run both of them against a real broker and shut them
// down deterministically -- a test process cannot send itself SIGTERM without
// ending the run.
func signalStop() <-chan struct{} {
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	stop := make(chan struct{})
	go func() {
		<-sig
		close(stop)
	}()
	return stop
}

func main() {
	c, err := loadConfig()
	if err != nil {
		fmt.Fprintf(os.Stderr, "pod-link: %v\n", err)
		os.Exit(2)
	}
	fmt.Printf("pod-link: role=%s node=%s broker=%s build=%s\n", c.role, c.node, c.broker, buildSHA)
	stop := signalStop()
	if c.role == "publisher" {
		err = runPublisher(c, stop)
	} else {
		if err := os.MkdirAll(filepath.Dir(c.flagFile), 0o755); err != nil {
			fmt.Fprintf(os.Stderr, "pod-link: %v\n", err)
			os.Exit(1)
		}
		err = runSubscriber(c, stop)
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "pod-link: %v\n", err)
		os.Exit(1)
	}
}
