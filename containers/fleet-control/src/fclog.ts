// The FC's dataflash logs, as a third thing the operator can pick alongside capture sessions.
//
// Same split as sessions (coordinator#302): the DEVICE reports what the FC holds, the GROUND
// decides which one is the flight. Not a style choice -- `time_utc` is LAST-MODIFIED rather
// than creation, so which log is the flight resolves against the armed window, and the armed
// window comes from the mavproxy console on the cluster's own clock. The device cannot answer
// it.
//
// Only the coordinator has an FC: it is the only thing wired to /dev/ttyAMA0. The port must be
// free -- coordinator-mavlink holds it while the stack is up, and two readers on one UART get
// half a byte stream each -- so every command here is quiesced like the rest.
//
// Device side is coordinator#384 and #395.

import { spawn } from 'node:child_process';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { rename } from 'node:fs/promises';
import { promisify } from 'node:util';
import { hostOf, type FleetNode } from './inventory.js';
import { quiesced } from './quiesce.js';
import type { ActionContext } from './actions.js';

const run = promisify(execFile);

/** One log as the FC describes it. */
export interface FcLog {
  id: number;
  bytes: number;
  /**
   * The FC's own field, and it is LAST-MODIFIED, NOT CREATION. With `LOG_FILE_DSRMROT=1` the
   * flight's log is the one stamped a few seconds after the disarm, because that rotation is
   * what closed it. Null when the FC had no GPS time when the log was last written.
   *
   * Anything rendering this must not call it a creation time; the operator picks the wrong log.
   */
  time_utc: string | null;
}

function sshArgs(node: FleetNode, ctx: ActionContext, command: string): string[] {
  return [
    '-i', ctx.privateKeyPath,
    '-o', 'BatchMode=yes',
    '-o', 'StrictHostKeyChecking=accept-new',
    '-o', `UserKnownHostsFile=${ctx.knownHostsPath}`,
    '-o', `ConnectTimeout=${ctx.sshTimeoutSec}`,
    `${ctx.inventory.user}@${hostOf(node)}`,
    command,
  ];
}

function describe(node: FleetNode, err: unknown, gaveUpAfterSec: number): Error {
  const e = err as { stderr?: string; message?: string; killed?: boolean };
  if (e.killed === true) return new Error(`${node.name}: coord fc-log gave up after ${gaveUpAfterSec}s`);
  const said = (e.stderr ?? '').trim() || e.message || 'ssh failed';
  return new Error(`${node.name}: ${said.split('\n').slice(-2).join(' ').slice(0, 300)}`);
}

/**
 * What the FC holds. JSON on stdout; no flag, because a tool only a web API calls has no
 * reason to render twice.
 *
 * Do not assume a short list: 56 logs on the box as of 2026-09-26, several zero-byte, against
 * a `LOG_MAX_FILES` of 500.
 */
export async function listLogs(node: FleetNode, ctx: ActionContext): Promise<FcLog[]> {
  const timeoutSec = 180;
  try {
    const { stdout } = await run('ssh', sshArgs(node, ctx, quiesced('sudo coord fc-log list')), {
      maxBuffer: 4 * 1024 * 1024,
      timeout: timeoutSec * 1000,
    });
    return (JSON.parse(stdout) as { logs: FcLog[] }).logs;
  } catch (err) {
    throw describe(node, err, timeoutSec);
  }
}

/** The `done` event, which carries the digest of what the device actually sent. */
interface DoneEvent {
  event: 'done';
  id: number;
  bytes: number;
  sha256: string;
  seconds: number;
}

/** Split a stream into lines and hand each to `onLine`. */
function lines(onLine: (line: string) => void): { push: (c: Buffer) => void; end: () => void } {
  let buf = '';
  return {
    push: (c) => {
      buf += c.toString('utf8');
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        onLine(buf.slice(0, i));
        buf = buf.slice(i + 1);
      }
    },
    end: () => { if (buf.trim()) onLine(buf); },
  };
}

