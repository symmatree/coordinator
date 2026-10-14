package main

// Stopping the collection containers so that something else can run.
//
// WHY THIS EXISTS AT ALL. Almost any command sent to a device risks hanging while
// collection is running: the work is memory-bound on a 417 MiB board with no swap, so a
// new process faults in text that evicts page cache that then refaults off a ~20 MiB/s
// card. Stopping collection first is therefore deliberate and applies to every command,
// including read-only ones -- it is not an optimisation to be removed from the cheap
// callers. The trade is accepted: a version check on the flight line stops collection.
//
// WHAT WAS WRONG WITH THE OLD ONE. It was `sudo pkill -x -TERM dumb-init` over ssh,
// prepended to each command with a 40-iteration pgrep loop. Two problems, both about
// scope rather than intent: `dumb-init` is PID 1 in every image we build, so it signalled
// the whole stack including pod-link -- the resident foothold that is the only way to be
// selective at all, and without which a device drops off the bus until someone reboots it
// individually. And the selection could not be narrowed, because `comm` is identical
// across containers; discriminating meant logic in bash, spawning more processes, in
// exactly the memory state where spawning is the hazard.
//
// A resident process reading /proc and calling systemd over an open D-Bus connection
// spends none of that. There is not much budget here either -- just about enough to turn
// the collection containers off gracefully, which is what this does and all it does.
//
// GRACEFULLY MEANS StopUnit, not a signal. systemd owns the stop timeout from each unit's
// TimeoutStopSec, which sits next to the measurement that justified it, so the camera gets
// the time it needs to put its buffers down instead of being raced by a loop out here.

import (
	"fmt"
	"os"
	"strings"
	"sync"
	"time"
)

// How long to let the units take. StopUnit does not wait for its job, and the camera's
// TimeoutStopSec is the number that matters here -- so this has to outlast the slowest
// unit's own stop timeout or it would report a failure that was merely slow. Bounded
// because the caller is holding a command: past this, saying what is still up beats
// waiting longer.
const quiesceDeadline = 45 * time.Second

// How often to ask systemd whether they are down yet. A D-Bus property read, no spawn.
const quiescePoll = 500 * time.Millisecond

// collectionUnits is the set to stop, from POD_LINK_COLLECTION_UNITS, comma separated.
//
// CONFIGURED RATHER THAN DISCOVERED. "Which containers are the heavyweight collectors" is
// not a property the box can read off itself -- every one of them looks the same from
// /proc -- and guessing from a name pattern would make adding a container silently change
// what a quiesce does. The quadlet sets it per role, next to the units themselves.
//
// Empty is a valid answer and means "nothing here collects". It is reported, not treated
// as an error: a device with nothing to stop is a device a command can safely run on.
func collectionUnits() []string {
	raw := os.Getenv("POD_LINK_COLLECTION_UNITS")
	var out []string
	for _, f := range strings.Split(raw, ",") {
		if f = strings.TrimSpace(f); f != "" {
			out = append(out, unitName(f))
		}
	}
	return out
}

// quiesce stops each collection unit, and keeps going after a failure.
//
// NOT ALL-OR-NOTHING ON PURPOSE. The caller's next move is to run a command on a box whose
// memory it wants back, so stopping two of three collectors is strictly better than
// stopping none. Every failure is reported; the caller decides whether that is enough.
//
// pod-link is never in this set, which is what makes the result usable: the agent is not
// part of the state it manages, so there is still something here to take the device back
// the other way. See ownInit() in reconcile.go for the same argument about the stack walk.
func (h *host) quiesce(units []string) error {
	if len(units) == 0 {
		return nil
	}
	var failed []string
	for _, u := range units {
		if err := h.setUnit(u, false); err != nil {
			failed = append(failed, fmt.Sprintf("%s: %v", u, err))
		}
	}
	if failed != nil {
		return fmt.Errorf("stopping %s", strings.Join(failed, "; "))
	}
	return h.awaitStopped(units)
}

// awaitStopped blocks until every unit is out, or the deadline passes.
//
// THE WAIT BELONGS HERE, not in the caller. StopUnit returns when the job is accepted, so
// "asked it to stop" and "it is gone" are different facts, and the old ssh loop learned
// that the hard way -- 19.0 s for the camera after the command returned. Polling a D-Bus
// property from a resident process is the cheap place to find out; doing it from the other
// end of an ssh connection is what we are getting rid of.
func (h *host) awaitStopped(units []string) error {
	deadline := time.Now().Add(quiesceDeadline)
	for {
		var up []string
		for _, u := range units {
			st, err := h.unitState(u)
			if err != nil {
				// Treat an unreadable state as still up: the point is to be sure before
				// telling the caller it is safe to run something.
				up = append(up, fmt.Sprintf("%s (%v)", u, err))
				continue
			}
			if st != "inactive" && st != "failed" && st != "not-found" {
				up = append(up, fmt.Sprintf("%s is %s", u, st))
			}
		}
		if len(up) == 0 {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("still up after %s: %s", quiesceDeadline, strings.Join(up, ", "))
		}
		time.Sleep(quiescePoll)
	}
}

// quiesceResult is the last quiesce, for the status document -- the same shape as
// last_intent, and for the same reason: "did it take" should be one read of the status
// topic rather than an inference from somewhere else.
type quiesceResult struct {
	mu       sync.Mutex
	atBootS  float64
	units    string
	errorStr string
	seen     bool
}

func (q *quiesceResult) record(atBootS float64, units []string, err error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.seen, q.atBootS, q.units = true, atBootS, strings.Join(units, " ")
	q.errorStr = ""
	if err != nil {
		q.errorStr = err.Error()
	}
}

func (q *quiesceResult) snapshot() (bool, float64, string, string) {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.seen, q.atBootS, q.units, q.errorStr
}
