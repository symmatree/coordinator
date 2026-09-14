import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { runDetached, type Runner } from '../src/detached.js';
import { HostKeyMismatchError } from '../src/ssh.js';
import type { FleetNode } from '../src/inventory.js';
import type { SessionOptions } from '../src/ssh.js';

// The follow-loop contract, exercised without a network: incremental reads, the status
// handshake, re-attach, and the vanished-process case.
const node: FleetNode = { name: 'campod-se', host: '10.0.5.237', role: 'campod' };
const opts = {} as SessionOptions;

/** A node whose shell returns a scripted sequence, recording what it was asked. */
function scripted(responses: string[]) {
  const calls: string[] = [];
  let i = 0;
  const runner: Runner = async (command) => {
    calls.push(command);
    return { code: 0, stdout: responses[Math.min(i++, responses.length - 1)] ?? '', stderr: '' };
  };
  return { runner, calls };
}

describe('runDetached', () => {
  it('detaches the work from the SSH session that started it', async () => {
    const { runner, calls } = scripted(['STARTED\n', 'hello\n___FC_EOF___0']);
    const r = await runDetached(node, opts, 'one-time', 'true', undefined, 1, runner);
    assert.equal(r.code, 0);
    assert.equal(r.attached, false);
    // These two are the whole point: a new session so closing the channel cannot SIGHUP the
    // work, and output to a file rather than down the channel.
    assert.ok(calls[0]!.includes('setsid'), 'must start the work with setsid');
    assert.ok(calls[0]!.includes('> /tmp/fleet-control/one-time/log') ||
              calls[0]!.includes('>/tmp/fleet-control/one-time/log') ||
              calls[0]!.includes('${dir}/log') || calls[0]!.includes('/log'),
              'output must go to a file on the node');
  });

  it('passes the command base64-encoded, so quoting cannot corrupt it', async () => {
    const { runner, calls } = scripted(['STARTED\n', '___FC_EOF___0']);
    await runDetached(node, opts, 'x', `sh -c 'weird "$quoting" && stuff'`, undefined, 1, runner);
    assert.ok(calls[0]!.includes('base64 -d'));
    assert.ok(calls[0]!.includes(Buffer.from(`sh -c 'weird "$quoting" && stuff'`).toString('base64')));
  });

  it('returns the real exit code rather than swallowing it', async () => {
    const { runner } = scripted(['STARTED\n', 'boom\n___FC_EOF___2']);
    assert.equal((await runDetached(node, opts, 'x', 'false', undefined, 1, runner)).code, 2);
  });

  it('ATTACHES to an already-running job instead of starting a second one', async () => {
    const { runner } = scripted(['ATTACHED\n', '___FC_EOF___0']);
    const r = await runDetached(node, opts, 'one-time', 'true', undefined, 1, runner);
    assert.equal(r.attached, true);
  });

  it('streams incrementally and does not repeat what it already read', async () => {
    const seen: string[] = [];
    const { runner, calls } = scripted([
      'STARTED\n',
      'one\ntwo\n___FC_EOF___',  // running, 8 bytes consumed
      'ALIVE\n',                 // liveness probe
      'three\n___FC_EOF___0',    // finished
    ]);
    await runDetached(node, opts, 'x', 'true', (_s, l) => seen.push(l), 1, runner);
    assert.deepEqual(seen.filter((l) => !l.startsWith('[fleet-control]')), ['one', 'two', 'three']);
    // The second read must start after the first chunk, or output duplicates on every poll.
    assert.ok(calls.some((c) => c.includes('tail -c +9 ')), `expected an offset read, got: ${calls.join(' | ')}`);
  });

  it('FAILS LOUDLY when the process vanished without writing a status', async () => {
    // The node rebooted, or the work was killed mid-step. Polling forever against a file
    // nothing will update would look identical to "still working".
    const { runner } = scripted(['STARTED\n', '___FC_EOF___', 'GONE\n']);
    await assert.rejects(
      () => runDetached(node, opts, 'x', 'true', undefined, 1, runner),
      /vanished without writing an exit status/,
    );
  });

  it('keeps waiting while the process is still alive', async () => {
    let polls = 0;
    const runner: Runner = async (command) => {
      if (command.includes('setsid')) return { code: 0, stdout: 'STARTED\n', stderr: '' };
      if (command.includes('kill -0')) return { code: 0, stdout: 'ALIVE\n', stderr: '' };
      polls += 1;
      return { code: 0, stdout: polls < 3 ? '___FC_EOF___' : '___FC_EOF___0', stderr: '' };
    };
    assert.equal((await runDetached(node, opts, 'x', 'true', undefined, 1, runner)).code, 0);
    assert.equal(polls, 3);
  });
});

