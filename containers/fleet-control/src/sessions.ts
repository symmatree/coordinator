// Post-flight: what is on a device, and getting it off.
//
// The device owns what a session IS -- listing, packaging and deleting are `coord sessions`
// (coordinator#344), installed next to `coord-version`. This drives those over the same SSH
// path everything else uses, and owns the half they deliberately do not: moving the bundle,
// verifying it landed intact, and putting it somewhere with the other nodes' contributions.
//
// Deleting is a DEVICE command taking session ids, not an `rm -rf` composed here. That is the
// point: the path is resolved where the data lives, and an id containing a separator is
// refused there rather than interpreted here.

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { hostOf, type FleetNode } from './inventory.js';
import type { ActionContext } from './actions.js';

const run = promisify(execFile);

/** One capture session as the device describes it. */
export interface Session {
  node: string;
  /** The kernel boot id. No clock in it -- see first_utc/last_utc. */
  session: string;
  path: string;
  /** True for the current boot. Cannot be packaged; capture would be writing to it. */
  open: boolean;
  bytes: number;
  frames: number;
  accel: string[];
  manifests: string[];
  /** From the first and last sidecar. Null when a session has no frames at all. */
  first_utc: string | null;
  last_utc: string | null;
}

/** What `package` reports: where the bundle is and what it should hash to. */
export interface Bundle {
  node: string;
  session: string;
  bundle: string;
  bytes: number;
  sha256: string;
  seconds: number;
}

export interface DeleteResult {
  deleted: Array<{
    session: string;
    /** Path removed, or the literal `absent` -- delete is idempotent by design. */
    session_dir: string;
    bundle: string;
    bytes: number;
  }>;
  bytes: number;
}

/**
 * Run one `coord sessions` subcommand and parse its JSON.
 *
 * Errors carry the device's stderr where there is any. A command that fails here is a
 * per-node outcome the caller reports, not an exception that empties a fleet-wide operation.
 */
async function coordSessions<T>(
  node: FleetNode,
  ctx: ActionContext,
  args: string[],
  timeoutSec: number,
): Promise<T> {
  const host = hostOf(node);
  try {
    const { stdout } = await run(
      'ssh',
      [
        '-i', ctx.privateKeyPath,
        '-o', 'BatchMode=yes',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', `UserKnownHostsFile=${ctx.knownHostsPath}`,
        '-o', `ConnectTimeout=${ctx.sshTimeoutSec}`,
        `${ctx.inventory.user}@${host}`,
        'coord', 'sessions', ...args,
      ],
      // Packaging a session is minutes of gzip on a Zero, so this is the caller's to set.
      { maxBuffer: 16 * 1024 * 1024, timeout: timeoutSec * 1000 },
    );
    return JSON.parse(stdout) as T;
  } catch (err) {
    const e = err as { stderr?: string; message?: string; killed?: boolean };
    if (e.killed === true) {
      throw new Error(`${node.name}: coord sessions ${args[0]} gave up after ${timeoutSec}s`);
    }
    const why = (e.stderr ?? '').trim() || e.message || 'ssh failed';
    throw new Error(`${node.name}: ${why.split('\n').slice(-2).join(' ').slice(0, 300)}`);
  }
}

/** Every session on a device. Capture should already be stopped; this does not stop it. */
export async function listSessions(node: FleetNode, ctx: ActionContext): Promise<Session[]> {
  const out = await coordSessions<{ sessions: Session[] }>(node, ctx, ['list'], 120);
  return out.sessions;
}

/**
 * Package one session on the device.
 *
 * Generous timeout: this is gzip over hundreds of megabytes on a 512 MB Zero, and the device
 * checks it has room before writing anything. Long is expected; the bound is against a hang.
 */
export async function packageSession(
  node: FleetNode,
  ctx: ActionContext,
  session: string,
  timeoutSec = 1800,
): Promise<Bundle> {
  return coordSessions<Bundle>(node, ctx, ['package', session], timeoutSec);
}

/**
 * Delete sessions and their bundles.
 *
 * Idempotent on the device: an absent session reports `absent` rather than failing, so a
 * retried transfer cannot fail on work it already finished.
 */
export async function deleteSessions(
  node: FleetNode,
  ctx: ActionContext,
  sessions: string[],
): Promise<DeleteResult> {
  if (sessions.length === 0) return { deleted: [], bytes: 0 };
  return coordSessions<DeleteResult>(node, ctx, ['delete', ...sessions], 300);
}

/**
 * Stop capture on a device, without converging it.
 *
 * The post-flight flow needs a quiet machine before it enumerates: capture writes ~6 GB/hour
 * and a session that is still growing is one whose size and span change while the operator
 * reads them. A converge stops capture too, but converging to stop capture is twenty minutes
 * of apt to achieve a `docker compose stop`.
 *
 * `coord stop` is not a sticky off -- the boot unit's ExecStart is unconditional (#256) -- so
 * a power cycle brings the stack back and nothing has to remember to undo this.
 */
export async function stopCapture(node: FleetNode, ctx: ActionContext): Promise<void> {
  const host = hostOf(node);
  try {
    await run(
      'ssh',
      [
        '-i', ctx.privateKeyPath,
        '-o', 'BatchMode=yes',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', `UserKnownHostsFile=${ctx.knownHostsPath}`,
        '-o', `ConnectTimeout=${ctx.sshTimeoutSec}`,
        `${ctx.inventory.user}@${host}`,
        `COORD_STACK=${node.role} coord stop`,
      ],
      // Bounded, because this is the step that has hung before (#281): a wedged docker leaves
      // `compose stop` waiting on a container that never exits.
      { maxBuffer: 1024 * 1024, timeout: 180_000 },
    );
  } catch (err) {
    const e = err as { stderr?: string; message?: string; killed?: boolean };
    if (e.killed === true) throw new Error(`${node.name}: coord stop gave up after 180s`);
    const why = (e.stderr ?? '').trim() || e.message || 'ssh failed';
    throw new Error(`${node.name}: ${why.split('\n').slice(-2).join(' ').slice(0, 300)}`);
  }
}
