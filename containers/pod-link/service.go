package main

// Starting and stopping one container by name.
//
// The case this exists for: pulling an FC log needs `coordinator-mavlink` to let go
// of /dev/ttyAMA0, and bringing it back afterwards should not need a reboot. Whole-
// stack control cannot express that -- on the coordinator it would take the broker
// down with it, and on a campod it is the wrong granularity.
//
// Talks to the Docker API over its socket with net/http, rather than linking the
// docker client or shelling to the CLI. The CLI is the thing that costs 71 MiB of
// page faults on a Zero; the client library is a large dependency for four calls.
// An HTTP request over a unix socket is neither.
//
// Names come from `container_name:` in the stack files, which are pinned, so a
// desire names exactly one container. A name that does not exist gets a 404 from
// the daemon, which answers "is this possible" in the one step we were taking
// anyway -- there is no list of permitted names here.

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"time"
)

// dockerClient is an HTTP client bound to the daemon's unix socket.
//
// The host part of the URL is ignored by the dialer but has to be present for
// net/http to build a request, hence the conventional "docker" placeholder.
type dockerClient struct {
	http *http.Client
}

func newDockerClient(socket string) *dockerClient {
	return &dockerClient{http: &http.Client{
		Timeout: 30 * time.Second,
		Transport: &http.Transport{
			DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				return (&net.Dialer{}).DialContext(ctx, "unix", socket)
			},
		},
	}}
}

// do issues one request and returns the status and body.
func (d *dockerClient) do(method, path string) (int, []byte, error) {
	req, err := http.NewRequest(method, "http://docker"+path, nil)
	if err != nil {
		return 0, nil, err
	}
	resp, err := d.http.Do(req)
	if err != nil {
		return 0, nil, fmt.Errorf("docker socket: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, body, err
}

// running reports whether one container is running.
//
// Unversioned API paths, so the daemon answers with whatever it speaks rather than
// us pinning a version that a later image moves past.
func (d *dockerClient) running(name string) (bool, error) {
	code, body, err := d.do("GET", "/containers/"+name+"/json")
	if err != nil {
		return false, err
	}
	if code == http.StatusNotFound {
		return false, fmt.Errorf("no container named %q", name)
	}
	if code != http.StatusOK {
		return false, fmt.Errorf("inspecting %s: HTTP %d", name, code)
	}
	var out struct {
		State struct{ Running bool } `json:"State"`
	}
	if err := json.Unmarshal(body, &out); err != nil {
		return false, fmt.Errorf("inspecting %s: %w", name, err)
	}
	return out.State.Running, nil
}

// setRunning starts or stops one container.
//
// 304 is the daemon saying it was already in that state, which is success for a
// reconciler -- the desired state holds either way.
//
// Stopping goes through the daemon rather than signalling the init directly,
// deliberately: the daemon owns the grace period from the stack file and the
// escalation after it, and reimplementing that one layer up is the same thrashing
// twice. This is also why there is no wait here -- the next pass observes.
func (d *dockerClient) setRunning(name string, want bool) error {
	verb := "stop"
	if want {
		verb = "start"
	}
	code, body, err := d.do("POST", "/containers/"+name+"/"+verb)
	if err != nil {
		return err
	}
	switch code {
	case http.StatusNoContent, http.StatusNotModified:
		return nil
	case http.StatusNotFound:
		return fmt.Errorf("no container named %q", name)
	default:
		return fmt.Errorf("%sping %s: HTTP %d: %s", verb, name, code, body)
	}
}
