// Stop a device collecting, before asking it to do anything else.
//
// WHY AT ALL, which is policy rather than mechanism: almost anything you invoke on a box
// that is still collecting risks hanging. `docker ps` measured 2.1-22.0 s with capture
// running against 136-223 ms without (docs/pod-bus.md). So every command this service sends
// a device is preceded by a quiesce, including the read-only ones. Stopping is a goal in
// itself; it just must not take the control plane down with it.
//
// WHY OVER THE BUS. pod-link is resident and already connected, so stopping a device costs
// a publish rather than an sshd fork and a shell faulted in off the card that is the thing
// being contended for. That is what pod-link is for: control code that does not have to be
// read off a busy card.
//
// It stops only the units in POD_LINK_COLLECTION_UNITS, and pod-link is never among them --
// a stop that took out pod-link would kill the thing carrying the instruction to undo it.
//
// AND THE WAIT IS THE DEVICE'S. `StopUnit` returns when the job is accepted, not when the
// unit is gone; the camera takes around 19 s. pod-link polls systemd until each unit is
// actually inactive and then publishes, so this side waits for one message instead of
// round-tripping a busy box.

import { quiesceNode } from './bus.js';
import type { ActionContext } from './actions.js';

/**
 * Quiesce one node, or throw.
 *
 * `timeoutMs` is how long the device gets to report that its units are out; the default is
 * `quiesceNode`'s. A timeout is a failure, not a warning -- see below.
 *
 * NO SSH FALLBACK. A device that cannot be reached on the bus is not quiesced, and the
 * caller does not proceed -- Seth's call: "Fail, we can figure out what to do and I have ssh
 * anyway." Falling back would reintroduce the paging path as a silent default, which is the
 * thing being removed, and it would do so exactly when the device is least able to afford it.
 */
export async function quiesce(
  node: string,
  ctx: ActionContext,
  reason: string,
  timeoutMs?: number,
): Promise<void> {
  // Node-first, like every other per-device failure here, because these land in a run log
  // next to other nodes' lines.
  if (ctx.bus.url === '') {
    throw new Error(
      `${node}: cannot quiesce, no pod bus configured (FLEET_MQTT_URL). Every command this ` +
        'service sends a device is preceded by a quiesce, so nothing can be sent without one.',
    );
  }
  let res: { units: string; error?: string };
  try {
    res = await quiesceNode(ctx.bus, node, reason, timeoutMs);
  } catch (err) {
    throw new Error(`${node}: ${(err as Error).message}`);
  }
  if (res.error !== undefined && res.error !== '') {
    throw new Error(`${node}: did not quiesce cleanly: ${res.error}. Units: ${res.units}`);
  }
}
