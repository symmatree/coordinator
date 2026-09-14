// Run a long command on a node DETACHED from the SSH session that started it.
//
// Why this is not optional. `one_time.sh` takes ~13 minutes and `coord pull` ~4; this service
// is a pod, and a pod gets evicted, rescheduled and rolled. If the command is a child of the
// SSH channel, losing the pod sends SIGHUP into the middle of an apt transaction or an Ansible
// run, and "the script is re-runnable" is a hope rather than a property -- a half-applied
// package install is not a state any re-run is guaranteed to fix.
//
// So: the node owns the work. `setsid` puts the command in its own session with its output on
// a file, and the SSH exec that launched it returns immediately. The service then *follows*
// that file over short-lived connections. Nothing long-running is held open, which also means
// no SSH keepalive is needed and a quiet command cannot idle the link out.
//
// The same mechanism gives re-attach for free: a fresh pod finds the run still going and
// picks up the log where it left off, instead of starting a second one on top of the first.

import type { FleetNode } from './inventory.js';
import type { LineSink, SessionOptions } from './ssh.js';
import { withSession, HostKeyMismatchError } from './ssh.js';
import { makeProgressCollapser } from './progress.js';

/** Scratch, not state: /tmp is the right home and a reboot legitimately ends any run. */
const RUNDIR = '/tmp/fleet-control';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export interface Limits {
  /**
   * How long the node may be UNREACHABLE before we stop following, ms.
   *
   * Tolerating this is the entire point of detaching. The work belongs to the node, so a
   * dropped link, a WiFi flap or a reboot is survivable -- failing on the first missed poll
   * would throw away exactly the property we detached to get. Observed 2026-09-13: campod-se
   * went unreachable mid-`coord pull`; the earlier inline implementation hung forever rather
   * than recovering or failing.
   */
  unreachableToleranceMs?: number;
  /** Overall ceiling for the command, ms. Something has to bound this. */
  deadlineMs?: number;
}

export interface DetachedResult {
  code: number;
  /** True when we attached to something already running rather than starting it. */
  attached: boolean;
}

/** One short command on the node. Injected so the follow loop is testable without a network. */
export type Runner = (command: string) => Promise<{ code: number | null; stdout: string; stderr: string }>;

const sshRunner =
  (node: FleetNode, opts: SessionOptions): Runner =>
  (command) =>
    withSession(node, opts, (s) => s.exec(command));

/**
 * Start `command` detached under `name`, or attach to it if it is already running, and stream
 * its output until it exits. Returns its real exit code.
 *
 * `pollMs` is how often a short connection is made to collect new output -- not a timeout.
 * There is deliberately no overall deadline here: the caller decides what "too long" means.
 */
