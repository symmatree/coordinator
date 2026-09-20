import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { reboot, rebootStarted, stop } from '../src/actions.js';
import type { ActionContext } from '../src/actions.js';

// No ansible and no device. TEST-NET-3 (203.0.113.0/24) is reserved and routable nowhere, so
// these reach a real ssh and a real failure without touching anything that exists.
const ctx: ActionContext = {
  inventory: { user: 'pi', nodes: [] },
  privateKeyPath: '/dev/null',
  // Short: the point is the ordering, not how patient ssh is.
  sshTimeoutSec: 2,
  knownHostsPath: '/dev/null',
};
const node = { name: 'campod-se', role: 'campod', host: '203.0.113.9' };

describe('stop', () => {
  it('names the node in its failure rather than reporting a bare ssh error', async () => {
    await assert.rejects(() => stop(node, ctx), /^Error: campod-se: /);
  });
});

describe('reboot', () => {
  it('is ONE command, gated on nothing -- no stop is attempted first', async () => {
    // A reboot is the way out of a stuck box, so it must not depend on anything else
    // working. If it quiesced first, an unreachable device would say so about the stop.
    const lines: string[] = [];
    await assert.rejects(() => reboot(node, ctx, (_s, l) => lines.push(l)));
    assert.ok(!lines.some((l) => /stop/i.test(l)), JSON.stringify(lines));
    assert.deepEqual(lines, ['[fleet-control] rebooting campod-se (203.0.113.9)']);
  });

  it('fails for real when the device was never reached', async () => {
    // A connect timeout is not a reboot. Distinguishing the two is the whole job of
    // rebootStarted, so the unreachable case must still surface as a failure.
    await assert.rejects(() => reboot(node, ctx), /^Error: campod-se: /);
  });
});

describe('rebootStarted', () => {
  // sshd is killed a moment after the request is accepted, so the exit status can be lost in
  // transit even though the reboot is happening. Reading that as success is only safe if it
  // is distinguishable from never having got a command in.
  it('reads a dropped connection as the device going down', () => {
    for (const said of [
      'Connection to 10.0.5.237 closed by remote host.',
      'client_loop: send disconnect: Broken pipe',
      'Connection reset by 10.0.5.237 port 22',
    ]) {
      assert.equal(rebootStarted(said), true, said);
    }
  });

  it('does NOT read never-reached as the device going down', () => {
    for (const said of [
      'ssh: connect to host 203.0.113.9 port 22: Connection timed out',
      'ssh: connect to host 10.0.5.237 port 22: Connection refused',
      'pi@10.0.5.237: Permission denied (publickey).',
      'Host key verification failed.',
      '',
    ]) {
      assert.equal(rebootStarted(said), false, JSON.stringify(said));
    }
  });
});
