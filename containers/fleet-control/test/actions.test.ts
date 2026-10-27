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
  // No broker. Reboot must not care; stop must say so rather than reach for ssh.
  bus: { url: '', settleMs: 10 },
  // Neither reboot nor stop submits a workflow, so these only have to exist.
  workflows: { namespace: 'fleet-control', template: 'fleet-control-playbook' },
  github: { repo: 'o/r', workflow: 'ansible-payload.yaml', ref: 'main' },
};
const node = { name: 'campod-se', role: 'campod', host: '203.0.113.9' };

describe('stop', () => {
  it('names the node in its failure rather than reporting a bare transport error', async () => {
    await assert.rejects(() => stop(node, ctx), /^Error: campod-se: /);
  });

  it('goes over the bus, so no bus is a refusal and not a quiet ssh fallback', async () => {
    // The device half of this is campod-se's pod-link. Without a broker there is no path to
    // it, and the old `sudo pkill` over ssh is deliberately not waiting behind it.
    await assert.rejects(() => stop(node, ctx), /no pod bus configured/);
  });
});

describe('reboot', () => {
  it('is ONE command, gated on nothing -- no stop is attempted first', async () => {
    // A reboot is the way out of a stuck box, so it must not depend on anything else
    // working. If it quiesced first, an unreachable device would fail on the stop and say
    // so -- `stop` above refuses without a bus, and that refusal must not appear here.
    await assert.rejects(
      () => reboot(node, ctx),
      (e: Error) => /^campod-se: /.test(e.message) && !/pod bus|quiesce|stop/i.test(e.message),
    );
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
