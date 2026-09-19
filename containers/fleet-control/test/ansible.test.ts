import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { converge, renderEvent } from '../src/ansible.js';

// converge() spawns `ansible-runner`. These tests exercise what this module is actually
// responsible for -- turning the event stream into operator-readable lines, and reporting the
// exit code honestly -- without requiring ansible or a node.

describe('converge -- when ansible-runner is not installed', () => {
  it('says so rather than failing as an unexplained nonzero', async () => {
    await assert.rejects(
      () =>
        converge({
          host: '10.0.0.1',
          user: 'pi',
          privateKeyPath: '/dev/null',
          extraVars: { device_role: 'campod' },
        }),
      /could not run ansible-runner/,
    );
  });
});

describe('renderEvent -- a failed command task', () => {
  // The shape that cost us an hour: `coord start` returned non-zero on the coordinator and
  // the run log said only "non-zero return code". Why it failed was in the event all along.
  const failure = {
    event: 'runner_on_failed',
    event_data: {
      task: 'Start the stack',
      host: '10.0.99.75',
      res: {
        msg: 'non-zero return code',
        rc: 1,
        stdout: '',
        stderr:
          'Container coordinator_mavlink  Created\n' +
          'Container coordinator_sh1106_display  Created\n' +
          'Error response from daemon: error gathering device information ' +
          'while adding custom device "/dev/i2c-1": no such file or directory',
      },
    },
  };

  it('includes the output that says what actually went wrong', () => {
    const out = renderEvent(failure);
    assert.match(out ?? '', /FAILED: 10\.0\.99\.75 Start the stack/);
    assert.match(out ?? '', /non-zero return code/);
    assert.match(out ?? '', /rc=1/);
    assert.match(out ?? '', /no such file or directory/, 'the actual cause, previously dropped');
  });

  it('labels which stream each line came from', () => {
    assert.match(renderEvent(failure) ?? '', /stderr: Error response from daemon/);
  });

  it('bounds the output rather than printing a whole result object', () => {
    const noisy = {
      event: 'runner_on_failed',
      event_data: {
        task: 'Noisy',
        host: 'h',
        res: { msg: 'm', stderr: Array.from({ length: 200 }, (_, i) => `line ${i}`).join('\n') },
      },
    };
    const lines = (renderEvent(noisy) ?? '').split('\n');
    assert.ok(lines.length <= 13, `kept ${lines.length} lines`);
    assert.match(renderEvent(noisy) ?? '', /line 199/, 'keeps the tail, where the error is');
  });

  it('a failure with no output still renders its header', () => {
    const bare = { event: 'runner_on_failed', event_data: { task: 'T', host: 'h', res: { msg: 'm' } } };
    assert.equal(renderEvent(bare), '  FAILED: h T -- m');
  });

  it('a task that succeeded still gets one line, not its result object', () => {
    const ok = {
      event: 'runner_on_ok',
      event_data: { task: 'Gathering Facts', host: 'h', res: { changed: false, stdout: 'x'.repeat(5000) } },
    };
    assert.equal(renderEvent(ok), '  ok: h Gathering Facts');
  });
});
