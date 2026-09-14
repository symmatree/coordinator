// SSH transport: connect to a node, run a command, stream its output, return its exit code.
//
// `exec` returns the code rather than throwing on nonzero: `one_time.sh` uses exit 1 to ask
// for a reboot, so the caller decides what a code means.

import { NodeSSH } from 'node-ssh';
import { hostOf, type FleetNode } from './inventory.js';
import type { HostKeyStore, VerifyOutcome } from './hostkeys.js';

export interface ExecResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

export type LineSink = (stream: 'stdout' | 'stderr', line: string) => void;

export class HostKeyMismatchError extends Error {
  constructor(readonly node: string, readonly presented: string, readonly expected: string) {
    super(`${node}: host key changed (presented ${presented}, expected ${expected}).`);
    this.name = 'HostKeyMismatchError';
  }
}

/** Splits a byte stream into lines, holding a partial trailing line until it completes. */
function lineSplitter(emit: (line: string) => void): { push(chunk: Buffer): void; end(): void } {
  let held = '';
  return {
    push(chunk) {
      held += chunk.toString('utf8');
      let nl: number;
      while ((nl = held.indexOf('\n')) !== -1) {
        emit(held.slice(0, nl).replace(/\r$/, ''));
        held = held.slice(nl + 1);
      }
    },
    end() {
      if (held.length > 0) {
        emit(held);
        held = '';
      }
    },
  };
}

export interface SessionOptions {
  /** Login account, fleet-wide. */
  user: string;
  privateKeyPath: string;
  hostKeys: HostKeyStore;
  /** Connect timeout, ms. */
  timeoutMs?: number;
}

/** One live SSH connection to one node. */
export class NodeSession {
  private constructor(
    readonly node: FleetNode,
    private readonly ssh: NodeSSH,
    readonly hostKey: VerifyOutcome,
  ) {}

  static async open(node: FleetNode, opts: SessionOptions): Promise<NodeSession> {
    const ssh = new NodeSSH();
    let outcome: VerifyOutcome | undefined;

    await ssh.connect({
      host: hostOf(node),
      username: opts.user,
      privateKeyPath: opts.privateKeyPath,
      readyTimeout: opts.timeoutMs ?? 15_000,
      hostVerifier: (key: Buffer) => {
        outcome = opts.hostKeys.verify(node.name, key);
        return outcome.ok;
      },
    });

    if (outcome && !outcome.ok) {
      ssh.dispose();
      throw new HostKeyMismatchError(node.name, outcome.fingerprint, outcome.expected);
    }
    if (!outcome) {
      ssh.dispose();
      throw new Error(`${node.name}: host key was never presented for verification`);
    }

    return new NodeSession(node, ssh, outcome);
  }

  /** Run a command. Never throws on a nonzero exit -- the code is the answer. */
  async exec(command: string, onLine?: LineSink): Promise<ExecResult> {
    const outSplit = lineSplitter((l) => onLine?.('stdout', l));
    const errSplit = lineSplitter((l) => onLine?.('stderr', l));

    const res = await this.ssh.execCommand(command, {
      onStdout: (c) => outSplit.push(c),
      onStderr: (c) => errSplit.push(c),
    });
    outSplit.end();
    errSplit.end();

    return { code: res.code, stdout: res.stdout, stderr: res.stderr };
  }

  /** Current boot id. Changes on every boot, so it is how we know a reboot actually happened. */
  async bootId(): Promise<string> {
    const r = await this.exec('cat /proc/sys/kernel/random/boot_id');
    if (r.code !== 0) throw new Error(`${this.node.name}: could not read boot_id: ${r.stderr}`);
    return r.stdout.trim();
  }

  dispose(): void {
    this.ssh.dispose();
  }
}

/** Open a session, run `fn`, always close. */
export async function withSession<T>(
  node: FleetNode,
  opts: SessionOptions,
  fn: (s: NodeSession) => Promise<T>,
): Promise<T> {
  const s = await NodeSession.open(node, opts);
  try {
    return await fn(s);
  } finally {
    s.dispose();
  }
}

export async function reachable(node: FleetNode, opts: SessionOptions): Promise<boolean> {
  try {
    await withSession(node, opts, async (s) => s.exec('true'));
    return true;
  } catch {
    return false;
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Reboot and wait for the node to come back as a different boot. Comparing boot ids avoids
 * reconnecting to the old system, which is still answering for a few seconds after the call.
 */
export async function rebootAndWait(
  node: FleetNode,
  opts: SessionOptions,
  priorBootId: string,
  onLine?: LineSink,
  timeoutMs = 300_000,
): Promise<void> {
  onLine?.('stdout', `[fleet-control] rebooting ${node.name}`);
  try {
    await withSession(node, opts, async (s) => s.exec('sudo -n systemctl reboot'));
  } catch {
    // Expected: the connection dies as the host goes down. Not an error.
  }

  const deadline = Date.now() + timeoutMs;
  let announced = false;
  while (Date.now() < deadline) {
    await sleep(5_000);
    try {
      const id = await withSession(node, opts, async (s) => s.bootId());
      if (id !== priorBootId) {
        onLine?.('stdout', `[fleet-control] ${node.name} is back (boot ${id.slice(0, 8)})`);
        return;
      }
      if (!announced) {
        onLine?.('stdout', `[fleet-control] ${node.name} still answering on the old boot, waiting`);
        announced = true;
      }
    } catch {
      // Down, or not up yet. Keep waiting.
    }
  }
  throw new Error(
    `${node.name}: did not come back within ${Math.round(timeoutMs / 1000)}s of the reboot.`,
  );
}
