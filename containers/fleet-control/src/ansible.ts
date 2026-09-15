// Drive a converge by running the playbook here, on the control node.
//
// `ansible-runner` rather than bare `ansible-playbook`: it emits a structured JSON event per
// task and per host -- name, host, ok/changed/failed -- so progress is parsed rather than
// scraped out of `-v` output, which is not a stable interface. It is Red Hat's own, actively
// maintained, and is the documented way to drive Ansible from something that is not Python.
//
// There is no SSH session held open here and nothing detached on the device: Ansible owns the
// connection, including the reboot-and-wait. What that does NOT solve is this process dying
// mid-converge -- the playbook stops with it, same as before. Losing the orchestrator is a
// real gap and it is not fixed by moving where Ansible runs.

import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/** Where the image keeps `host/ansible/**`. Only the playbook comes from here; see site.yaml. */
export const PLAYBOOK_DIR = process.env.FLEET_PLAYBOOK_DIR ?? '/app/ansible';

export type EventSink = (stream: 'stdout' | 'stderr', line: string) => void;

export interface ConvergeOptions {
  /** Address or hostname to converge. A bare `addr,` is a valid inventory. */
  host: string;
  /** Login account. */
  user: string;
  privateKeyPath: string;
  extraVars: Record<string, string | boolean>;
  /** Ansible's own connect timeout. Its default is 10s, which a loaded Zero misses. */
  sshTimeoutSec?: number;
  sink?: EventSink;
}

/** One line of ansible-runner's JSON event stream, as much of it as we use. */
interface RunnerEvent {
  event?: string;
  stdout?: string;
  event_data?: {
    task?: string;
    host?: string;
    res?: { changed?: boolean; msg?: string };
    playbook?: string;
  };
}

/**
 * Render an event as a line worth showing an operator.
 *
 * Deliberately not the raw stdout: `-v` prints each task's entire result object, which is how
 * one `docker.service` fact block becomes several kilobytes. The event type already says what
 * happened, so say that instead.
 */
function describe(e: RunnerEvent): string | null {
  const task = e.event_data?.task;
  const host = e.event_data?.host;
  switch (e.event) {
    case 'playbook_on_task_start':
      return task ? `TASK ${task}` : null;
    case 'runner_on_ok':
      return `  ok${e.event_data?.res?.changed ? ' (changed)' : ''}: ${host ?? ''} ${task ?? ''}`.trimEnd();
    case 'runner_on_failed':
      return `  FAILED: ${host ?? ''} ${task ?? ''}${e.event_data?.res?.msg ? ' -- ' + e.event_data.res.msg : ''}`;
    case 'runner_on_unreachable':
      return `  UNREACHABLE: ${host ?? ''}${e.event_data?.res?.msg ? ' -- ' + e.event_data.res.msg : ''}`;
    case 'runner_on_skipped':
      return null; // skips are noise; the recap carries the count
    case 'playbook_on_stats':
      return e.stdout?.trim() ? e.stdout.trimEnd() : 'PLAY RECAP';
    default:
      return null;
  }
}

/** Splits a byte stream into lines, holding a partial trailing line until it completes. */
function lineSplitter(emit: (line: string) => void): { push(c: Buffer): void; end(): void } {
  let held = '';
  return {
    push(chunk) {
      held += chunk.toString('utf8');
      let nl: number;
      while ((nl = held.indexOf('\n')) !== -1) {
        emit(held.slice(0, nl));
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

/**
 * Converge one host. Resolves with the playbook's exit code; **0 means converged**.
 *
 * Ansible reboots the device and waits for it when something actually changed, so there is no
 * retry dance here and nothing to interpret beyond the code.
 */
export async function converge(opts: ConvergeOptions): Promise<number> {
  // ansible-runner wants a private data directory: project/ holds the playbook, env/ the
  // settings and extra vars, inventory/ the hosts.
  const pdd = mkdtempSync(join(tmpdir(), 'fleet-converge-'));
  try {
    mkdirSync(join(pdd, 'env'), { recursive: true });
    mkdirSync(join(pdd, 'inventory'), { recursive: true });

    // A bare `addr,` is a valid inventory, so no inventory file or DNS is needed -- the same
    // property site.yaml documents for the command-line form.
    writeFileSync(join(pdd, 'inventory', 'hosts'), `${opts.host}\n`);

    writeFileSync(
      join(pdd, 'env', 'extravars'),
      JSON.stringify({ ansible_user: opts.user, ...opts.extraVars }, null, 2),
    );

    writeFileSync(
      join(pdd, 'env', 'settings'),
      JSON.stringify({
        // A Zero under a converge misses Ansible's 10s default; this is the value the
        // handshake failures on campod-se argued for.
        job_timeout: 0,
      }),
    );

    const args = [
      'run',
      pdd,
      '--project-dir',
      PLAYBOOK_DIR,
      '-p',
      'site.yaml',
      '--private-key',
      opts.privateKeyPath,
      '-j', // JSON events on stdout
    ];

    const child = spawn('ansible-runner', args, {
      env: {
        ...process.env,
        ANSIBLE_HOST_KEY_CHECKING: 'False',
        ANSIBLE_TIMEOUT: String(opts.sshTimeoutSec ?? 90),
        // Keep ansible's own text output out of the way; we render from events.
        ANSIBLE_STDOUT_CALLBACK: 'null',
      },
    });

    const out = lineSplitter((line) => {
      const trimmed = line.trim();
      if (trimmed.length === 0) return;
      try {
        const rendered = describe(JSON.parse(trimmed) as RunnerEvent);
        if (rendered !== null) opts.sink?.('stdout', rendered);
      } catch {
        // Not an event line -- runner's own chatter. Pass it through rather than hide it.
        opts.sink?.('stdout', trimmed);
      }
    });
    const err = lineSplitter((line) => {
      if (line.trim().length > 0) opts.sink?.('stderr', line);
    });

    child.stdout.on('data', (c: Buffer) => out.push(c));
    child.stderr.on('data', (c: Buffer) => err.push(c));

    return await new Promise<number>((resolve, reject) => {
      child.on('error', (e) => reject(new Error(`could not run ansible-runner: ${e.message}`)));
      child.on('close', (code) => {
        out.end();
        err.end();
        resolve(code ?? -1);
      });
    });
  } finally {
    rmSync(pdd, { recursive: true, force: true });
  }
}