export async function runDetached(
  node: FleetNode,
  opts: SessionOptions,
  name: string,
  command: string,
  sink?: LineSink,
  pollMs = 5_000,
  runner?: Runner,
  limits: Limits = {},
): Promise<DetachedResult> {
  const run: Runner = runner ?? sshRunner(node, opts);
  const dir = `${RUNDIR}/${name}`;
  // base64 so the command crosses two shells without any quoting rules applying to it.
  const b64 = Buffer.from(command, 'utf8').toString('base64');

  const start = await run(
    `set -u
mkdir -p ${dir}
if [ -f ${dir}/pid ] && kill -0 "$(cat ${dir}/pid)" 2>/dev/null; then
  echo ATTACHED
else
  rm -f ${dir}/status ${dir}/log ${dir}/pid
  printf '%s' '${b64}' | base64 -d > ${dir}/cmd.sh
  # setsid: new session, so closing this channel cannot SIGHUP the work.
  setsid bash -c 'bash ${dir}/cmd.sh > ${dir}/log 2>&1; echo $? > ${dir}/status' </dev/null >/dev/null 2>&1 &
  echo "$!" > ${dir}/pid
  echo STARTED
fi`,
  );

  const attached = start.stdout.includes('ATTACHED');
  if (start.code !== 0) {
    throw new Error(`${node.name}: could not start detached '${name}' (exit ${start.code}): ${start.stderr.trim()}`);
  }
  sink?.('stdout', `[fleet-control] ${attached ? 'attached to running' : 'started'} '${name}' on ${node.name} (detached)`);

  const tolerance = limits.unreachableToleranceMs ?? 10 * 60_000;
  const ceilingMs = limits.deadlineMs ?? 60 * 60_000;
  const deadline = Date.now() + ceilingMs;

  let offset = 0;
  let unreachableSince: number | null = null;
  // Docker emits one line per progress redraw when there is no TTY; collapse them so real
  // output is not buried. See src/progress.ts.
  const collapse = makeProgressCollapser();
  for (;;) {
    await sleep(pollMs);

    if (Date.now() > deadline) {
      throw new Error(
        `${node.name}: '${name}' is still going past its ${Math.round(ceilingMs / 60_000)} minute ` +
          `ceiling. It has NOT been stopped -- it is detached and still on the node. Probe the ` +
          `node and decide; do not assume it failed.`,
      );
    }
    // One short connection per poll: read new bytes, then ask whether it finished. Order
    // matters -- read first, so a command that exits between the two calls does not lose its
    // final lines.
    let tick;
    try {
      tick = await run(
        `tail -c +${offset + 1} ${dir}/log 2>/dev/null; echo "___FC_EOF___"; cat ${dir}/status 2>/dev/null`,
      );
      if (unreachableSince !== null) {
        sink?.('stdout', `[fleet-control] ${node.name} is answering again; the work kept running`);
        unreachableSince = null;
      }
    } catch (err) {
      if (err instanceof HostKeyMismatchError) throw err; // never transient
      const now = Date.now();
      unreachableSince ??= now;
      const downFor = Math.round((now - unreachableSince) / 1000);
      if (now - unreachableSince > tolerance) {
        throw new Error(
          `${node.name}: unreachable for ${downFor}s while '${name}' was running, past the ` +
            `${Math.round(tolerance / 60_000)} minute tolerance. The command was detached, so it ` +
            `may have finished, may still be running, or may have died with the node -- its state ` +
            `is UNKNOWN. Probe the node before acting on it. (${(err as Error).message})`,
        );
      }
      sink?.('stdout', `[fleet-control] ${node.name} not answering (${downFor}s) -- work is detached, still waiting`);
      continue;
    }

    const marker = tick.stdout.indexOf('___FC_EOF___');
    const chunk = marker === -1 ? tick.stdout : tick.stdout.slice(0, marker);
    const status = marker === -1 ? '' : tick.stdout.slice(marker + '___FC_EOF___'.length).trim();

    if (chunk.length > 0) {
      offset += Buffer.byteLength(chunk, 'utf8');
      for (const raw of chunk.split('\n')) {
        if (raw.length === 0) continue;
        const line = collapse(raw.replace(/\r$/, ''));
        if (line !== null) sink?.('stdout', line);
      }
    }

    if (status !== '') {
      const code = Number(status);
      if (!Number.isFinite(code)) {
        throw new Error(`${node.name}: '${name}' wrote an unreadable status: ${JSON.stringify(status)}`);
      }
      return { code, attached };
    }

    // The process is gone but no status was written: the node rebooted, was killed, or the
    // work died in a way that skipped the trailing `echo $?`. Say that, rather than polling
    // forever against a file nothing will ever update.
    const alive = await run(
      `[ -f ${dir}/pid ] && kill -0 "$(cat ${dir}/pid)" 2>/dev/null && echo ALIVE || echo GONE`,
    );
    if (alive.stdout.includes('GONE')) {
      throw new Error(
        `${node.name}: '${name}' vanished without writing an exit status -- the node rebooted, ` +
          `the process was killed, or it died mid-step. The node is in an unknown intermediate ` +
          `state; probe it before doing anything else.`,
      );
    }
  }
}
