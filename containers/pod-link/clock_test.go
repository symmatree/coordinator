package main

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// fakeChronyd answers TRACKING requests the way chronyd does, with refID as the selected
// source. Returns its address and a pointer to the length of the last request it saw.
func fakeChronyd(t *testing.T, refID uint32, mangle func([]byte)) (string, *int) {
	t.Helper()
	pc, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { pc.Close() })
	seen := new(int)
	go func() {
		buf := make([]byte, 1024)
		for {
			n, from, err := pc.ReadFrom(buf)
			if err != nil {
				return
			}
			*seen = n
			req := buf[:n]
			rpy := make([]byte, chronyTrackingLen)
			rpy[0], rpy[1] = req[0], chronyPktReply
			copy(rpy[4:6], req[4:6])
			binary.BigEndian.PutUint16(rpy[6:], chronyRpyTracking)
			copy(rpy[16:20], req[8:12])
			binary.BigEndian.PutUint32(rpy[chronyRefIDOffset:], refID)
			if mangle != nil {
				mangle(rpy)
			}
			pc.WriteTo(rpy, from)
		}
	}()
	return pc.LocalAddr().String(), seen
}

func show(b *bool) string {
	if b == nil {
		return "unknown"
	}
	return fmt.Sprintf("%t", *b)
}

func TestChronyOnPPS(t *testing.T) {
	for _, tc := range []struct {
		name   string
		refID  uint32
		mangle func([]byte)
		want   string
	}{
		{"chrony on the PPS refclock", ppsRefID, nil, "true"},
		// The coordinator's gadget address, 10.55.0.1: a campod still on NTP.
		{"chrony on the coordinator's NTP", 0x0A370001, nil, "false"},
		{"chrony on the chip's seconds alone", 0x44533332, nil, "false"},
		{"chrony with no source", 0, nil, "false"},
		{"a refusal is unknown, not false", ppsRefID,
			func(r []byte) { binary.BigEndian.PutUint16(r[8:], 2) }, "unknown"},
		{"a reply to someone else's request is unknown", ppsRefID,
			func(r []byte) { r[16] ^= 0xFF }, "unknown"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			addr, seen := fakeChronyd(t, tc.refID, tc.mangle)
			got, err := chronyOnPPS(addr)
			if show(got) != tc.want {
				t.Errorf("got %s (%v), want %s", show(got), err, tc.want)
			}
			if (got == nil) != (err != nil) {
				t.Errorf("an unknown answer comes with its reason and only then: %s, %v", show(got), err)
			}
			// chronyd answers a version 6 request only when it is padded to its reply's
			// length. 104 is what a real chronyd sent back to this request, written out
			// rather than taken from the constant so the two cannot agree on a wrong size.
			if *seen != 104 {
				t.Errorf("request was %d bytes, want 104", *seen)
			}
		})
	}

	t.Run("no chronyd is unknown", func(t *testing.T) {
		pc, err := net.ListenPacket("udp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		addr := pc.LocalAddr().String()
		pc.Close()
		got, err := chronyOnPPS(addr)
		if got != nil || err == nil {
			t.Errorf("got %s, %v", show(got), err)
		}
	})
}

func TestPPSArriving(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "assert")
	now := time.Unix(1794084822, 547000000)
	for _, tc := range []struct {
		name string
		body string
		want bool
	}{
		{"an edge half a second ago", "1794084822.047000000#1234\n", true},
		{"the last edge five seconds ago", "1794084817.547000000#1234\n", false},
		// What the file holds on a device that has never seen an edge.
		{"never an edge", "0.000000000#0\n", false},
		{"empty", "", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := os.WriteFile(path, []byte(tc.body), 0o644); err != nil {
				t.Fatal(err)
			}
			if got := ppsArriving(path, now); got != tc.want {
				t.Errorf("got %t, want %t", got, tc.want)
			}
		})
	}
	if ppsArriving(filepath.Join(dir, "absent"), now) {
		t.Error("no pps device is no edge")
	}
}

func TestClockDoc(t *testing.T) {
	root := t.TempDir()
	withProc(t, root)
	if err := os.MkdirAll(filepath.Join(root, "sys", "kernel", "random"), 0o755); err != nil {
		t.Fatal(err)
	}
	const bootID = "this-boot"
	if err := os.WriteFile(filepath.Join(root, "sys", "kernel", "random", "boot_id"),
		[]byte(bootID+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	addr, _ := fakeChronyd(t, ppsRefID, nil)
	dir := t.TempDir()
	now := time.Now()

	t.Run("campod: edge and selection read here", func(t *testing.T) {
		assertFile := filepath.Join(dir, "assert")
		stamp := fmt.Sprintf("%d.000000000#9\n", now.Unix())
		if err := os.WriteFile(assertFile, []byte(stamp), 0o644); err != nil {
			t.Fatal(err)
		}
		body, errs := clockDoc(config{ppsAssertFile: assertFile, chronyAddr: addr}, now)
		if len(errs) != 0 {
			t.Fatal(errs)
		}
		if string(body) != `{"edge":true,"pps_selected":true}` {
			t.Errorf("got %s", body)
		}
	})

	t.Run("coordinator: the feed's chip state and edge, with chrony's selection", func(t *testing.T) {
		feed := filepath.Join(dir, "rtc_status")
		doc := fmt.Sprintf(`{"boot_id":%q,"edge":false,"state":"ok"}`, bootID)
		if err := os.WriteFile(feed, []byte(doc), 0o644); err != nil {
			t.Fatal(err)
		}
		body, errs := clockDoc(config{rtcStatusFile: feed, chronyAddr: addr}, now)
		if len(errs) != 0 {
			t.Fatal(errs)
		}
		var got map[string]any
		if err := json.Unmarshal(body, &got); err != nil {
			t.Fatal(err)
		}
		if got["edge"] != false || got["state"] != "ok" || got["pps_selected"] != true ||
			got["boot_id"] != bootID {
			t.Errorf("got %s", body)
		}
	})

	t.Run("chronyd not answering is null, and an error", func(t *testing.T) {
		pc, err := net.ListenPacket("udp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		gone := pc.LocalAddr().String()
		pc.Close()
		body, errs := clockDoc(config{chronyAddr: gone}, now)
		if len(errs) != 1 || string(body) != `{"pps_selected":null}` {
			t.Errorf("got %s, %v", body, errs)
		}
	})

	t.Run("nothing configured is no document", func(t *testing.T) {
		if body, errs := clockDoc(config{}, now); body != nil || errs != nil {
			t.Errorf("got %s, %v", body, errs)
		}
	})
}
