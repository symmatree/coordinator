import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { composeFlight, recordCollected, validFlightName, type Collected, type FlightRecord } from '../src/offload.js';

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

test('flight.json is composed from collected/, so two offloads cannot lose each other', async () => {
  // The reason the summary is derived rather than appended to. Two campods offload in pods
  // of their own; a shared flight.json read-modify-written from both drops whichever entry
  // was read before the other's write. Each writing its own file cannot.
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
  // Composed again, as the join step does once both have stopped writing.
  const joined = await composeFlight(flights, 'f');
  assert.deepEqual(joined.collected.map((c) => c.node), ['campod-se', 'campod-sw']);

  const onDisk = JSON.parse(
    readFileSync(join(flights, 'f', 'flight.json'), 'utf8'),
  ) as FlightRecord;
  assert.deepEqual(onDisk.collected.map((c) => c.node), ['campod-se', 'campod-sw']);
  assert.equal(onDisk.flight, 'f');
});

test('recomposing keeps when the flight was created, not when it last ran', async () => {
  // createdAt is a fact about the flight. Recomposing is something that happens to the
  // summary afterwards, repeatedly, and must not keep moving it.
  const flights = mkdtempSync(join(tmpdir(), 'flights-'));
  await recordCollected(flights, 'f', {
    node: 'campod-se',
    session: 's',
    file: 'f.tar.zst',
    bytes: 1,
    sha256: 'b'.repeat(64),
    collectedAt: '2026-10-18T00:00:00Z',
    sourceDeleted: false,
  });
  const first = await composeFlight(flights, 'f');
  const again = await composeFlight(flights, 'f');
  assert.equal(again.createdAt, first.createdAt);
});
