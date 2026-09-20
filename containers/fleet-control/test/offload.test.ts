import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readBundle, validFlightName, type FlightRecord } from '../src/offload.js';

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

test('the flight record survives being read back', () => {
  // flight.json is rewritten after every session rather than once at the end, so a run that
  // dies half way still leaves a directory that says what is in it.
  const dir = mkdtempSync(join(tmpdir(), 'flight-'));
  const rec: FlightRecord = {
    flight: '2026-09-19-vio',
    createdAt: '2026-09-19T23:00:00Z',
    collected: [
      {
        node: 'campod-se',
        session: 'a0760391-8e43-49f2-98fb-9d9e9fe15595',
        file: 'campod-se_a0760391-8e43-49f2-98fb-9d9e9fe15595.tar.zst',
        bytes: 1_500_000_000,
        sha256: 'f'.repeat(64),
        collectedAt: '2026-09-19T23:05:00Z',
        sourceDeleted: true,
      },
    ],
  };
  writeFileSync(join(dir, 'flight.json'), JSON.stringify(rec, null, 2));
  const back = JSON.parse(readFileSync(join(dir, 'flight.json'), 'utf8')) as FlightRecord;
  assert.equal(back.collected.length, 1);
  assert.equal(back.collected[0]?.sourceDeleted, true);
  // The session id is the boot id and must round-trip exactly -- it is what delete is given.
  assert.equal(back.collected[0]?.session, 'a0760391-8e43-49f2-98fb-9d9e9fe15595');
});

test('reading a bundle needs root, like everything else in this chain', () => {
  // The capture tree is written by root-running containers, so `package` builds a root-owned
  // bundle (#366, #371). Reasoning about whether `pi` could read it anyway has been wrong on
  // a device twice. The reason is here so this does not get tidied back out.
  const cmd = readBundle('/var/lib/campod/bundles/campod-se_abc.tar.zst');
  assert.match(cmd, /sudo cat '\/var\/lib\/campod\/bundles\/campod-se_abc\.tar\.zst'/);
  // Quiesced like every other command, and the quiesce is itself root (#366).
  assert.ok(cmd.startsWith('sudo pkill'), cmd);
});

test('a quote in the path cannot end the quoting', () => {
  // The path comes from the device's own output, but it still goes into a remote shell.
  const cmd = readBundle("/tmp/it's here.tar.zst");
  assert.ok(cmd.endsWith(`sudo cat '/tmp/it'\\''s here.tar.zst'`), cmd);
});
