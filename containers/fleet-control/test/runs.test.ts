import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { RunRegistry, sinkFor } from '../src/runs.js';

const settle = () => new Promise((r) => setTimeout(r, 10));

describe('RunRegistry', () => {
  it('returns immediately and finishes in the background', async () => {
    const runs = new RunRegistry();
    const run = runs.start('update', 'campod-sw', async () => {});
    assert.equal(run.status, 'running');
    await settle();
    assert.equal(runs.get(run.id)?.status, 'succeeded');
  });

  it('captures the failure message rather than losing it', async () => {
    const runs = new RunRegistry();
    const run = runs.start('update', 'campod-sw', async () => {
      throw new Error('coord pull failed (exit 1)');
    });
    await settle();
    const done = runs.get(run.id)!;
    assert.equal(done.status, 'failed');
    assert.equal(done.error, 'coord pull failed (exit 1)');
    assert.ok(done.lines.some((l) => l.line.includes('coord pull failed')));
  });

  it('REFUSES a second action against a node already running one', async () => {
    const runs = new RunRegistry();
    runs.start('bootstrap', 'campod-sw', () => new Promise(() => {}));
    assert.throws(() => runs.start('update', 'campod-sw', async () => {}), /already has 'bootstrap' running/);
  });

  it('allows concurrent actions against DIFFERENT nodes', async () => {
    const runs = new RunRegistry();
    runs.start('update', 'campod-sw', () => new Promise(() => {}));
    assert.doesNotThrow(() => runs.start('update', 'coordinator', () => new Promise(() => {})));
  });

  it('allows a new action once the previous one finished', async () => {
    const runs = new RunRegistry();
    runs.start('update', 'campod-sw', async () => {});
    await settle();
    assert.doesNotThrow(() => runs.start('update', 'campod-sw', async () => {}));
  });

  it('records streamed lines in order, tagged by stream', async () => {
    const runs = new RunRegistry();
    const run = runs.start('update', 'campod-sw', async (emit) => {
      const sink = sinkFor(emit);
      sink('stdout', 'Already up to date.');
      sink('stderr', 'warning: something');
    });
    await settle();
    const lines = runs.get(run.id)!.lines;
    assert.equal(lines[0]?.line, 'Already up to date.');
    assert.equal(lines[0]?.stream, 'stdout');
    assert.equal(lines[1]?.stream, 'stderr');
  });

  it('delivers lines to a live subscriber', async () => {
    const runs = new RunRegistry();
    const seen: string[] = [];
    let go: () => void = () => {};
    const gate = new Promise<void>((r) => (go = r));
    const run = runs.start('update', 'campod-sw', async (emit) => {
      await gate;
      sinkFor(emit)('stdout', 'pulling');
    });
    runs.subscribe(run.id, (l) => seen.push(l.line));
    go();
    await settle();
    assert.ok(seen.includes('pulling'));
  });
});
