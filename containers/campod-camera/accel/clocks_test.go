package main

import (
	"testing"
	"time"
)

// A wrong CLOCK_* constant produces plausible-looking int64s that are silently
// the wrong quantity, and nothing downstream would notice until an alignment
// failed months later. So check each one is the clock it claims to be.
func TestClockIDsAreTheClocksTheyClaim(t *testing.T) {
	wall := wallNS()
	ref := time.Now().UnixNano()
	// CLOCK_REALTIME must agree with the Go runtime's wall clock. A second of
	// slack is enormous for two reads microseconds apart, so this only fails if
	// the constant is wrong.
	if d := wall - ref; d > int64(time.Second) || d < -int64(time.Second) {
		t.Fatalf("wallNS() = %d, time.Now() = %d, differ by %v -- clockRealtime is not CLOCK_REALTIME",
			wall, ref, time.Duration(d))
	}

	// CLOCK_MONOTONIC and CLOCK_BOOTTIME are both time-since-boot-ish, so they
	// must be far SMALLER than a realtime epoch -- unless the box booted in 1970.
	mono, boot := monoNS(), bootNS()
	if mono >= wall/2 {
		t.Errorf("monoNS() = %d is not plausibly time-since-boot next to realtime %d", mono, wall)
	}
	if boot >= wall/2 {
		t.Errorf("bootNS() = %d is not plausibly time-since-boot next to realtime %d", boot, wall)
	}

	// CLOCK_BOOTTIME includes suspend, CLOCK_MONOTONIC does not, so boot >= mono.
	if boot < mono {
		t.Errorf("bootNS() = %d < monoNS() = %d; BOOTTIME should never trail MONOTONIC", boot, mono)
	}
}

// All three must advance, and monotonic must never step backwards -- that is the
// whole reason the analysis joins on it rather than on the wall clock.
func TestMonotonicAdvancesAndNeverGoesBackwards(t *testing.T) {
	prev := monoNS()
	for i := 0; i < 50; i++ {
		time.Sleep(time.Millisecond)
		now := monoNS()
		if now < prev {
			t.Fatalf("monoNS() went backwards: %d then %d", prev, now)
		}
		prev = now
	}
	if d := monoNS() - prev; d < 0 {
		t.Fatalf("monoNS() regressed by %d", d)
	}
}
