import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { makeProgressCollapser } from '../src/progress.js';

/** Feed lines at a controlled clock, so the rate limit is exercised without waiting. */
function feed(lines: Array<string | number>, minIntervalMs = 2_000) {
  let clock = 0;
  const c = makeProgressCollapser({ minIntervalMs, now: () => clock });
  const out: string[] = [];
  for (const l of lines) {
    if (typeof l === 'number') { clock += l; continue; } // advance time
    const r = c(l);
    if (r !== null) out.push(r);
  }
  return out;
}

describe('progress collapsing', () => {
  it('collapses INTERLEAVED layers, which is what docker actually emits', () => {
    // The real shape from campod-se's bootstrap: two layers alternating, each line identical
    // to the one two back. A consecutive-duplicate filter removes none of these.
    const storm: string[] = [];
    for (let i = 0; i < 400; i++) {
      storm.push(' a20d191cf9b9 Downloading 28.31MB', ' 75782e20ea1f Extracting 1B');
    }
    assert.deepEqual(feed(storm), [' a20d191cf9b9 Downloading 28.31MB', ' 75782e20ea1f Extracting 1B']);
  });

  it('tolerates the leading indent docker writes', () => {
    assert.equal(feed([' a1 Extracting 1B']).length, 1);
  });

  it('lets real progress through once the rate limit has elapsed', () => {
    assert.deepEqual(
      feed([' a1 Downloading 1MB', ' a1 Downloading 2MB', 3000, ' a1 Downloading 3MB']),
      [' a1 Downloading 1MB', ' a1 Downloading 3MB'],
    );
  });

  it('never suppresses a terminal stage -- those are the lines worth reading', () => {
    const out = feed([' a1 Downloading 1MB', ' a1 Pull complete', ' a2 Already exists']);
    assert.ok(out.includes(' a1 Pull complete'));
    assert.ok(out.includes(' a2 Already exists'));
  });

  it('emits a terminal stage once, not on every redraw of the display', () => {
    assert.equal(feed([' a1 Pull complete', ' a1 Pull complete', ' a1 Pull complete']).length, 1);
  });

  it('keeps layers independent', () => {
    assert.equal(feed([' a1 Downloading 1B', ' a2 Downloading 1B', ' a3 Downloading 1B']).length, 3);
  });

  it('NEVER swallows a non-progress line, however often it repeats', () => {
    const real = ['error: manifest unknown', 'error: manifest unknown', 'PLAY RECAP ****'];
    assert.deepEqual(feed(real), real);
  });

  it('passes ansible and one_time output through untouched', () => {
    const real = [
      'one_time: complete (campod, no pending kernel/firmware reboot).',
      'localhost                  : ok=26   changed=15   unreachable=0    failed=0',
    ];
    assert.deepEqual(feed(real), real);
  });
});
