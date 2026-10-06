// pod-link carries capture intent from the coordinator to the campods.
//
// One binary, two roles, selected by POD_LINK_ROLE:
//
//	publisher (coordinator)  watches the arm file the MAVLink router already
//	                         writes and publishes it as capture intent
//	subscriber (campod)      receives intent and writes the capture flag that
//	                         capture.py reads, and publishes its own status
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
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
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
)

// topicFor is per node, deliberately. One shared status topic would mean the last
// pod to publish is the only one the coordinator can see, and "which pods are
// ready" is the question this exists to answer.
func topicFor(node string) string { return fmt.Sprintf(topicStatusFmt, node) }

type config struct {
	role     string
	broker   string
	node     string
	armFile  string // publisher: what to watch
	flagFile string // subscriber: what to write
	period   time.Duration
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
	}
	secs, err := strconv.Atoi(env("POD_LINK_PERIOD_S", "2"))
	if err != nil || secs < 1 {
		return c, fmt.Errorf("POD_LINK_PERIOD_S=%q is not a positive integer", os.Getenv("POD_LINK_PERIOD_S"))
	}
	c.period = time.Duration(secs) * time.Second
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
		SetConnectRetryInterval(5 * time.Second).
		SetMaxReconnectInterval(30 * time.Second).
		SetKeepAlive(10 * time.Second).
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

// runPublisher watches the arm file and republishes it when it changes.
//
// POLLED, not inotify. The file is written by a different container through a
// shared mount, where inotify's guarantees get thin, and a 2 s stat on a Pi 4B is
// free. Capture starting up to 2 s after arm is inside the arm-to-takeoff window.
func runPublisher(c config) error {
	var last string
	cl := clientFor(c, func(cl mqtt.Client) {
		fmt.Printf("pod-link: connected to %s as publisher\n", c.broker)
		last = "" // force a republish, so a reconnect re-establishes the retained value
	})
	if t := cl.Connect(); t.Wait() && t.Error() != nil {
		return t.Error()
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	tick := time.NewTicker(c.period)
	defer tick.Stop()

	for {
		armed := readFlagFile(c.armFile)
		payload := `{"capture":false,"reason":"disarmed"}`
		if armed {
			payload = `{"capture":true,"reason":"armed"}`
		}
		if payload != last {
			publish(cl, topicIntent, payload)
			fmt.Printf("pod-link: intent -> %s\n", payload)
			last = payload
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

// runSubscriber receives intent, writes the flag, and reports what it did.
//
// It writes the flag on EVERY message rather than on change: the file is the
// authority capture.py reads, and a retained message arriving after a reconnect
// must restore it even if this process believes nothing changed.
func runSubscriber(c config) error {
	status := topicFor(c.node)
	var applied string

	cl := clientFor(c, func(cl mqtt.Client) {
		fmt.Printf("pod-link: connected to %s as subscriber\n", c.broker)
		t := cl.Subscribe(topicIntent, 1, func(_ mqtt.Client, m mqtt.Message) {
			want := strings.Contains(string(m.Payload()), `"capture":true`)
			if err := writeFlagFile(c.flagFile, want); err != nil {
				fmt.Fprintf(os.Stderr, "pod-link: cannot write %s: %v\n", c.flagFile, err)
				publish(cl, status, fmt.Sprintf(`{"state":"error","detail":%q}`, err.Error()))
				return
			}
			if got := fmt.Sprintf("%t", want); got != applied {
				applied = got
				fmt.Printf("pod-link: capture flag -> %t\n", want)
			}
			publish(cl, status, fmt.Sprintf(`{"state":"ok","capture":%t,"build":%q}`, want, buildSHA))
		})
		if !t.WaitTimeout(10*time.Second) || t.Error() != nil {
			fmt.Fprintf(os.Stderr, "pod-link: subscribe failed: %v\n", t.Error())
		}
	})
	if t := cl.Connect(); t.Wait() && t.Error() != nil {
		return t.Error()
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	fmt.Println("pod-link: stop requested")
	// Leave the flag as it is. A pod that is told to stop capturing by being shut
	// down is a pod whose power is about to go; rewriting the file on the way out
	// would be a write we do not need on a card we are about to lose.
	cl.Disconnect(250)
	return nil
}

func main() {
	c, err := loadConfig()
	if err != nil {
		fmt.Fprintf(os.Stderr, "pod-link: %v\n", err)
		os.Exit(2)
	}
	fmt.Printf("pod-link: role=%s node=%s broker=%s build=%s\n", c.role, c.node, c.broker, buildSHA)
	if c.role == "publisher" {
		err = runPublisher(c)
	} else {
		if err := os.MkdirAll(filepath.Dir(c.flagFile), 0o755); err != nil {
			fmt.Fprintf(os.Stderr, "pod-link: %v\n", err)
			os.Exit(1)
		}
		err = runSubscriber(c)
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "pod-link: %v\n", err)
		os.Exit(1)
	}
}
