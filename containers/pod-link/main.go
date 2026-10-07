// pod-link carries capture intent from the coordinator to the campods.
//
// One binary, two roles, selected by POD_LINK_ROLE:
//
//	publisher (coordinator)  additionally watches the arm file the MAVLink router
//	                         already writes and publishes it as capture intent
//	subscriber (campod)      additionally writes the capture flag capture.py reads
//
// BOTH roles reconcile the states a device can be told to be in -- services, stack,
// radio -- and both publish what they actually are. The role selects only the two
// lines above. The coordinator wants this too: pulling an FC log needs
// coordinator-mavlink to let go of /dev/ttyAMA0, and per-service control is how that
// is expressed without taking down the broker that carries the instruction.
//
// A LIMITED VOCABULARY IS THE POINT. The aim is a small resident foothold that stays
// in RAM and answers predictably, in place of an ssh session that can do anything.
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
	// subtree and dispatched on the remainder, so a new concern is a new case rather
	// than a new subscription -- `service/<container>` uses that second level.
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

// concern is the part of a desired-state topic that says what is being asked for --
// "radio", "stack", or "service/<container>". Returns "" for a topic that is not
// under this pod's prefix at all.
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
	// The Docker API socket, for per-service start and stop. Empty, or absent from
	// the container, disables that concern and says so in the status.
	dockerSocket string
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
		dockerSocket:     env("POD_LINK_DOCKER_SOCKET", "/var/run/docker.sock"),
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

