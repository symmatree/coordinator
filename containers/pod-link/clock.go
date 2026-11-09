package main

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
)

// The clock half of the `rtc` document: whether the 1 Hz is arriving (`edge`) and whether
// chrony has selected it (`pps_selected`). Read here, in the process that publishes them,
// so a pod runs no second resident process and no `chronyc` fork to produce two booleans.

// chronyd's command protocol, from chrony's candm.h and pktlength.c. Version 6 pads every
// request to the length of its reply, so a request cannot be used to amplify traffic.
const (
	chronyProtoVersion = 6
	chronyPktRequest   = 1
	chronyPktReply     = 2
	chronyReqTracking  = 33
	chronyRpyTracking  = 5
	chronyStatusOK     = 0
	// offsetof(CMD_Reply, data.tracking.EOR): a 28-byte reply header and RPY_Tracking
	// (ref_id 4, IPAddr 20, stratum 2, leap 2, Timespec 12, nine Floats 36). The TRACKING
	// request has no data of its own and is padded to this same length.
	chronyTrackingLen = 104
	// RPY_Tracking.ref_id, the first field after the reply header.
	chronyRefIDOffset = 28
)

// The refid both roles' chrony.conf give the PPS refclock, "PPS" as four bytes. chrony on
// it is the clock phased to the edge; on any other source a campod's phase is NTP's over
// the gadget link, tens to hundreds of milliseconds off its neighbours while its camera
// reports synced.
const ppsRefID = 0x50505300

// chronyOnPPS asks chronyd whether the PPS refclock is its selected source.
//
// UDP to chronyd's command port, the same request `chronyc tracking` sends and the same
// port collectd's chrony plugin reads on both roles. TRACKING is one of the commands
// chronyd answers without authentication, from localhost by default. Nil when chronyd
// cannot be asked, which is not the same answer as "on something else".
func chronyOnPPS(addr string) (*bool, error) {
	if addr == "" {
		return nil, nil
	}
	conn, err := net.Dial("udp", addr)
	if err != nil {
		return nil, fmt.Errorf("chronyd at %s: %w", addr, err)
	}
	defer conn.Close()
	if err := conn.SetDeadline(time.Now().Add(time.Second)); err != nil {
		return nil, fmt.Errorf("chronyd at %s: %w", addr, err)
	}
	seq := rand.Uint32()
	req := make([]byte, chronyTrackingLen)
	req[0], req[1] = chronyProtoVersion, chronyPktRequest
	binary.BigEndian.PutUint16(req[4:], chronyReqTracking)
	binary.BigEndian.PutUint32(req[8:], seq)
	if _, err := conn.Write(req); err != nil {
		return nil, fmt.Errorf("chronyd at %s: %w", addr, err)
	}
	rpy := make([]byte, 512)
	n, err := conn.Read(rpy)
	if err != nil {
		return nil, fmt.Errorf("chronyd at %s: %w", addr, err)
	}
	rpy = rpy[:n]
	if n < chronyTrackingLen || rpy[0] != chronyProtoVersion || rpy[1] != chronyPktReply ||
		binary.BigEndian.Uint16(rpy[4:]) != chronyReqTracking ||
		binary.BigEndian.Uint16(rpy[6:]) != chronyRpyTracking ||
		binary.BigEndian.Uint32(rpy[16:]) != seq {
		return nil, fmt.Errorf("chronyd at %s: not a tracking reply (%d bytes)", addr, n)
	}
	if st := binary.BigEndian.Uint16(rpy[8:]); st != chronyStatusOK {
		return nil, fmt.Errorf("chronyd at %s: tracking refused, status %d", addr, st)
	}
	on := binary.BigEndian.Uint32(rpy[chronyRefIDOffset:]) == ppsRefID
	return &on, nil
}

// ppsArriving is whether the kernel stamped an edge on the 1 Hz within the last
// ppsFreshness.
//
// `/sys/class/pps/pps0/assert` reads as `<secs>.<nsecs>#<seq>`, the realtime clock at the
// last edge. A working 1 Hz is never more than a second old; the margin is one missed
// pulse. A step of this clock -- chrony's first NTP sample on a pod -- makes the stamp
// read stale until the next edge, a second later. False when the file is absent (no
// pps-gpio) or empty (the device has never seen an edge, an unsoldered leg).
func ppsArriving(path string, now time.Time) bool {
	body, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	stamp, _, ok := strings.Cut(strings.TrimSpace(string(body)), "#")
	if !ok {
		return false
	}
	secs, err := strconv.ParseFloat(stamp, 64)
	if err != nil || secs <= 0 {
		return false
	}
	age := now.Sub(time.Unix(0, int64(secs*1e9)))
	return age >= -ppsFreshness && age < ppsFreshness
}

const ppsFreshness = 2 * time.Second

// clockDoc is the `rtc` document: coord-rtc feed's chip state on the coordinator, or
// nothing on a campod, with `pps_selected` from chronyd and, where this process reads the
// pulse itself, `edge`.
//
// On the coordinator the feed sets `edge`, because it watches every edge to read the chip
// against it. On a campod nothing else watches, so ppsAssertFile is set and `edge` is
// read here.
func clockDoc(c config, now time.Time) (json.RawMessage, []error) {
	var errs []error
	doc := map[string]any{}
	feed, err := thisBootDoc(c.rtcStatusFile)
	if err != nil {
		errs = append(errs, err)
	} else if feed != nil {
		if err := json.Unmarshal(feed, &doc); err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", c.rtcStatusFile, err))
		}
	}
	if c.ppsAssertFile != "" {
		doc["edge"] = ppsArriving(c.ppsAssertFile, now)
	}
	if c.chronyAddr != "" {
		on, err := chronyOnPPS(c.chronyAddr)
		if err != nil {
			errs = append(errs, err)
		}
		doc["pps_selected"] = on
	}
	if len(doc) == 0 {
		return nil, errs
	}
	body, err := json.Marshal(doc)
	if err != nil {
		return nil, append(errs, err)
	}
	return body, errs
}
