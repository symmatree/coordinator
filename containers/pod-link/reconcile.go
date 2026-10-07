package main

// What a pod does when it is told what state to be in.
//
// EVERY CONTROL HERE IS A DESIRED STATE, NOT A COMMAND, and that is the whole
// design rather than a stylistic preference. "Stack running" is a node and "stop
// the stack" is the edge into it; publishing the node and letting the pod walk
// the edge gets three properties free that a command would have to build:
//
//   * Idempotence. The same retained message applied twice is the same state.
//   * Recovery without a retry policy. The broker holds the last desired value,
//     so a pod whose link blipped reconciles on reconnect instead of having
//     missed a one-shot.
//   * No waiting. `pkill` returns when the signal is sent, not when the process
//     is gone -- measured at 1.1 s for the accel and 19.0 s for the camera, both
//     AFTER the command returned -- so every imperative caller has to carry its
//     own wait loop, and two of ours shipped without one. A reconciler has no
//     wait to forget: `stack` reads `running` until the inits are actually gone.
//
// The one exception is reboot, at the bottom, and the test that makes it an
// exception is sharp: does the desired value stay true once it has been reached?
// "Stopped" does. "Rebooted" does not -- the box comes back in the node it left,
// so a retained reboot request means reboot, come up, read it again, reboot.

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"

	"github.com/godbus/dbus/v5"
)

const (
	stackRunning = "running"
	stackStopped = "stopped"
	radioOpen    = "open"
	radioClosed  = "closed"
	unknown      = "unknown"
)

// initComm is what every image on a device runs as its container init. One name,
// put there by our own Dockerfiles, which is why `coord stop` and the ansible
// quiesce can both select on it with no table to keep in sync.
const initComm = "dumb-init"

// procRoot is a var, not a const, so the tests can point the walk at a fixture
// directory -- the same reason campod-accel's hostHostnamePath is one. Nothing
// else reassigns it.
var procRoot = "/proc"

// ownInit is this container's own init, which must be excluded from everything
// below. dumb-init exec'd us, so it is our parent -- no search, no guessing, and
// correct even though the PID is a host PID we never chose.
//
// Excluding it is not a special case. The agent is not part of the state it
// manages: pod-link has to survive the transition it performs, or there is
// nothing left to take the pod back the other way.
func ownInit() int { return os.Getppid() }

// stackInits lists the container inits on the box other than our own.
//
// Requires `pid: host` on the service. Without it this process is in its own PID
// namespace and /proc shows only itself, so the camera and accel inits are not
// merely unreachable, they are invisible -- the list comes back empty and the
// stack reads `stopped` while it is running.
//
// Reads /proc directly rather than shelling to pgrep: procps is not in this image,
// and the walk is a few hundred in-memory reads with no fork and no page cache
// touched. That distinction is the point on a board where invoking the docker CLI
// faults in ~71 MiB and costs seconds.
func stackInits(own int) ([]int, error) {
	entries, err := os.ReadDir(procRoot)
	if err != nil {
		return nil, err
	}
	var pids []int
	for _, e := range entries {
		pid, err := strconv.Atoi(e.Name())
		if err != nil || pid == own || pid == os.Getpid() {
			continue
		}
		comm, err := os.ReadFile(filepath.Join(procRoot, e.Name(), "comm"))
		if err != nil {
			continue // exited between the listing and the read; not an error
		}
		if strings.TrimSpace(string(comm)) == initComm {
			pids = append(pids, pid)
		}
	}
	return pids, nil
}

// signalStack SIGTERMs each init, which is exactly what `coord stop` does.
//
// dumb-init is PID 1 in every image we run and proxies the signal to its child's
// whole process group (dumb-init.c:66), so one signal per container stops
// everything in it. The program exits, containerd reports the task exit, and
// dockerd tears the container down the ordinary way -- the same path as any
// program in a container finishing.
//
// NO ESCALATION TO SIGKILL, matching bin/coord's deliberate choice. Signal, wait,
// send something harder is what docker already does; doing it again one layer up
// is the same thrashing twice. A container that does not exit is a result worth
// seeing rather than something to force.
func signalStack(pids []int) error {
	var failed []string
	for _, pid := range pids {
		if err := syscall.Kill(pid, syscall.SIGTERM); err != nil && err != syscall.ESRCH {
			failed = append(failed, fmt.Sprintf("%d: %v", pid, err))
		}
	}
	if failed != nil {
		return fmt.Errorf("SIGTERM failed for %s", strings.Join(failed, ", "))
	}
	return nil
}

