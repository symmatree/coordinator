// The pod bus: what the devices say about themselves, read off MQTT instead of ssh.
//
// `docs/pod-bus.md` is the contract. The broker is mochi-mqtt on the coordinator, and every
// campod plus the coordinator publishes a RETAINED status document to `rekon/pod/<node>/status`.
//
// WHY THIS EXISTS RATHER THAN A PROBE. `probe.ts` asks a device what is installed, and pays a
// quiesce to do it, so loading the status screen stops collection on every node and nothing
// restarts it until a power cycle. Asking whether the pods are ready was the thing that made them
// not ready (#434). Readiness does not need that any more: it is published, and reading it costs
// the devices nothing at all.
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
  /**
   * Will arming work now: warm AND synced, combined on the pod (#577). The two halves
   * below are carried so the table can say WHY a pod is not ready; nothing in this
   * service recombines them, because then there would be two definitions of ready.
   */
  ready?: boolean;
  /** Camera started and buffers allocated, the part #434 is about. */
  warm?: boolean;
  /** This pod believes it is aligned with the pair. Absent until it has evaluated. */
  syncReady?: boolean;
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
    warm: typeof cam.warm === 'boolean' ? cam.warm : undefined,
    syncReady: typeof cam.sync_ready === 'boolean' ? cam.sync_ready : undefined,
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
/**
 * Ask one device to quiesce, and wait until it says it has.
 *
 * UNRETAINED, unlike setDesired. A quiesce is an action, not a state: retained, it would
 * stop collection again on every boot, which is a device that silently never collects.
 * `reboot` is unretained for the same reason.
 *
 * THE WAIT IS THE DEVICE'S, not ours. pod-link stops the units and then polls systemd
 * until they are actually out -- `StopUnit` returns when the job is accepted, and the
 * camera took 19.0 s to go after the old command had returned. It publishes its status
 * out of band when it finishes, so all we do here is wait for one message. Doing the
 * polling from this end is what we are getting rid of: every round trip to a device whose
 * collection is still running is the thing that hangs.
 *
 * Resolves with what the device reported, or rejects on timeout. A timeout is worth
 * surfacing rather than carrying on: the caller's next move assumes the memory is free.
 */
export async function quiesceNode(
  cfg: BusConfig,
  node: string,
  reason: string,
  timeoutMs = 60_000,
): Promise<{ units: string; error?: string }> {
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
    // Subscribe BEFORE publishing. The status is published the moment the device finishes,
    // and a subscribe afterwards can miss it -- the retained copy would then be the one
    // from before the quiesce, which reads as success.
    await new Promise<void>((resolve, reject) => {
      client.subscribe(`rekon/pod/${node}/status`, { qos: 0 }, (e) =>
        e ? reject(new Error(`subscribe failed: ${e.message}`)) : resolve());
    });
    const done = new Promise<{ units: string; error?: string }>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error(`did not report a quiesce within ${timeoutMs} ms`)),
        timeoutMs);
      // The status topic is retained, so the device's state from BEFORE we asked arrives
      // too -- including any earlier quiesce, which reads as success if taken for ours.
      // Two things separate it from the answer, and neither is arrival time (losing that
      // race means ignoring the real answer and then timing out):
      //
      //   the RETAIN FLAG, which the broker sets only on the copy replayed at subscribe, so
      //   a retained message is by definition not a reply to a publish we had not made yet;
      //
      //   and the VALUE of last_quiesce_at_boot_s, the device's uptime when it last
      //   quiesced, which only increases. That rules out an out-of-band heartbeat published
      //   after we asked but before the units were out.
      //
      // The flag is what makes a device with no retained copy work: there, the first message
      // IS the answer, and a rule that spent the first message on a baseline would eat it.
      let baseline: number | undefined;
      client.on('message', (_topic, payload, packet) => {
        let doc: Record<string, unknown>;
        try {
          doc = JSON.parse(payload.toString()) as Record<string, unknown>;
        } catch {
          return; // a status we cannot read is not the one we are waiting for
        }
        const at = typeof doc.last_quiesce_at_boot_s === 'number'
          ? doc.last_quiesce_at_boot_s : undefined;
        if (packet.retain) {
          baseline = at;
          return;
        }
        if (at === undefined) return;
        if (baseline !== undefined && at <= baseline) return;
        clearTimeout(timer);
        resolve({
          units: String(doc.last_quiesce_units ?? ''),
          error: doc.last_quiesce_error ? String(doc.last_quiesce_error) : undefined,
        });
      });
    });
    await new Promise<void>((resolve, reject) => {
      client.publish(`rekon/pod/${node}/quiesce`, reason, { qos: 1, retain: false }, (e) =>
        e ? reject(new Error(`publish failed: ${e.message}`)) : resolve());
    });
    return await done;
  } finally {
    client.end(true);
  }
}

export async function requestReboot(cfg: BusConfig, node: string, reason: string): Promise<void> {
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
      // NOT RETAINED, which pod-link enforces by refusing a retained reboot: the device
      // comes back subscribed and would read the same instruction again, forever. QoS 1 so
      // it is not lost on a link that is about to carry nothing else.
      client.publish(`rekon/pod/${node}/reboot`, reason, { qos: 1, retain: false }, (e) =>
        e ? reject(new Error(`publish failed: ${e.message}`)) : resolve());
    });
  } finally {
    client.end(true);
  }
}

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
