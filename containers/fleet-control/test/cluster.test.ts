import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, utimesSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { backpackMetrics, listTlogs, resolveRange, type TlogFile } from '../src/cluster.js';

/** A directory shaped like tlog-split's output, including the shapes that do not parse. */
function tlogDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'tlogs-'));
  const files: [string, string][] = [
    // Two flights from one evening, and the noflight stretch before them.
    ['20260926T180000Z-noflight-age.tlog', '2026-09-26T21:00:00Z'],
    ['20260926T210000Z-armed-20260926T221502Z-disarmed-20260926T223000Z.tlog', '2026-09-26T22:30:00Z'],
    ['20260926T223000Z-armed-20260926T224000Z-disarmed-20260926T225500Z.tlog', '2026-09-26T22:55:00Z'],
    // Being written right now.
    ['20260927T090000Z.tlog.part', '2026-09-27T09:30:00Z'],
    // Something nobody's naming scheme produced. It still has to be listable.
    ['hand-copied.tlog', '2026-09-20T12:00:00Z'],
  ];
  for (const [name, when] of files) {
    writeFileSync(join(dir, name), name);
    const t = Date.parse(when) / 1000;
    utimesSync(join(dir, name), t, t);
  }
  return dir;
}

test('the listing reports every file, including the ones whose names do not parse', async () => {
  // A listing that hides what it cannot classify makes those files unretrievable, which is worse
  // than a row with empty columns (#414).
  const list = await listTlogs(tlogDir());
  assert.equal(list.length, 5);
  assert.ok(list.find((t) => t.name === 'hand-copied.tlog'));
  const odd = list.find((t) => t.name === 'hand-copied.tlog') as TlogFile;
  assert.equal(odd.start, undefined);
  assert.equal(odd.armed, undefined);
  // Every row has these two, which is why they are what it sorts on and falls back to.
  assert.ok(odd.bytes > 0);
  assert.equal(odd.modified, '2026-09-20T12:00:00.000Z');
});

test('an open file is listed and marked, not hidden', async () => {
  const list = await listTlogs(tlogDir());
  const part = list.find((t) => t.name.endsWith('.part')) as TlogFile;
  assert.equal(part.partial, true);
  assert.equal(list.filter((t) => !t.partial).length, 4);
});

test('the stamps in a name are read out as ISO, and a never-armed file has no armed time', async () => {
  const list = await listTlogs(tlogDir());
  const flight = list.find((t) => t.name.startsWith('20260926T210000Z')) as TlogFile;
  assert.equal(flight.start, '2026-09-26T21:00:00Z');
  assert.equal(flight.armed, '2026-09-26T22:15:02Z');
  assert.equal(flight.disarmed, '2026-09-26T22:30:00Z');
  const bench = list.find((t) => t.name.includes('noflight')) as TlogFile;
  assert.equal(bench.start, '2026-09-26T18:00:00Z');
  assert.equal(bench.armed, undefined);
});

test('newest first, by the field every row has', async () => {
  const list = await listTlogs(tlogDir());
  assert.deepEqual(list.map((t) => t.modified.slice(0, 16)), [
    '2026-09-27T09:30', '2026-09-26T22:55', '2026-09-26T22:30', '2026-09-26T21:00', '2026-09-20T12:00',
  ]);
});

test('a missing directory says so rather than reporting nothing', async () => {
  // An empty result is not a finding: "no tlogs" and "the share is not mounted" are different.
  await assert.rejects(() => listTlogs(join(tmpdir(), 'no-such-dir-here')), /cannot read/);
});

test('a given range is used exactly as given', () => {
  // No margin. A caller that states an interval means that interval -- widening it silently is
  // the kind of cleverness this route is being cured of.
  const r = resolveRange([], { start: '2026-09-26T21:00:00Z', end: '2026-09-26T23:00:00Z' });
  assert.deepEqual(r, { start: '2026-09-26T21:00:00Z', end: '2026-09-26T23:00:00Z', from: 'given' });
});

test('with no range given, the tlogs named bound it', async () => {
  // Each tlog runs from the previous cut to its disarm, so it already covers the pre-arm window
  // where RTK convergence happens -- which an armed-to-disarmed range excluded.
  const list = await listTlogs(tlogDir());
  const two = list.filter((t) => t.armed !== undefined);
  const r = resolveRange(two, {});
  assert.equal(r?.start, '2026-09-26T21:00:00Z');
  assert.equal(r?.end, '2026-09-26T22:55:00Z');
  assert.match(r?.from ?? '', /2 tlog/);
});

test('a tlog whose name did not parse still bounds a range, by its mtime', async () => {
  const list = await listTlogs(tlogDir());
  const odd = list.filter((t) => t.name === 'hand-copied.tlog');
  const r = resolveRange(odd, {});
  assert.equal(r?.start, '2026-09-20T12:00:00.000Z');
  assert.equal(r?.end, '2026-09-20T12:00:00.000Z');
});

test('nothing named and nothing given is no range, not a guessed one', () => {
  assert.equal(resolveRange([], {}), undefined);
});

test('the metrics step widens with the range, so a long one is not hundreds of thousands of points', async () => {
  // A query_range asking for a point every 5 s over a week is ~120k per series. The step is
  // reported in the run log so a coarse answer is visible as one rather than passing for fine.
  const asked: URL[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (u: URL) => {
    asked.push(u);
    return { ok: true, text: async () => '{"status":"success"}' } as unknown as Response;
  }) as typeof fetch;
  try {
    const cfg = { cluster: { mimirUrl: 'http://mimir.invalid', mimirTenant: 'tiles' } } as never;
    const short = await backpackMetrics(cfg, { start: '2026-09-26T21:00:00Z', end: '2026-09-26T22:00:00Z', from: 'x' });
    assert.equal(short.step, 5);
    const week = await backpackMetrics(cfg, { start: '2026-09-20T00:00:00Z', end: '2026-09-27T00:00:00Z', from: 'x' });
    assert.equal(week.step, 55);   // 7 days over an 11k-point ceiling
    assert.equal(asked[1]?.searchParams.get('step'), '55');
  } finally {
    globalThis.fetch = realFetch;
  }
});

test('a range that is not a pair of timestamps is refused rather than queried', async () => {
  const cfg = { cluster: { mimirUrl: 'http://mimir.invalid', mimirTenant: 'tiles' } } as never;
  await assert.rejects(() => backpackMetrics(cfg, { start: 'nope', end: 'nor this', from: 'x' }), /not a pair/);
});
