import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { jobEventsDir, runDir } from '../src/ansible.js';
import { eventFiles, eventLines, isRunId, logChunks } from '../src/runartifacts.js';

/** Lay down a run directory shaped the way ansible-runner 2.4.3 actually writes one. */
function fakeRun(events: Array<{ counter: number; body: string }>): string {
  const id = randomUUID();
  const dir = jobEventsDir(id, randomUUID());
  mkdirSync(dir, { recursive: true });
  for (const { counter, body } of events) {
    writeFileSync(join(dir, `${counter}-${randomUUID()}.json`), body);
  }
  return id;
}

const event = (counter: number, stdout: string): { counter: number; body: string } => ({
  counter,
  body: JSON.stringify({ counter, event: 'runner_on_ok', stdout }, null, 2),
});

async function collect(gen: AsyncGenerator<string>): Promise<string> {
  let out = '';
  for await (const chunk of gen) out += chunk;
  return out;
}

describe('run artifacts', () => {
  it('orders events by counter, not by filename', async () => {
    // The whole point: `10-...json` sorts before `2-...json` lexically, so a plain readdir
    // sort reorders any play with more than nine events -- which is all of them.
    const id = fakeRun([1, 2, 10, 11, 3].map((c) => event(c, `line ${c}`)));
    try {
      const lines = (await collect(eventLines(await eventFiles(id)))).trim().split('\n');
      assert.deepEqual(
        lines.map((l) => (JSON.parse(l) as { counter: number }).counter),
        [1, 2, 3, 10, 11],
      );
    } finally {
      rmSync(runDir(id), { recursive: true, force: true });
    }
  });

  it('reports an unreadable event instead of abandoning the response', async () => {
    // A response already half-sent cannot become a 500, and one bad file should not cost the
    // other five hundred events.
    const id = fakeRun([event(1, 'fine'), { counter: 2, body: 'not json at all' }, event(3, 'also fine')]);
    try {
      const lines = (await collect(eventLines(await eventFiles(id)))).trim().split('\n');
      assert.equal(lines.length, 3);
      assert.equal((JSON.parse(lines[1]!) as { event: string }).event, 'fleet_control_unreadable');
    } finally {
      rmSync(runDir(id), { recursive: true, force: true });
    }
  });

  it('terminates each log line, so a task header and its result are not one line', async () => {
    // Verified against real runner output: a task-start event's text ends without a newline
    // and the result event's begins without one, so concatenating raw runs them together.
    const id = fakeRun([event(1, '\r\nTASK [Stop data collection] ****'), event(2, 'fatal: [campod-se]: FAILED!')]);
    try {
      const log = await collect(logChunks(await eventFiles(id)));
      assert.equal(log, '\r\nTASK [Stop data collection] ****\nfatal: [campod-se]: FAILED!\n');
    } finally {
      rmSync(runDir(id), { recursive: true, force: true });
    }
  });

  it('a run that kept nothing has no events, rather than an error', async () => {
    assert.deepEqual(await eventFiles(randomUUID()), []);
  });

  it('only ever accepts a run id, so a URL segment cannot become a path', () => {
    assert.equal(isRunId('a2a31907-48c6-448e-a8b4-b36da4e971cb'), true);
    for (const bad of ['../../etc', 'a2a31907', '', '.', 'A2A31907-48C6-448E-A8B4-B36DA4E971CB']) {
      assert.equal(isRunId(bad), false, JSON.stringify(bad));
    }
  });
});
