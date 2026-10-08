package main

// Starting and stopping one service by name.
//
// The case this exists for: pulling an FC log needs `coordinator-mavlink` to let go of
// /dev/ttyAMA0, and bringing it back afterwards should not need a reboot. Whole-stack
// control cannot express that -- on the coordinator it would take the broker down with
// the instruction that asked for it.
//
// SYSTEMD, over the system bus (#449). The containers are quadlet units now, so a
// service IS a systemd unit and `StopUnit` is the operation -- which means this reuses
// the D-Bus connection already open for the radio and the reboot, and needs no
// container socket mounted at all.
//
// What this replaced: four HTTP calls over the Docker API socket. That was already
// chosen to avoid the CLI's page faults, but it is the wrong layer now. Stopping a
// container behind systemd's back leaves the unit and the container disagreeing about
// what is running, and systemd owns the stop timeout and the ordering either way.
//
// Names come from the quadlet units, which are pinned in the stack directory, so a
// desire names exactly one unit. A name systemd does not know comes back as
// `not-found` rather than an error, which is reported as what it is -- there is no list
// of permitted names here.

import (
	"fmt"
	"strings"

	"github.com/godbus/dbus/v5"
)

const (
	systemdService  = "org.freedesktop.systemd1"
	systemdPath     = dbus.ObjectPath("/org/freedesktop/systemd1")
	systemdManager  = "org.freedesktop.systemd1.Manager"
	methodStartUnit = systemdManager + ".StartUnit"
	methodStopUnit  = systemdManager + ".StopUnit"
	methodListNames = systemdManager + ".ListUnitsByNames"
)

// unitName normalises what somebody published into a systemd unit name.
//
// A bare name, a `.service` name and a `.container` name are all things a reader of
// the stack directory might reasonably type, and the quadlet file for
// `coordinator-mavlink.container` generates `coordinator-mavlink.service`. Accepting
// all three costs two lines and removes the only sharp edge in the topic.
func unitName(name string) string {
	name = strings.TrimSuffix(strings.TrimSpace(name), ".container")
	if !strings.HasSuffix(name, ".service") {
		name += ".service"
	}
	return name
}

// unitState is systemd's ActiveState for one unit: "active", "inactive", "failed",
// "activating", "deactivating", or "not-found" for a unit systemd does not know.
//
// ListUnitsByNames rather than GetUnit, because GetUnit errors on a unit that is not
// loaded and a stopped quadlet unit may well not be. This returns a row either way,
// which is what lets "stopped" and "no such unit" be different answers rather than
// the same error.
func (h *host) unitState(name string) (string, error) {
	conn, err := h.bus()
	if err != nil {
		return unknown, err
	}
	// UnitStatus is a 10-field struct; index 3 is active_state. Decoded into a slice
	// of dbus.Variant rather than a typed struct so a systemd that adds a field does
	// not break the decode.
	var out [][]dbus.Variant
	call := conn.Object(systemdService, systemdPath).Call(methodListNames, 0, []string{name})
	if call.Err != nil {
		return unknown, fmt.Errorf("%s: %w", methodListNames, call.Err)
	}
	if err := call.Store(&out); err != nil {
		return unknown, fmt.Errorf("decoding %s: %w", methodListNames, err)
	}
	if len(out) == 0 {
		return "not-found", nil
	}
	row := out[0]
	if len(row) < 4 {
		return unknown, fmt.Errorf("%s returned %d fields, expected at least 4", methodListNames, len(row))
	}
	state, ok := row[3].Value().(string)
	if !ok {
		return unknown, fmt.Errorf("active_state is %T, not a string", row[3].Value())
	}
	return state, nil
}

// setUnit starts or stops one unit and does NOT wait for the job.
//
// "replace" is systemd's standard job mode: supersede any conflicting job rather than
// failing or queueing behind it. The next reconcile pass observes the result, which is
// the same reason nothing else here waits -- `stack` reads `running` until the inits
// are actually gone, and a unit reads `activating` until it is up.
//
// systemd owns the stop timeout, from TimeoutStopSec in the quadlet unit, next to the
// measurement that justifies it. Nothing is reimplemented here.
func (h *host) setUnit(name string, want bool) error {
	conn, err := h.bus()
	if err != nil {
		return err
	}
	method, verb := methodStopUnit, "stop"
	if want {
		method, verb = methodStartUnit, "start"
	}
	var job dbus.ObjectPath
	call := conn.Object(systemdService, systemdPath).Call(method, 0, name, "replace")
	if call.Err != nil {
		return fmt.Errorf("%sing %s (polkit may refuse this): %w", verb, name, call.Err)
	}
	if err := call.Store(&job); err != nil {
		// The job was accepted; only the reply shape surprised us. Worth saying, not
		// worth failing: the next pass reads the real state regardless.
		return fmt.Errorf("%sing %s was accepted but the reply did not decode: %w", verb, name, err)
	}
	return nil
}

// activeFromState maps systemd's vocabulary onto the two words this bus uses.
//
// "activating" and "deactivating" are transitional and deliberately report as their
// destination's opposite -- a unit that is still coming up is not yet running, so a
// consumer comparing desired against actual sees them still disagreeing, which is
// true, rather than seeing success early.
func activeFromState(state string) string {
	if state == "active" {
		return stackRunning
	}
	return stackStopped
}
