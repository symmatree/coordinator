// The ground station's own record of a flight, which lives in the cluster rather than on the
// vehicle: the per-flight tlogs tlog-split wrote, and the backpack link metrics.
//
// `captures/` is the vehicle's view and `ground/` is the ground station's -- different observers,
// neither substituting for the other (docs/flight-data-layout.md). The tlog is the ground record:
// what actually crossed the radio link and the backpack, which the vehicle cannot see, and the
// timing reconciliation for everything device-side, since neither device has an RTC.
//
// THIS DOES NOT WORK OUT WHICH FLIGHT YOU MEANT. It takes the tlogs you name and the range you
// give (#414). It used to derive an armed window from mavproxy's console log and then rebuild a
// tlog filename from it, which filed the wrong flight's tlog as soon as that pod had seen more
// than one -- and armed is the wrong interval anyway, since it begins after the pre-arm window
// where RTK convergence happens.
//
// NOTHING HERE TOUCHES ANOTHER POD, which is why the `pods/log` and `pods/exec` grants this
// service held in `mavproxy` and `ntrip` are gone. Two of those reads were dropped outright:
// mavproxy's console log is cluster debugging output rather than flight data, and rtkbase's
// settings.conf is git-authoritative in tiles so it already has a history. The third, the base
// station's raw observations, moved rather than went: it was `kubectl exec ... cat` into a 64 MB
// buffer against a few hundred MB of file, and now rtkbase writes them straight to the datasets
// share and this reads the days a flight spans off it (#416).
//
// So every source here is a directory on the share, plus one HTTP query to Mimir.