/**
 * Stream one log off the FC straight into `destPath`, and verify it against the device's digest.
 *
 * `pull` writes the LOG BYTES to stdout and its own progress to stderr as JSON Lines, so there
 * is no file on the device and no path for this side to invent -- which also means one transfer
 * rather than a serial download followed by a copy.
 *
 * Written to `.part` and renamed only after the digest matches, so an interrupted pull never
 * leaves something that looks like a log. `error` means a window could not be completed: the
 * stream is short and the file must not be filed.
 *
 * Progress is throttled. At the measured 84 KiB/s a 148 MB log is about half an hour, and the
 * device emits progress far faster than a run log should carry it.
 */
export async function fetchLog(
  node: FleetNode,
  ctx: ActionContext,
  id: number,
  destPath: string,
  opts: { note?: (line: string) => void; progressEverySec?: number } = {},
): Promise<{ bytes: number; sha256: string }> {
  if (!Number.isInteger(id) || id < 0) throw new Error(`not a log id: ${id}`);
  const say = opts.note ?? (() => {});
  const everyMs = (opts.progressEverySec ?? 30) * 1000;
  const part = `${destPath}.part`;

  const child = spawn('ssh', sshArgs(node, ctx, quiesced(`sudo coord fc-log pull ${id}`)));

  const hash = createHash('sha256');
  let bytes = 0;
  let done: DoneEvent | undefined;
  let failed: string | undefined;
  let lastNote = 0;

  const err = lines((line) => {
    let ev: { event?: string; message?: string; rate_kib_s?: number; eta_s?: number; sent?: number; bytes?: number };
    try {
      ev = JSON.parse(line) as typeof ev;
    } catch {
      // Not ours. The quiesce prefix runs `sudo pkill` first and may say something; passing
      // that through is better than failing on it.
      if (line.trim()) say(`  ${line.trim().slice(0, 200)}`);
      return;
    }
    if (ev.event === 'start') say(`${node.name}: FC log ${id}, ${((ev.bytes ?? 0) / 1e6).toFixed(0)} MB to come`);
    else if (ev.event === 'progress') {
      const now = Date.now();
      if (now - lastNote < everyMs) return;
      lastNote = now;
      const pct = ev.bytes ? Math.round(((ev.sent ?? 0) / ev.bytes) * 100) : 0;
      say(`${node.name}: ${pct}% at ${ev.rate_kib_s ?? '?'} KiB/s, ${Math.round((ev.eta_s ?? 0) / 60)} min left`);
    } else if (ev.event === 'done') done = ev as unknown as DoneEvent;
    else if (ev.event === 'error') failed = ev.message ?? 'pull failed';
  });

  const sink = createWriteStream(part);
  child.stdout.on('data', (c: Buffer) => { bytes += c.length; hash.update(c); sink.write(c); });
  child.stderr.on('data', (c: Buffer) => err.push(c));

  const code = await new Promise<number>((resolve, reject) => {
    child.on('error', (e) => reject(new Error(`${node.name}: could not run ssh: ${e.message}`)));
    child.on('close', (c) => { err.end(); sink.end(() => resolve(c ?? -1)); });
  });

  if (failed !== undefined) throw new Error(`${node.name}: FC log ${id} incomplete -- ${failed}. Not filed.`);
  if (code !== 0) throw new Error(`${node.name}: coord fc-log pull ${id} exited ${code}. Not filed.`);
  if (done === undefined) {
    throw new Error(`${node.name}: FC log ${id} ended without a 'done' event, so there is nothing to verify against. Not filed.`);
  }

  const got = hash.digest('hex');
  if (got !== done.sha256 || bytes !== done.bytes) {
    throw new Error(
      `${node.name}: FC log ${id} did not survive the transfer -- device said ${done.sha256.slice(0, 12)} / ${done.bytes} bytes, got ${got.slice(0, 12)} / ${bytes}. Not filed.`,
    );
  }
  await rename(part, destPath);
  return { bytes, sha256: got };
}

/**
 * Where the .bin belongs in a flight directory.
 *
 * `docs/flight-data-layout.md` puts it at the flight ROOT -- "`<fc-log>.bin` -- SOURCE: the FC
 * dataflash log (one per flight)". `docs/post-flight-collection.md` used `<flight>/fc/` on the
 * day; the two disagree and the layout doc is the one that calls itself canonical.
 */
export const fcLogName = (id: number): string => `fc-log-${id}.bin`;
