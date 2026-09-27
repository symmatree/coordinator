// The ground station's own record of a flight, which lives in the cluster rather than on the
// vehicle: mavproxy's console timeline, the base station's position and raw observations, and the
// backpack link metrics.
//
// `captures/` is the vehicle's view and `ground/` is the ground station's -- different observers,
// neither substituting for the other (docs/flight-data-layout.md). A ground-side link dropout is
// invisible to the vehicle, and the FC log cannot tell you where the base station was.
//
// Driven with `kubectl`, for the same reason the device side is driven with `ssh`: one credential
// and one trust path, and the automated steps are the documented manual ones (#385,
// docs/post-flight-collection.md) rather than a second implementation of them. In-cluster kubectl
// picks up the pod's ServiceAccount, which has pod read plus exec in `mavproxy` and `ntrip` and
// nothing else (tiles#793).

import { execFile } from 'node:child_process';
import { copyFile, mkdir, readdir, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { promisify } from 'node:util';
import type { Config } from './config.js';

const run = promisify(execFile);

/** Everything here is one `kubectl`. Errors carry what it said, not just that it failed. */
async function kubectl(args: string[], timeoutSec = 300): Promise<string> {
  try {
    const { stdout } = await run('kubectl', args, {
      maxBuffer: 64 * 1024 * 1024,
      timeout: timeoutSec * 1000,
    });
    return stdout;
  } catch (err) {
    const e = err as { stderr?: string; message?: string; killed?: boolean };
    if (e.killed === true) throw new Error(`kubectl ${args[0]} gave up after ${timeoutSec}s`);
    const said = (e.stderr ?? '').trim() || e.message || 'kubectl failed';
    throw new Error(`kubectl ${args.slice(0, 3).join(' ')}: ${said.split('\n').slice(-2).join(' ').slice(0, 300)}`);
  }
}

/** The newest running pod whose name contains `match`, in `ns`. */
export async function findPod(ns: string, match: string): Promise<string> {
  const out = await kubectl(['get', 'pods', '-n', ns, '--field-selector=status.phase=Running',
    '-o', 'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{end}']);
  const pod = out.split('\n').map((l) => l.trim()).filter((l) => l.includes(match))[0];
  if (pod === undefined) throw new Error(`no running pod matching ${match} in ${ns}`);
  return pod;
}

/**
 * The armed window, as the ground saw it.
 *
 * This is the one clock in the whole flight that is trustworthy without qualification: a cluster
 * workload on the cluster's own time. Neither device has an RTC, so nothing on the vehicle can
 * establish this -- which is why selecting sessions and FC logs is a ground-side act (#385).
 */
export function armedWindow(console: string): { armed?: string; disarmed?: string } {
  // mavproxy console lines are `<RFC3339 stamp> <text>` with --timestamps, and ArduPilot's
  // arm/disarm shows up as the literal words. Matched case-insensitively and anchored on the
  // stamp so a mode name containing "armed" cannot be mistaken for the event.
  const stamped = /^(\S+)\s+(.*)$/;
  let armed: string | undefined;
  let disarmed: string | undefined;
  for (const line of console.split('\n')) {
    const m = stamped.exec(line);
    if (!m) continue;
    const when = m[1];
    const text = m[2];
    if (when === undefined || text === undefined) continue;
    if (/\bARMED\b/i.test(text) && !/DISARMED/i.test(text)) armed ??= when;
    else if (/\bDISARMED\b/i.test(text)) disarmed = when;
  }
  return { armed, disarmed };
}

/** `key=value` out of an rtkbase settings.conf, quotes stripped. */
export function settingsValue(conf: string, key: string): string | undefined {
  for (const line of conf.split('\n')) {
    const m = new RegExp(`^\\s*${key}\\s*=\\s*(.*)$`).exec(line);
    if (m?.[1] !== undefined) return m[1].trim().replace(/^['"]|['"]$/g, '');
  }
  return undefined;
}

export interface Collected {
  file: string;
  bytes: number;
}

export interface GroundOptions {
  flightsDir: string;
  note?: (line: string) => void;
}

/**
 * Collect the ground side of one flight into `<flight>/ground/`.
 *
 * `ground/` rather than `cluster/`: docs/flight-data-layout.md puts ground-side records there and
 * calls itself canonical. docs/post-flight-collection.md used `cluster/` on the day; the two
 * disagree.
 *
 * Each artifact is attempted independently and a failure is recorded rather than thrown, because
 * a flight missing its base position is still worth the console log -- and "not collected, and
 * why" is as load-bearing as the list of what was.
 */
export async function collectGround(
  cfg: Config,
  flight: string,
  opts: GroundOptions,
): Promise<{ collected: Collected[]; failed: string[]; window: { armed?: string; disarmed?: string } }> {
  const say = opts.note ?? (() => {});
  const dir = join(opts.flightsDir, flight, 'ground');
  await mkdir(dir, { recursive: true });

  const collected: Collected[] = [];
  const failed: string[] = [];
  let window: { armed?: string; disarmed?: string } = {};

  const keep = async (name: string, body: string | Buffer): Promise<void> => {
    await writeFile(join(dir, name), body);
    collected.push({ file: name, bytes: body.length });
    say(`  ${name}: ${body.length} bytes`);
  };

  // 1. mavproxy's console. First, because it establishes the window everything else is cut to.
  try {
    const pod = await findPod(cfg.cluster.mavproxyNamespace, 'mavproxy');
    say(`mavproxy console from ${pod}`);
    const log = await kubectl(['logs', '-n', cfg.cluster.mavproxyNamespace, pod, '--timestamps']);
    await keep('mavproxy-console.log', log);
    window = armedWindow(log);
    say(window.armed
      ? `armed ${window.armed} -> disarmed ${window.disarmed ?? '(not seen)'}`
      : 'no ARM in the console log; nothing to cut the other artifacts to');
  } catch (err) {
    failed.push(`mavproxy-console.log: ${(err as Error).message}`);
    say(`mavproxy console FAILED: ${(err as Error).message}`);
  }

  // 2. The base station's settings -- `position=` is why this matters. PPK is not possible
  //    without the base coordinates, and `local_ntripc_msg` is the mount the vehicle consumed.
  let datadir: string | undefined;
  try {
    const pod = await findPod(cfg.cluster.ntripNamespace, 'rtkbase');
    say(`rtkbase settings from ${pod}`);
    const conf = await kubectl(['exec', '-n', cfg.cluster.ntripNamespace, pod, '-c', 'rtkbase',
      '--', 'cat', '/root/rtkbase/settings.conf']);
    await keep('rtkbase-settings.conf', conf);
    const position = settingsValue(conf, 'position');
    say(position ? `base position ${position}` : 'settings.conf carries no position=');
    datadir = settingsValue(conf, 'datadir');
  } catch (err) {
    failed.push(`rtkbase-settings.conf: ${(err as Error).message}`);
    say(`rtkbase settings FAILED: ${(err as Error).message}`);
  }

  // 3. The raw observations for the flight's day. `datadir` is on a separate mount inside that
  //    pod, so it is read from settings.conf rather than guessed at.
  if (datadir !== undefined) {
    const day = (window.armed ?? new Date().toISOString()).slice(0, 10);
    try {
      const pod = await findPod(cfg.cluster.ntripNamespace, 'rtkbase');
      const listing = await kubectl(['exec', '-n', cfg.cluster.ntripNamespace, pod, '-c', 'rtkbase',
        '--', 'sh', '-c', `ls -1 '${datadir}' 2>/dev/null || true`]);
      const wanted = listing.split('\n').map((l) => l.trim()).filter((l) => l.startsWith(day) && l.includes('.ubx'));
      if (wanted.length === 0) say(`no .ubx for ${day} under ${datadir}`);
      for (const name of wanted) {
        // The current day's file is open and being appended; the already-written prefix covers
        // any past flight, which is why this reads rather than requiring a quiescent file.
        const body = await kubectl(['exec', '-n', cfg.cluster.ntripNamespace, pod, '-c', 'rtkbase',
          '--', 'cat', `${datadir}/${name}`], 1800);
        await keep(name, body);
      }
    } catch (err) {
      failed.push(`rtkbase observations: ${(err as Error).message}`);
      say(`rtkbase observations FAILED: ${(err as Error).message}`);
    }
  }

  // 4. The backpack link metrics. Mimir over HTTP, needing no Kubernetes identity at all -- the
  //    only view we have of the backpack's own WiFi hop (#190), and the direct evidence for #99.
  if (window.armed !== undefined) {
    try {
      const body = await backpackMetrics(cfg, window.armed, window.disarmed);
      await keep(`backpack-metrics-${window.armed.slice(0, 10)}.json`, body);
    } catch (err) {
      failed.push(`backpack metrics: ${(err as Error).message}`);
      say(`backpack metrics FAILED: ${(err as Error).message}`);
    }
  }

  // 5. The per-flight tlog tlog-split already wrote. A file copy, not a retrieval: it has been
  //    on the share since the flight, which is the point of tiles#794.
  if (window.armed !== undefined) {
    try {
      const name = await tlogForWindow(cfg.cluster.groundTlogs, window.armed);
      if (name === undefined) {
        say(`no split tlog covering ${window.armed} in ${cfg.cluster.groundTlogs}`);
      } else {
        await copyFile(join(cfg.cluster.groundTlogs, name), join(dir, name));
        const { size } = await stat(join(dir, name));
        collected.push({ file: name, bytes: size });
        say(`  ${name}: ${size} bytes`);
      }
    } catch (err) {
      failed.push(`split tlog: ${(err as Error).message}`);
      say(`split tlog FAILED: ${(err as Error).message}`);
    }
  }

  return { collected, failed, window };
}

/**
 * The split tlog whose armed stamp matches this flight.
 *
 * tlog-split names each file `<start>-armed-<armed>-disarmed-<disarmed>.tlog`, so the armed time
 * the console log gave us selects the file directly -- no scanning, and no trusting a device
 * clock. Matched to the second, because both stamps come from cluster time.
 *
 * `.part` files are skipped: one is still being written.
 */
export async function tlogForWindow(dir: string, armed: string): Promise<string | undefined> {
  const stamp = armed.replace(/[-:]/g, '').replace(/\.\d+/, '').replace(/Z$/, 'Z');
  let names: string[];
  try {
    names = await readdir(dir);
  } catch (err) {
    throw new Error(`cannot read ${dir}: ${(err as Error).message}`);
  }
  return names.filter((n) => n.endsWith('.tlog')).find((n) => n.includes(`-armed-${stamp}`));
}

/**
 * Every `backpack_*` series across the flight window, plus a margin.
 *
 * The margin is not decoration: on 2026-09-23 the backpack rebooted and re-associated in the
 * minutes BEFORE the armed window, which is the event that explained the flight. A range clipped
 * to arm-disarm would have shown a healthy link throughout.
 */
export async function backpackMetrics(cfg: Config, armed: string, disarmed?: string): Promise<string> {
  const marginSec = 20 * 60;
  const start = Math.floor(Date.parse(armed) / 1000) - marginSec;
  const end = Math.floor(Date.parse(disarmed ?? armed) / 1000) + marginSec;
  const url = new URL('/prometheus/api/v1/query_range', cfg.cluster.mimirUrl);
  url.searchParams.set('query', '{__name__=~"backpack.*"}');
  url.searchParams.set('start', String(start));
  url.searchParams.set('end', String(end));
  url.searchParams.set('step', '5');
  const res = await fetch(url, { headers: { 'X-Scope-OrgID': cfg.cluster.mimirTenant } });
  if (!res.ok) throw new Error(`mimir ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return JSON.stringify(JSON.parse(await res.text()), null, 2);
}
