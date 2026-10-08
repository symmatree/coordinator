package main

// Starting and stopping one systemd unit, over the same system bus connection the
// radio and the reboot use.
//
// The case it exists for: pulling an FC log needs coordinator-mavlink to release
// /dev/ttyAMA0, and whole-stack control on the coordinator would stop the broker that
// carried the instruction. Contract: docs/pod-bus.md.

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

// unitName turns a published name into a systemd unit name. A bare name, `.service`
// and `.container` all resolve, since the quadlet file and the unit differ by suffix.
func unitName(name string) string {
	name = strings.TrimSuffix(strings.TrimSpace(name), ".container")
	if !strings.HasSuffix(name, ".service") {
		name += ".service"
	}
	return name
}

// unitState is systemd's ActiveState: active, inactive, failed, activating,
// deactivating, or not-found.
//
// ListUnitsByNames rather than GetUnit, which errors on a unit that is not loaded --
// so "stopped" and "no such unit" stay different answers.
func (h *host) unitState(name string) (string, error) {
	conn, err := h.bus()
	if err != nil {
		return unknown, err
	}
	// UnitStatus is a 10-field struct; index 3 is active_state. Decoded loosely so an
	// added field does not break it.
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

// setUnit starts or stops one unit. Does not wait for the job: the next reconcile pass
// observes the result. "replace" supersedes a conflicting job rather than queueing.
// systemd owns the stop timeout, from the unit's TimeoutStopSec.
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

// activeFromState maps systemd's vocabulary onto the two words this bus uses. Only
// "active" counts as running, so a transitional state reads as not-yet-there.
func activeFromState(state string) string {
	if state == "active" {
		return stackRunning
	}
	return stackStopped
}
