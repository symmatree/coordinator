// SSH transport: connect to a fleet node, run a command, stream its output, get its exit code.
//
// Exit codes are the interface, not a detail. `host/one_time.sh` exits 1 to mean "I installed
// a kernel/firmware change, reboot me and run me again" -- a NORMAL path, not a failure. Any
// layer that collapses that to "nonzero means broken" makes a working bootstrap look broken,
// which is the specific failure coordinator#236 calls out. So `exec` returns the code and
// lets the caller decide.
//
// Output streams. `coord pull` moves hundreds of MB and takes minutes; buffering it until the
// process exits gives the operator nothing to watch and no way to tell "slow" from "stuck".
// Every exec emits lines as they arrive.

import { NodeSSH } from 'node-ssh';
import type { FleetNode } from './inventory.js';
import type { HostKeyStore, VerifyOutcome } from './hostkeys.js';

export interface ExecResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

export type LineSink = (stream: 'stdout' | 'stderr', line: string) => void;

export class HostKeyMismatchError extends Error {
  constructor(readonly node: string, readonly presented: string, readonly expected: string) {
    super(
      `${node}: host key changed (presented ${presented}, expected ${expected}). ` +
        `If you reflashed this card, forget its recorded key and retry; otherwise stop and investigate.`,
    );
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
      host: node.address,
      username: node.user,
      privateKeyPath: opts.privateKeyPath,
      readyTimeout: opts.timeoutMs ?? 15_000,
      // Sync verifier: ssh2 hands us the raw public-key blob and we answer yes/no.
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
      // hostVerifier not invoked means we did not actually check. Fail rather than proceed
      // on an assumption about ssh2's internals.
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
 * Reboot a node and wait for it to come back as a DIFFERENT boot.
 *
 * `priorBootId` closes the race that makes naive "wait for ssh" loops flaky: for the first
 * seconds after the reboot command the old system is still up and still answering, so a
 * reconnect can succeed against the host we are trying to restart and report success without
 * a reboot having happened. Comparing boot ids makes the check positive rather than temporal.
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
    `${node.name}: did not come back within ${Math.round(timeoutMs / 1000)}s of the reboot. ` +
      `It may still be booting, or the boot may have failed -- a failed first boot powers the ` +
      `board off (coordinator#236), which looks identical to a hang from here. Check HDMI on the ` +
      `coordinator, serial on a campod.`,
  );
}
