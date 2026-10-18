import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { collectedDir, recordCollected, validFlightName, type Collected } from '../src/offload.js';

test('a flight name has to be one safe path segment', () => {
  // It becomes a directory under the datasets share, so this is the only thing standing
  // between an operator typo and a write somewhere unintended.
  for (const ok of ['2026-09-19-vio', 'bench_01', 'a', 'A.1-b']) {
    assert.equal(validFlightName(ok), true, ok);
  }
  for (const bad of ['../etc', 'a/b', '.', '..', '', '-leading', 'with space', 'x'.repeat(65)]) {
    assert.equal(validFlightName(bad), false, JSON.stringify(bad));
  }
});


test('each contribution is a file of its own, so two offloads cannot lose each other', async () => {
  // The reason there is no manifest to merge. Both campods offload at the same time, in
  // pods of their own; appending to one shared file is read-modify-write, and whichever
  // entry was read before the other's write disappeared -- a verified bundle on the share
  // that nothing named. One file per contribution has nothing to coordinate.
  const flights = mkdtempSync(join(tmpdir(), 'flights-'));
  const entry = (node: string): Collected => ({
    node,
    session: `${node}-session`,
    file: `${node}_session.tar.zst`,
    bytes: 1_000,
    sha256: 'a'.repeat(64),
    collectedAt: '2026-10-18T00:00:00Z',
    sourceDeleted: true,
  });

  await Promise.all([
    recordCollected(flights, 'f', entry('campod-sw')),
    recordCollected(flights, 'f', entry('campod-se')),
  ]);

  // What a reader does: glob the directory. No ordering, no join, no summary to be stale.
  const dir = collectedDir(flights, 'f');
  const got = readdirSync(dir).sort()
    .map((n) => JSON.parse(readFileSync(join(dir, n), 'utf8')) as Collected);
  assert.deepEqual(got.map((c) => c.node), ['campod-se', 'campod-sw']);
  assert.equal(got[0]?.sha256.length, 64);
});

test('a contribution round-trips exactly, because the session id is what delete is given', () => {
  const flights = mkdtempSync(join(tmpdir(), 'flights-'));
  const session = 'a0760391-8e43-49f2-98fb-9d9e9fe15595';
  return recordCollected(flights, '2026-09-19-vio', {
    node: 'campod-se',
    session,
    file: `campod-se_${session}.tar.zst`,
    bytes: 1_500_000_000,
    sha256: 'f'.repeat(64),
    collectedAt: '2026-09-19T23:05:00Z',
    sourceDeleted: true,
  }).then(() => {
    const dir = collectedDir(flights, '2026-09-19-vio');
    const [name] = readdirSync(dir);
    const back = JSON.parse(readFileSync(join(dir, name as string), 'utf8')) as Collected;
    assert.equal(back.session, session);
    assert.equal(back.sourceDeleted, true);
    assert.equal(back.bytes, 1_500_000_000);
  });
});