// NetworkManager over the system bus. Mount /run/dbus into the container for any
// of this to work; absent it, every call below fails and says so in the status,
// while capture intent keeps working. That isolation is the reason these are
// handlers in one process rather than one process per concern -- a pod with no
// D-Bus should lose the radio verb, not the flight.
const (
	nmService  = "org.freedesktop.NetworkManager"
	nmPath     = dbus.ObjectPath("/org/freedesktop/NetworkManager")
	nmWireless = "org.freedesktop.NetworkManager.WirelessEnabled"

	login1Service = "org.freedesktop.login1"
	login1Path    = dbus.ObjectPath("/org/freedesktop/login1")
	login1Reboot  = "org.freedesktop.login1.Manager.Reboot"
)

// host is the device's own system services, reached over D-Bus.
//
// The connection is made on demand and kept. A pod whose radio is already in the
// desired state never opens it at all, which keeps a stack with no /run/dbus mount
// silent rather than noisy.
//
// Guarded, because two goroutines reach it: the reconcile tick reads the radio
// property, and the reboot handler runs on paho's delivery goroutine. Lazy
// initialisation from both is a data race, and the failure it produces -- two
// connections, one leaked -- is the kind that only shows up when someone reboots a
// pod at the moment a tick lands.
type host struct {
	mu   sync.Mutex
	conn *dbus.Conn
}

func (h *host) bus() (*dbus.Conn, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.conn != nil {
		return h.conn, nil
	}
	conn, err := dbus.ConnectSystemBus()
	if err != nil {
		return nil, fmt.Errorf("system bus (is /run/dbus mounted?): %w", err)
	}
	h.conn = conn
	return conn, nil
}

// radio reports whether the WiFi radio is enabled.
//
// The same property `nmcli radio wifi` reads, and NetworkManager persists it
// across boots in /var/lib/NetworkManager/NetworkManager.state -- so this is the
// same mechanism `coord radio` uses, not a parallel one, and nothing turns the
// radio back on by itself.
func (h *host) radio() (string, error) {
	conn, err := h.bus()
	if err != nil {
		return unknown, err
	}
	v, err := conn.Object(nmService, nmPath).GetProperty(nmWireless)
	if err != nil {
		return unknown, fmt.Errorf("reading %s: %w", nmWireless, err)
	}
	on, ok := v.Value().(bool)
	if !ok {
		return unknown, fmt.Errorf("%s is %T, not a bool", nmWireless, v.Value())
	}
	if on {
		return radioOpen, nil
	}
	return radioClosed, nil
}

// setRadio enables or disables the radio.
//
// Going CLOSED needs no gate here, and that is a real difference from the ssh
// path. `coord radio closed` refuses unless it can first ping the coordinator
// over the gadget link, because that link is the way back in -- but a message
// that arrived here came over that link, through a broker on the coordinator. The
// check is satisfied by construction rather than reimplemented.
func (h *host) setRadio(want string) error {
	conn, err := h.bus()
	if err != nil {
		return err
	}
	if err := conn.Object(nmService, nmPath).SetProperty(nmWireless, want == radioOpen); err != nil {
		return fmt.Errorf("setting %s (polkit may refuse this): %w", nmWireless, err)
	}
	return nil
}

// reboot asks systemd for an ordered restart.
//
// login1's Reboot, not the reboot(2) syscall, and the difference is the whole
// point: systemd stops the units, which gives the capture containers their
// stop_grace_period to finish writing. A hard syscall reboot would skip that on a
// device whose only deliverable is what reached the card.
//
// sync() first anyway, because the reboot might not be the thing that kills us --
// on this vehicle a power pull usually is.
func (h *host) reboot() error {
	conn, err := h.bus()
	if err != nil {
		return err
	}
	syscall.Sync()
	if call := conn.Object(login1Service, login1Path).Call(login1Reboot, 0, false); call.Err != nil {
		return fmt.Errorf("%s: %w", login1Reboot, call.Err)
	}
	return nil
}
