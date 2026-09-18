import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { cacheKey, ImageCache, type CachedImage } from '../src/imagecache.js';

const SHA = '31bb65c20ba499ecc65c1e3c31e1a533b2c1cda5';

function meta(over: Partial<CachedImage> = {}): CachedImage {
  return {
    role: 'campod',
    sha: SHA,
    ref: 'main',
    runId: 1,
    sha256: 'f'.repeat(64),
    sizeBytes: 852_000_000,
    fetchedAt: '2026-09-18T18:52:10Z',
    title: 'pi-image: emit the fleet manifest (#64)',
    ...over,
  };
}

function seed(entries: CachedImage[], withZip = true): string {
  const dir = mkdtempSync(join(tmpdir(), 'imgcache-'));
  mkdirSync(dir, { recursive: true });
  for (const m of entries) {
    const key = cacheKey(m.role, m.sha);
    writeFileSync(join(dir, `${key}.json`), JSON.stringify(m));
    if (withZip) writeFileSync(join(dir, `${key}.zip`), 'zip bytes');
  }
  return dir;
}

test('an absent cache directory lists as empty rather than throwing', async () => {
  const c = new ImageCache(join(tmpdir(), 'definitely-not-created-' + Date.now()));
  assert.deepEqual(await c.list(), []);
});

test('lists what it holds, newest fetch first', async () => {
  const dir = seed([
    meta({ sha: 'a'.repeat(40), fetchedAt: '2026-09-01T00:00:00Z' }),
    meta({ sha: 'b'.repeat(40), fetchedAt: '2026-09-18T00:00:00Z' }),
  ]);
  const got = await new ImageCache(dir).list();
  assert.equal(got.length, 2);
  assert.equal(got[0]?.sha, 'b'.repeat(40));
});

test('a corrupt entry costs that entry, not the listing', async () => {
  const dir = seed([meta()]);
  writeFileSync(join(dir, 'campod-deadbeef.json'), '{ not json');
  const got = await new ImageCache(dir).list();
  assert.equal(got.length, 1);
  assert.equal(got[0]?.sha, SHA);
});

test('get and pathFor are keyed by role and sha together', async () => {
  const dir = seed([meta(), meta({ role: 'coordinator' })]);
  const c = new ImageCache(dir);
  assert.equal((await c.get('campod', SHA))?.role, 'campod');
  assert.equal((await c.get('coordinator', SHA))?.role, 'coordinator');
  assert.equal(await c.get('pocketterm', SHA), undefined);
  assert.ok((await c.pathFor('campod', SHA))?.endsWith(`${cacheKey('campod', SHA)}.zip`));
});

test('metadata without its zip does not count as cached', async () => {
  // A .json with no .zip beside it is a half-written entry; pathFor is what `ensure` trusts
  // before deciding it can skip the download.
  const dir = seed([meta()], false);
  const c = new ImageCache(dir);
  assert.ok(await c.get('campod', SHA));
  assert.equal(await c.pathFor('campod', SHA), undefined);
});
