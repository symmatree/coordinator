// Ask a machine what it is running.
//
// One command, one SSH round trip, on demand. Not polled: nothing should touch the fleet
// while it is flying, and the probe is cheap enough that a refresh button is the whole
// scheduling policy (coordinator#326).
//
// Shells out to the ssh binary rather than using an SSH library, for the same reason Ansible
// does: one trust policy. The host-key check, the known_hosts file and the key are the same
// ones a converge uses, so there is not a second implementation with its own opinions about
// which hosts are acceptable.

import { execFile } from 'node:child_process';
import { connect } from 'node:net';
import { promisify } from 'node:util';
import { quiesced } from './quiesce.js';
import { hostOf, type FleetNode } from './inventory.js';
import { parseProbe, type Probe } from './manifest.js';
import type { ActionContext } from './actions.js';

const run = promisify(execFile);

/**
 * How long to wait for a TCP connection before calling a machine unreachable.
 *
 * Short on purpose. Accepting a connection is kernel-side and stays cheap on a machine that
 * is merely busy -- it is the BANNER that is userspace and slow, which is how a loaded Zero
 * can complete a handshake and then take a minute to say hello. So this separates "off the
 * network" from "on but working hard", where shortening ssh's own ConnectTimeout would
 * conflate them, because that bounds the banner too.
 */
const REACH_TIMEOUT_MS = 4000;

/**
 * Can we open a TCP connection to sshd at all?
 *
 * This is a NECESSARY CONDITION for the probe, not a guess at one: ssh needs this same
 * connection, so a failure here is proof the probe cannot succeed rather than a prediction
 * that it might not. It is worth doing because the alternative is waiting out a 120s timeout
 * for a machine that is simply switched off, and these get switched off constantly.
 *
 * It deliberately does NOT try to conclude anything from success.
 */
async function canConnect(host: string, port = 22): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = connect({ host, port });
    const done = (ok: boolean): void => {
      sock.destroy();
      resolve(ok);
    };
    sock.setTimeout(REACH_TIMEOUT_MS);
    sock.once('connect', () => done(true));
    sock.once('timeout', () => done(false));
    sock.once('error', () => done(false));
  });
}

/**
 * What the device prints. `coord` is on PATH at /usr/local/bin (coordinator#327).
 *
 * Quiesced: probing a capturing campod takes minutes, because `coord version` makes five
 * dockerd round-trips. `coord version` itself stays read-only -- the stop is a separate
 * statement, not something the verb does.
 */
export const PROBE_COMMAND = quiesced('coord version');

export interface NodeStatus {
  node: string;
  host: string;
  role: string;
  /** When we asked. Stamped here rather than on the device, which would only show clock skew. */
  probedAt: string;
  /** Present when the machine answered. */
  probe?: Probe;
  /**
   * Present when it did not. A machine that cannot be reached is a status, not an exception:
   * the screen shows it as unreachable alongside the ones that answered.
   */
  error?: string;
}

/**
 * Say what happened, and do not guess at why.
 *
 * A killed process has EMPTY stderr, so the old fallback reported node-js's generic
 * `Command failed: ssh ...` -- which reads like the device refused us, when in fact it
 * answered nothing within our own timeout. Those want different responses, so the
 * distinction is worth drawing.
 *
 * What is NOT worth drawing is a conclusion. A machine that does not answer may be powered
 * off, mid-reboot, busy with someone working on it, or genuinely wedged, and nothing here can
 * tell those apart. Report the observation and the elapsed time; the operator knows which of
 * their machines they just unplugged.
 */
function describeFailure(err: unknown, elapsedMs: number, timeoutMs: number): string {
  const e = err as { stderr?: string; message?: string; killed?: boolean; signal?: string };
  const secs = Math.round(elapsedMs / 1000);
  if (e.killed === true || e.signal != null) {
    return `no answer within ${Math.round(timeoutMs / 1000)}s (gave up after ${secs}s)`;
  }
  const stderr = (e.stderr ?? '').trim();
  if (stderr.length > 0) return `${stderr.split('\n').slice(-2).join(' ').slice(0, 280)} (after ${secs}s)`;
  return `${(e.message ?? 'ssh failed').slice(0, 200)} (after ${secs}s)`;
}

/**
 * Probe one node.
 *
 * Never throws. Unreachable, no `coord`, or unparseable output all resolve to a status
 * carrying the reason -- one bad machine should not empty the screen for the rest.
 */
export async function probeNode(node: FleetNode, ctx: ActionContext): Promise<NodeStatus> {
  const host = hostOf(node);
  const base: Omit<NodeStatus, 'probe' | 'error'> = {
    node: node.name,
    host,
    role: node.role,
    probedAt: new Date().toISOString(),
  };
  const timeoutMs = (ctx.sshTimeoutSec + 30) * 1000;
  const startedMs = Date.now();

  // Machines here are switched off, rebooted and worked on as a matter of course. Finding
  // that out in four seconds rather than two minutes is the difference between a refresh
  // that is usable with half the fleet down and one that is not.
  if (!(await canConnect(host))) {
    return {
      ...base,
      error: `nothing listening on ${host}:22 within ${REACH_TIMEOUT_MS / 1000}s`,
    };
  }

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
        PROBE_COMMAND,
      ],
      // A probe is small. This bounds a device that connects and then says nothing, which is
      // a shape we have actually seen -- the cap is on output, the timeout below on the wait.
      { maxBuffer: 4 * 1024 * 1024, timeout: timeoutMs },
    );
    return { ...base, probe: parseProbe(stdout) };
  } catch (err) {
    return { ...base, error: describeFailure(err, Date.now() - startedMs, timeoutMs) };
  }
}

/**
 * Probe every node at once.
 *
 * Concurrent because they are independent and the screen wants all of them; a slow machine
 * delays only itself. Nothing here writes to a device, so there is no reason to serialise.
 */
export async function probeAll(nodes: FleetNode[], ctx: ActionContext): Promise<NodeStatus[]> {
  return Promise.all(nodes.map((n) => probeNode(n, ctx)));
}
