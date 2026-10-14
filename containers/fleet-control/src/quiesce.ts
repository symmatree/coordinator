// Stop a device collecting, before asking it to do anything else.
//
// WHY AT ALL, which is policy rather than mechanism: almost anything you invoke on a box
// that is still collecting risks hanging. `docker ps` measured 2.1-22.0 s with capture
// running against 136-223 ms without (docs/pod-bus.md). So every command this service sends
// a device is preceded by a quiesce, including the read-only ones. Stopping is a goal in
// itself; it just must not take the control plane down with it.
//
// WHY OVER THE BUS. This used to be `sudo pkill -x -TERM dumb-init` plus a 40 s poll loop,
// sent over ssh -- so to free memory on a thrashing box we first forked an sshd on it, and
// then polled it from the far end of that connection. That is the paging cost arriving at
// exactly the worst moment, which is the whole reason pod-link exists: resident control code
// that does not have to be read off the card when the card is busy.
//
// `pkill -x dumb-init` was also too broad. It matches EVERY container init, so it stopped the
// stack rather than the collection -- pod-link and the broker included, which is to say it
// killed the thing that carries the instruction to undo it. The bus quiesce stops only the
// units named in POD_LINK_COLLECTION_UNITS, and pod-link is deliberately never among them.
//
// AND THE WAIT IS THE DEVICE'S. `StopUnit` returns when the job is accepted, not when the
// unit is gone -- the camera took 19.0 s to exit after the old command had already returned.
// pod-link polls systemd until each unit is actually inactive and then publishes, so this
// side waits for one message instead of round-tripping a busy box.

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