import { copyFile, mkdir, readdir, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { Config } from './config.js';

/** One file in the tlog directory, as the listing reports it. */
export interface TlogFile {
  name: string;
  bytes: number;
  /** Last written, from the filesystem. Present for every file, unlike the parsed stamps. */
  modified: string;
  /** Still being written, so a copy of it is a prefix rather than a whole file. */
  partial: boolean;
  /** Parsed out of the name when it is there. `armed` is absent on a session that never armed. */
  start?: string;
  armed?: string;
  disarmed?: string;
}

/** `20260926T221502Z` -> `2026-09-26T22:15:02Z`. Undefined if it is not that shape. */
function isoFromStamp(stamp: string | undefined): string | undefined {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(stamp ?? '');
  return m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z` : undefined;
}

/**
 * What the tlog directory holds.
 *
 * A LISTING, NOT A PARSE. It stats each file and reads the stamps tlog-split put in the name; it
 * never opens a tlog. And it reports every file it finds rather than only the ones matching the
 * naming it expects -- a listing that hides what it cannot classify makes those files
 * unretrievable, which is worse than a row with empty columns.
 *
 * tlog-split names a finished file `<start>-armed-<armed>-disarmed-<disarmed>.tlog`, or
 * `<start>-noflight-<why>.tlog` when nothing armed, and holds an open one at `.tlog.part`.
 */
export async function listTlogs(dir: string): Promise<TlogFile[]> {
  let names: string[];
  try {
    names = await readdir(dir);
  } catch (err) {
    throw new Error(`cannot read ${dir}: ${(err as Error).message}`);
  }
  const files = names.filter((n) => n.endsWith('.tlog') || n.endsWith('.tlog.part'));
  const out = await Promise.all(files.map(async (name): Promise<TlogFile> => {
    const st = await stat(join(dir, name));
    const m = /^(\d{8}T\d{6}Z)(?:-armed-(\d{8}T\d{6}Z)-disarmed-(\d{8}T\d{6}Z|open))?/.exec(name);
    return {
      name,
      bytes: st.size,
      modified: st.mtime.toISOString(),
      partial: name.endsWith('.part'),
      start: isoFromStamp(m?.[1]),
      armed: isoFromStamp(m?.[2]),
      disarmed: isoFromStamp(m?.[3]),
    };
  }));
  // Newest first, by the one field every row has.
  return out.sort((a, b) => b.modified.localeCompare(a.modified));
}

/** The interval the range-dependent artifacts are fetched over. */
export interface Range {
  start: string;
  end: string;
  /** How it was arrived at, for the run log -- given, or taken from the tlogs named. */
  from: string;
}

/**
 * The range, from what the caller said.
 *
 * An explicit `start`/`end` is used as given: no margin is added, because a caller that states an
 * interval means that interval. Otherwise the named tlogs bound it -- each one runs from the
 * previous cut to its disarm, so it already covers the pre-arm window, and a tlog spanning more
 * than the flight is a harmless superset.
 *
 * `modified` is the fallback for a file whose name did not parse, since every file has one.
 */
export function resolveRange(
  picked: TlogFile[],
  given: { start?: string; end?: string },
): Range | undefined {
  if (given.start !== undefined && given.end !== undefined) {
    return { start: given.start, end: given.end, from: 'given' };
  }
  if (picked.length === 0) return undefined;
  const start = picked.map((t) => t.start ?? t.modified).reduce((a, b) => (a < b ? a : b));
  const end = picked.map((t) => t.disarmed ?? t.modified).reduce((a, b) => (a > b ? a : b));
  return { start, end, from: `the ${picked.length} tlog(s) named` };
}

/**
 * Every UTC day the range touches, because the base station's raw observations rotate daily.
 *
 * UTC rather than local: the filenames rtkbase writes are UTC and so are the range's stamps, and a
 * range that crosses midnight in one zone and not the other is a distinction with nothing behind
 * it here.
 */
export function daysIn(range: Range): string[] {
  const first = Date.parse(`${range.start.slice(0, 10)}T00:00:00Z`);
  const last = range.end.slice(0, 10);
  if (!Number.isFinite(first) || !/^\d{4}-\d{2}-\d{2}$/.test(last)) {
    throw new Error(`range is not a pair of timestamps: ${range.start} -> ${range.end}`);
  }
  const days: string[] = [];
  // Ends on `last`, and ends immediately if the range runs backwards, so a bad pair cannot spin.
  for (let t = first; days.length < 400; t += 86_400_000) {
    const day = new Date(t).toISOString().slice(0, 10);
    days.push(day);
    if (day >= last) break;
  }
  return days;
}

/**
 * The base station's raw observations for one UTC day.
 *
 * `file_name='%Y-%m-%d_%h-%M-%S_GNSS-1'` in rtkbase's settings, so a day's files are the ones whose
 * name begins with that date. The `.ubx.tag` sidecar comes too -- it is matched by the same
 * `.ubx` test and is part of the record.
 *
 * A DIRECTORY READ. rtkbase writes these to `datasets/gps-logs/attic-rtk-base/raw/` -- the
 * directory the base's logs already live in, whose curated top level feeds a PPP re-solve of the
 * base position -- and this service mounts it read-only, so there is no pod to reach into and
 * nothing to stream (#416).
 */
export async function observationsFor(dir: string, day: string): Promise<string[]> {
  const names = await readdir(dir);
  return names.filter((n) => n.startsWith(day) && n.includes('.ubx')).sort();
}

export interface Collected {
  file: string;
  bytes: number;
}

export interface GroundOptions {
  flightsDir: string;
  /** The tlogs to collect, by the name the listing gave. */
  tlogs?: string[];
  /** The interval for the observations and the backpack series. Overrides what the tlogs imply. */
  start?: string;
  end?: string;
  note?: (line: string) => void;
}

/**
 * Collect the ground side of one flight into `<flight>/ground/`.
 *
 * `ground/` rather than `cluster/`: docs/flight-data-layout.md puts ground-side records there and
 * calls itself canonical. docs/post-flight-collection.md used `cluster/` on the day.
 *
 * WHAT THE CALLER NAMED IS NOT BEST-EFFORT. A tlog that was asked for and did not arrive fails the
 * run, because the screen unticks on success and an unticked file reads as collected -- which is
 * then what "Delete the rest" spares. The range-derived artifacts are the other way round: a flight
 * missing its observations or its backpack series is still worth its tlog, so those are recorded as
 * "not collected, and why" and fail nothing.
 */
export async function collectGround(
  cfg: Config,
  flight: string,
  opts: GroundOptions,
): Promise<{ collected: Collected[]; failed: string[]; range?: Range }> {
  const say = opts.note ?? (() => {});
  const dir = join(opts.flightsDir, flight, 'ground');
  await mkdir(dir, { recursive: true });

  const collected: Collected[] = [];
  const failed: string[] = [];

  // 1. The tlogs named. A file copy, not a retrieval: they have been on the share since the
  //    flight, which is the point of tiles#794.
  const wanted = opts.tlogs ?? [];
  const picked: TlogFile[] = [];
  if (wanted.length > 0) {
    // Not caught: a named tlog that cannot be collected throws, for the reason above. Matching
    // against the listing is also what makes a path impossible to smuggle in -- only a name the
    // directory actually holds gets as far as being opened.
    const have = await listTlogs(cfg.cluster.groundTlogs);
    for (const name of wanted) {
      const file = have.find((t) => t.name === name);
      if (file === undefined) {
        throw new Error(`${name} is not in ${cfg.cluster.groundTlogs}. Nothing was collected.`);
      }
      await copyFile(join(cfg.cluster.groundTlogs, name), join(dir, name));
      const { size } = await stat(join(dir, name));
      collected.push({ file: name, bytes: size });
      picked.push(file);
      say(`  ${name}: ${size} bytes${file.partial ? ' (still being written -- a prefix)' : ''}`);
    }
  }

  const range = resolveRange(picked, { start: opts.start, end: opts.end });
  if (range === undefined) {
    say('no range given and no tlog named, so no observations and no backpack series');
    return { collected, failed };
  }
  say(`range ${range.start} -> ${range.end} (${range.from})`);

  // 2. The base station's raw observations, for PPK. The current day's file is open and being
  //    appended; the already-written prefix covers any past flight, which is why this copies it
  //    rather than requiring a quiescent file. Best-effort like the metrics: a flight missing its
  //    observations is still worth its tlog.
  try {
    for (const day of daysIn(range)) {
      const names = await observationsFor(cfg.cluster.baseObs, day);
      if (names.length === 0) {
        say(`no observations for ${day} in ${cfg.cluster.baseObs}`);
        continue;
      }
      for (const name of names) {
        await copyFile(join(cfg.cluster.baseObs, name), join(dir, name));
        const { size } = await stat(join(dir, name));
        collected.push({ file: name, bytes: size });
        say(`  ${name}: ${size} bytes`);
      }
    }
  } catch (err) {
    failed.push(`base observations: ${(err as Error).message}`);
    say(`base observations FAILED: ${(err as Error).message}`);
  }

  // 3. The backpack link metrics. Mimir over HTTP, needing no Kubernetes identity at all -- the
  //    only view we have of the backpack's own WiFi hop (#190), and the direct evidence for #99.
  try {
    const { body, step } = await backpackMetrics(cfg, range);
    say(`backpack series at ${step}s resolution`);
    await writeFile(join(dir, `backpack-metrics-${range.start.slice(0, 10)}.json`), body);
    collected.push({ file: `backpack-metrics-${range.start.slice(0, 10)}.json`, bytes: body.length });
  } catch (err) {
    failed.push(`backpack metrics: ${(err as Error).message}`);
    say(`backpack metrics FAILED: ${(err as Error).message}`);
  }

  return { collected, failed, range };
}

/**
 * Every `backpack_*` series across the range.
 *
 * The step widens for a long range rather than sitting at 5 s: a `query_range` asking for a point
 * every five seconds over a week is hundreds of thousands per series. `POINTS` is the ceiling, and
 * the resolution actually used is reported so a coarse answer is visible as one.
 */
export async function backpackMetrics(cfg: Config, range: Range): Promise<{ body: string; step: number }> {
  const POINTS = 11_000;
  const start = Math.floor(Date.parse(range.start) / 1000);
  const end = Math.floor(Date.parse(range.end) / 1000);
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    throw new Error(`range is not a pair of timestamps: ${range.start} -> ${range.end}`);
  }
  const step = Math.max(5, Math.ceil((end - start) / POINTS));
  const url = new URL('/prometheus/api/v1/query_range', cfg.cluster.mimirUrl);
  url.searchParams.set('query', '{__name__=~"backpack.*"}');
  url.searchParams.set('start', String(start));
  url.searchParams.set('end', String(end));
  url.searchParams.set('step', String(step));
  const res = await fetch(url, { headers: { 'X-Scope-OrgID': cfg.cluster.mimirTenant } });
  if (!res.ok) throw new Error(`mimir ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return { body: JSON.stringify(JSON.parse(await res.text()), null, 2), step };
}
