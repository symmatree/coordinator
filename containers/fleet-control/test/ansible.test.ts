import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { converge, publicKeyOf, renderEvent, runDir } from '../src/ansible.js';

// converge() spawns `ansible-runner`. These tests exercise what this module is actually
// responsible for -- turning the event stream into operator-readable lines, and reporting the
// exit code honestly -- without requiring ansible or a node.

describe('converge -- when ansible-runner is not installed', () => {
  // A REAL key, because converge now derives its public half before it does anything else --
  // the device is told to trust the key we authenticate with, so a converge that cannot state
  // its own key must not proceed. `/dev/null` stood in here while nothing read the path.
  const keyDir = mkdtempSync(join(tmpdir(), 'convergekey-'));
  const keyPath = join(keyDir, 'id');
  execFileSync('ssh-keygen', ['-q', '-t', 'ed25519', '-N', '', '-C', '', '-f', keyPath]);

  /** Run a converge that cannot start, under a run id of our choosing. */
  async function failedConverge(runId: string): Promise<string[]> {
    const lines: string[] = [];
    await assert.rejects(
      () =>
        converge({
          runId,
          host: '10.0.0.1',
          user: 'pi',
          privateKeyPath: keyPath,
          extraVars: { device_role: 'campod' },
          sink: (_stream, line) => lines.push(line),
        }),
      /could not run ansible-runner/,
    );
    return lines;
  }

  it('says so rather than failing as an unexplained nonzero', async () => {
    const id = randomUUID();
    await failedConverge(id);
    rmSync(runDir(id), { recursive: true, force: true });
  });

  // The run that prompted this was a converge whose `coord stop` never returned. Its rendered
  // lines died with the pod, and the runner's own artifacts -- which hold every task's whole
  // result object, and so say HOW it ended -- had already been deleted by a `finally` that
  // did not care whether the play worked.
  it('KEEPS the runner data directory, named after the run', async () => {
    const id = randomUUID();
    const lines = await failedConverge(id);
    // Named after the run, not mkdtemp'd, so a route can find it with no mapping to keep.
    assert.ok(existsSync(runDir(id)), `${runDir(id)} should still be on disk`);
    assert.ok(
      lines.some((l) => l.includes(`/runs/${id}/events`)),
      `nothing pointed at the route: ${JSON.stringify(lines)}`,
    );
    rmSync(runDir(id), { recursive: true, force: true });
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

describe('the fleet key', () => {
  it('the fleet public key is derived from the key we authenticate with', () => {
  // The whole point: what a device is told to trust is derived from the private key this
  // process actually presents, so there is no second copy to disagree with it. A committed
  // literal and the cluster secret did exactly that and locked the service out of a fresh card.
  const dir = mkdtempSync(join(tmpdir(), 'fleetkey-'));
  execFileSync('ssh-keygen', ['-q', '-t', 'ed25519', '-N', '', '-C', '', '-f', join(dir, 'id')]);
  const expected = execFileSync('ssh-keygen', ['-y', '-f', join(dir, 'id')], { encoding: 'utf8' }).trim();

  const got = publicKeyOf(join(dir, 'id'));
  assert.equal(got, expected);
  assert.match(got, /^ssh-ed25519 AAAA/);
  // It contains a space, which is why it travels as JSON extravars and not `-e key=value`:
  // ansible's k=v form stops at the first space and silently yields "ssh-ed25519".
  assert.ok(got.includes(' '));
  });

  it('a key it cannot read throws rather than converging without one', () => {
  // Returning empty would have `ssh-access` write an authorized_keys with no fleet key --
  // locking this service out of the device at the end of its own successful run.
  assert.throws(() => publicKeyOf(join(tmpdir(), 'no-such-key-at-all')));
  });

});