describe('runDetached -- surviving a lost link', () => {
  // The reason for detaching at all: the work belongs to the node, so losing the connection
  // must not fail the run. Failing on the first missed poll would give up the property we
  // detached to get.
  const flaky = (failures: number, then: string[]): Runner => {
    let started = false;
    let failed = 0;
    let i = 0;
    return async (command) => {
      if (!started) { started = true; return { code: 0, stdout: 'STARTED\n', stderr: '' }; }
      if (command.includes('kill -0')) return { code: 0, stdout: 'ALIVE\n', stderr: '' };
      if (failed < failures) { failed += 1; throw new Error('Timed out while waiting for handshake'); }
      return { code: 0, stdout: then[Math.min(i++, then.length - 1)]!, stderr: '' };
    };
  };

  it('keeps waiting through a transient outage and recovers', async () => {
    const seen: string[] = [];
    const r = await runDetached(
      node, opts, 'one-time', 'true', (_s, l) => seen.push(l), 1,
      flaky(3, ['done\n___FC_EOF___0']), { unreachableToleranceMs: 60_000 },
    );
    assert.equal(r.code, 0);
    assert.ok(seen.some((l) => l.includes('not answering')), 'should report the outage');
    assert.ok(seen.some((l) => l.includes('answering again')), 'should report recovery');
    assert.ok(seen.includes('done'), 'should still deliver the output');
  });

  it('gives up once the outage passes the tolerance, and says the state is UNKNOWN', async () => {
    await assert.rejects(
      () => runDetached(node, opts, 'x', 'true', undefined, 1, flaky(999, []), { unreachableToleranceMs: 5 }),
      /unreachable for .* past the .* tolerance[\s\S]*UNKNOWN/,
    );
  });

  it('never retries past a host-key mismatch', async () => {
    let started = false;
    const runner: Runner = async () => {
      if (!started) { started = true; return { code: 0, stdout: 'STARTED\n', stderr: '' }; }
      throw new HostKeyMismatchError('campod-se', 'SHA256:new', 'SHA256:old');
    };
    await assert.rejects(
      () => runDetached(node, opts, 'x', 'true', undefined, 1, runner, { unreachableToleranceMs: 60_000 }),
      /host key changed/,
    );
  });

  it('stops at the deadline, and says the work was NOT stopped', async () => {
    const runner: Runner = async (command) => ({
      code: 0,
      stdout: command.includes('setsid') ? 'STARTED\n' : '___FC_EOF___',
      stderr: '',
    });
    await assert.rejects(
      () => runDetached(node, opts, 'x', 'true', undefined, 1, runner, { deadlineMs: 5 }),
      /has NOT been stopped/,
    );
  });
});

describe('runDetached -- the status/liveness race', () => {
  // Observed on campod-se at load average 7.8: the command finished between our status read
  // and the liveness check that follows it, so a SUCCESSFUL run was reported as "vanished
  // without writing an exit status". The two reads are separate round trips; the second one
  // has to re-read the status rather than trusting the pid alone.
  it('believes a status that appears during the liveness check', async () => {
    const responses = [
      'STARTED\n',
      'working\n___FC_EOF___',      // poll: output, no status yet
      'GONE\n___FC_EOF___0',        // liveness: pid gone, but status is there now
      'done\n',                      // drain of remaining output
    ];
    let i = 0;
    const seen: string[] = [];
    const runner: Runner = async () => ({
      code: 0,
      stdout: responses[Math.min(i++, responses.length - 1)]!,
      stderr: '',
    });
    const r = await runDetached(node, opts, 'apt-git', 'true', (_s, l) => seen.push(l), 1, runner);
    assert.equal(r.code, 0);
    assert.ok(seen.includes('working'), 'output before the race must survive');
    assert.ok(seen.includes('done'), 'output written after the last poll must be drained');
  });

  it('still reports a genuine vanish -- pid gone AND no status', async () => {
    const responses = ['STARTED\n', '___FC_EOF___', 'GONE\n___FC_EOF___'];
    let i = 0;
    const runner: Runner = async () => ({
      code: 0,
      stdout: responses[Math.min(i++, responses.length - 1)]!,
      stderr: '',
    });
    await assert.rejects(
      () => runDetached(node, opts, 'x', 'true', undefined, 1, runner),
      /vanished without writing an exit status/,
    );
  });
});