// run connects, subscribes, and then reconciles on a tick until stopped.
//
// One function for both roles. The role adds the arm-file poll (publisher) or the
// capture-flag write (subscriber); everything else -- services, stack, radio, reboot,
// the status document -- is the same on both, because both are devices somebody needs
// to put into a known state from the bench without an ssh session.
func run(c config, stop <-chan struct{}) error {
	status := topicFor(c.node)
	want := newWanted()
	h := &host{}
	var docker *dockerClient
	if c.dockerSocket != "" {
		docker = newDockerClient(c.dockerSocket)
	}

	// Publisher state: the last intent published. Guarded because the on-connect
	// handler clears it from paho's goroutine while the loop below reads it, and
	// losing that write means no republish after a reconnect -- which matters because
	// the broker holds no retained state across a restart of its own.
	var mu sync.Mutex
	var lastIntent string
	var applied string

	cl := clientFor(c, func(cl mqtt.Client) {
		fmt.Printf("pod-link: connected to %s as %s\n", c.broker, c.role)
		mu.Lock()
		lastIntent = ""
		mu.Unlock()

		if c.role == "subscriber" {
			t := cl.Subscribe(topicIntent, 1, func(_ mqtt.Client, m mqtt.Message) {
				capture := strings.Contains(string(m.Payload()), `"capture":true`)
				// Recorded like every other desire so the status shows asked-for beside
				// actual, and written here rather than on the tick: arm comes from the
				// operator's controller and the first frame should not wait a status
				// period on top of the publisher's poll.
				want.set("capture", fmt.Sprintf("%t", capture))
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
		}

		// One subtree for every desired-state concern. Recorded here and acted on by
		// the tick, so the whole of this device's state is decided in one place from
		// one view.
		dt := cl.Subscribe(desiredPrefix(c.node)+"#", 1, func(_ mqtt.Client, m mqtt.Message) {
			k := concern(c.node, m.Topic())
			if k == "" {
				return
			}
			v := strings.TrimSpace(string(m.Payload()))
			fmt.Printf("pod-link: desired %s -> %q\n", k, v)
			want.set(k, v)
		})
		if !dt.WaitTimeout(10*time.Second) || dt.Error() != nil {
			fmt.Fprintf(os.Stderr, "pod-link: subscribe desired failed: %v\n", dt.Error())
		}

		// Reboot is not a state, so it happens here rather than on the tick. Refused if
		// retained, because the device would come back and read the same instruction.
		rt := cl.Subscribe(rebootTopic(c.node), 1, func(_ mqtt.Client, m mqtt.Message) {
			if m.Retained() {
				fmt.Fprintf(os.Stderr, "pod-link: refusing a retained reboot request; "+
					"it would re-apply on every boot. Publish it with retain off\n")
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

	// Two cadences. Intent is polled at the faster one because it carries arm state;
	// nothing waits on a status refresh.
	armTick := time.NewTicker(c.period)
	defer armTick.Stop()
	statusTick := time.NewTicker(c.statusPeriod)
	defer statusTick.Stop()

	publishIntent := func() {
		if c.role != "publisher" {
			return
		}
		payload := `{"capture":false,"reason":"disarmed"}`
		if readFlagFile(c.armFile) {
			payload = `{"capture":true,"reason":"armed"}`
		}
		mu.Lock()
		changed := payload != lastIntent
		if changed {
			lastIntent = payload
		}
		mu.Unlock()
		if changed {
			publish(cl, topicIntent, payload)
			fmt.Printf("pod-link: intent -> %s\n", payload)
		}
	}

	publishStatusNow := func() {
		doc := reconcile(c, h, docker, want)
		if body, err := json.Marshal(doc); err == nil {
			publishStatus(cl, status, string(body))
		} else {
			fmt.Fprintf(os.Stderr, "pod-link: cannot marshal status: %v\n", err)
		}
	}

	publishIntent()
	publishStatusNow()

	for {
		select {
		case <-stop:
			fmt.Println("pod-link: stop requested")
			// Leave the capture flag as it is. A pod told to stop by being shut down is
			// a pod whose power is about to go, and that is the ordinary case rather
			// than an exception: the plug is the lifecycle here.
			cl.Disconnect(250)
			return nil
		case <-armTick.C:
			publishIntent()
		case <-statusTick.C:
			publishStatusNow()
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
func reconcile(c config, h *host, docker *dockerClient, want *wanted) *podStatus {
	w := want.snapshot()
	// The coordinator has no capture flag of its own; what it knows is the arm state
	// it publishes from, which is the honest thing for it to report.
	captureFile := c.flagFile
	if c.role == "publisher" {
		captureFile = c.armFile
	}
	doc := &podStatus{
		State:     "ok",
		Node:      c.node,
		Build:     buildSHA,
		AsOfBootS: uptimeS(),
		Capture:   readFlagFile(captureFile),
		Stack:     unknown,
		Radio:     unknown,
		Desired:   w,
	}
	fail := func(format string, args ...any) {
		doc.Errors = append(doc.Errors, fmt.Sprintf(format, args...))
	}

	// Services, by container name. The reason this granularity exists: the FC log
	// pull needs coordinator-mavlink to release /dev/ttyAMA0, and whole-stack control
	// on the coordinator would take the broker down with the instruction.
	for k, v := range w {
		name, ok := strings.CutPrefix(k, "service/")
		if !ok || name == "" {
			continue
		}
		if v != stackRunning && v != stackStopped {
			fail("desired service/%s %q is not %q or %q", name, v, stackRunning, stackStopped)
			continue
		}
		if docker == nil {
			fail("service/%s wants %q but there is no docker socket mounted", name, v)
			continue
		}
		isRunning, err := docker.running(name)
		if err != nil {
			fail("%v", err)
			continue
		}
		if doc.Services == nil {
			doc.Services = map[string]string{}
		}
		doc.Services[name] = stackStopped
		if isRunning {
			doc.Services[name] = stackRunning
		}
		if isRunning != (v == stackRunning) {
			if err := docker.setRunning(name, v == stackRunning); err != nil {
				fail("%v", err)
			}
		}
	}

	// Capture. Written in the intent handler for latency; re-asserted here so a
	// write that failed transiently does not leave the pod dark for the flight
	// while the broker still holds the intent that would fix it.
	switch w["capture"] {
	case "true", "false":
		if got := fmt.Sprintf("%t", doc.Capture); c.role == "subscriber" && got != w["capture"] {
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

	// Radio. Read every pass, because "is this pod's radio on" is worth knowing
	// unasked -- but a FAILED read is only an error when somebody wanted a radio
	// state. A device with no D-Bus socket mounted reports `radio: unknown`, which
	// is the fact rather than a fault, and repeating it as an error every five
	// seconds would bury the ones that mean something.
	radio, radioErr := h.radio()
	doc.Radio = radio
	switch wantRadio := w["radio"]; wantRadio {
	case radioOpen, radioClosed:
		if radioErr != nil {
			fail("%v", radioErr)
		} else if radio != wantRadio {
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
	if c.role == "subscriber" {
		if err := os.MkdirAll(filepath.Dir(c.flagFile), 0o755); err != nil {
			fmt.Fprintf(os.Stderr, "pod-link: %v\n", err)
			os.Exit(1)
		}
	}
	err = run(c, stop)
	if err != nil {
		fmt.Fprintf(os.Stderr, "pod-link: %v\n", err)
		os.Exit(1)
	}
}
