// The pod bus: what the devices say about themselves, read off MQTT instead of ssh.
//
// `docs/pod-bus.md` is the contract. The broker is mochi-mqtt on the coordinator, and every
// campod plus the coordinator publishes a RETAINED status document to `rekon/pod/<node>/status`.
//
// WHY THIS EXISTS RATHER THAN A PROBE. `probe.ts` asks a device what is installed, and pays a
// quiesce to do it -- `PROBE_COMMAND` is `quiesced('coord version')`, so loading the status screen
// SIGTERMs every container on every node and nothing restarts them until a reboot. Asking whether
// the pods are ready was the thing that made them not ready (#434). Readiness does not need that
// any more: it is published, and reading it costs the devices nothing at all.
//
// The two questions stay separate on purpose. This answers "what is it doing" -- ready, capturing,
// frames, free bytes, sensors. The probe answers "what is installed" -- image digests and
// revisions, which are static and worth a deliberate expensive question. See probe.ts.
//
// CONNECT, READ, DISCONNECT. Retained means one subscribe gets the whole current picture with
// nothing to assemble, so there is no long-lived subscription and no poll loop -- which keeps the
// existing rule that nothing here touches the fleet unless somebody asked.

import mqtt from 'mqtt';

/** Where the bus is. Empty disables, the way an empty notify url does. */
export interface BusConfig {
  url: string;
  /** How long to wait for retained messages to arrive after subscribing. */
  settleMs: number;
}

export const configured = (cfg: BusConfig): boolean => cfg.url !== '';

/**
 * One device's status document, as published.
 *
 * Deliberately NOT a schema of the whole thing. `camera` and `accel` are passed through verbatim
 * by pod-link from the files the capture processes write, so a field added on the writing side
 * must not need a change here -- the contract says there is no schema kept in two languages, and
 * re-declaring one would be exactly that. The fields below are the ones this service reads to
 * decide what to show; everything else rides along in `raw`.
 */
export interface PodStatus {
  node: string;
  /** `ok`, or `gone` -- published by the broker as the pod's last will when it drops. */
  state?: string;
  build?: string;
  capture?: boolean;
  stack?: string;
  radio?: string;
  dataFreeBytes?: number;
  /** Camera readiness, the part #434 is about. */
  ready?: boolean;
  phase?: string;
  frames?: number;
  /** The whole document, so nothing is lost by this interface being narrower than it. */
  raw: unknown;
}

/** What a read of the bus found, including why it found nothing. */
export interface BusRead {
  pods: PodStatus[];
  /** Set when the bus could not be read at all, as against being read and empty. */
  error?: string;
}

function parse(node: string, payload: string): PodStatus {
  const d = JSON.parse(payload) as Record<string, unknown>;
  const cam = (d.camera ?? {}) as Record<string, unknown>;
  return {
    // The document carries its own node, which is the device's own hostname. Trust that over
    // the topic: a device states its own identity, and #272 is what a configured one costs.
    node: typeof d.node === 'string' ? d.node : node,
    state: typeof d.state === 'string' ? d.state : undefined,
    build: typeof d.build === 'string' ? d.build : undefined,
    capture: typeof d.capture === 'boolean' ? d.capture : undefined,
    stack: typeof d.stack === 'string' ? d.stack : undefined,
    radio: typeof d.radio === 'string' ? d.radio : undefined,
    dataFreeBytes: typeof d.data_free_bytes === 'number' ? d.data_free_bytes : undefined,
    ready: typeof cam.ready === 'boolean' ? cam.ready : undefined,
    phase: typeof cam.phase === 'string' ? cam.phase : undefined,
    frames: typeof cam.frames === 'number' ? cam.frames : undefined,
    raw: d,
  };
}

/** `rekon/pod/<node>/status` -> `<node>`. */
export function nodeOfTopic(topic: string): string | undefined {
  const m = /^rekon\/pod\/([^/]+)\/status$/.exec(topic);
  return m?.[1];
}

/**
 * Every pod's current status, in one subscribe.
 *
 * `settleMs` is a WAIT, not a timeout on a request: retained messages arrive unprompted right
 * after SUBACK and there is no count to expect, so the only way to know they have all landed is
 * to stop listening. A pod that is off has no retained document and is simply absent -- which is
 * reported as absence rather than as an error, because a powered-off pod is not a fault.
 *
 * A document that will not parse is reported against that node rather than dropped: the contract
 * has already been bitten once by a payload read differently than its publisher meant, and a
 * reader that silently skips what it cannot read hides the same class of bug.
 */
export async function readPods(cfg: BusConfig): Promise<BusRead> {
  const found = new Map<string, PodStatus>();
  const client = mqtt.connect(cfg.url, {
    // Short: this is a bench read and a broker that is not there should say so quickly rather
    // than hold a page load.
    connectTimeout: 5_000,
    reconnectPeriod: 0,
    // Named so a `mosquitto_sub` at a bench can see who is attached.
    clientId: `fleet-control-${Math.random().toString(16).slice(2, 10)}`,
  });

  try {
    await new Promise<void>((resolve, reject) => {
      client.once('connect', () => resolve());
      client.once('error', (e) => reject(new Error(`${cfg.url}: ${e.message}`)));
      client.once('close', () => reject(new Error(`${cfg.url}: connection closed before connecting`)));
    });

    client.on('message', (topic, payload) => {
      const node = nodeOfTopic(topic);
      if (node === undefined) return;
      try {
        found.set(node, parse(node, payload.toString('utf8')));
      } catch (err) {
        found.set(node, { node, state: 'unreadable', raw: { error: (err as Error).message } });
      }
    });

    await new Promise<void>((resolve, reject) => {
      client.subscribe('rekon/pod/+/status', { qos: 0 }, (e) =>
        e ? reject(new Error(`subscribe failed: ${e.message}`)) : resolve());
    });

    await new Promise<void>((r) => setTimeout(r, cfg.settleMs));
    return { pods: [...found.values()].sort((a, b) => a.node.localeCompare(b.node)) };
  } catch (err) {
    return { pods: [], error: (err as Error).message };
  } finally {
    client.end(true);
  }
}

/**
 * Set one desired state on one device.
 *
 * RETAINED, because that is what the contract says a desired state is: the device reconciles it
 * every pass and picks it up again after a reboot without anyone re-publishing. Reboot is the one
 * exception and is not this function's business.
 *
 * Bare words, not JSON -- one enum value does not need a wrapper, and `mosquitto_sub -v` at a
 * bench stays readable.
 */
export async function setDesired(
  cfg: BusConfig,
  node: string,
  key: string,
  value: string,
): Promise<void> {
  const client = mqtt.connect(cfg.url, {
    connectTimeout: 5_000,
    reconnectPeriod: 0,
    clientId: `fleet-control-${Math.random().toString(16).slice(2, 10)}`,
  });
  try {
    await new Promise<void>((resolve, reject) => {
      client.once('connect', () => resolve());
      client.once('error', (e) => reject(new Error(`${cfg.url}: ${e.message}`)));
    });
    await new Promise<void>((resolve, reject) => {
      // QoS 1: a desired state that did not arrive is worse than one sent twice, and the device
      // reconciles idempotently -- the same value twice is one state.
      client.publish(`rekon/pod/${node}/desired/${key}`, value, { qos: 1, retain: true }, (e) =>
        e ? reject(new Error(`publish failed: ${e.message}`)) : resolve());
    });
  } finally {
    client.end(true);
  }
}
