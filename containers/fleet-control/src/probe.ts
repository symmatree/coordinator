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
import { promisify } from 'node:util';
import { hostOf, type FleetNode } from './inventory.js';
import { parseProbe, type Probe } from './manifest.js';
import type { ActionContext } from './actions.js';

const run = promisify(execFile);

/** What the device prints. `coord` is on PATH at /usr/local/bin (coordinator#327). */
export const PROBE_COMMAND = 'coord version';

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
      { maxBuffer: 4 * 1024 * 1024, timeout: (ctx.sshTimeoutSec + 30) * 1000 },
    );
    return { ...base, probe: parseProbe(stdout) };
  } catch (err) {
    const e = err as { stderr?: string; message?: string };
    const why = (e.stderr ?? '').trim() || e.message || 'ssh failed';
    return { ...base, error: why.split('\n').slice(-2).join(' ').slice(0, 300) };
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
