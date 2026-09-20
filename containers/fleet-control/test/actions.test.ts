import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { reboot, stop } from '../src/actions.js';
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
  it('STOPS FIRST, so an unreachable device fails before any playbook runs', async () => {
    // The ordering is the whole design: the containers get our timeout rather than whatever
    // the shutdown allows. If the play ran first this would fail with ansible-runner's error
    // (which is what `ansible.test.ts` sees, since it is not installed) instead of ssh's.
    const lines: string[] = [];
    await assert.rejects(
      () => reboot(node, ctx, { runId: 'unused' }, (_s, l) => lines.push(l)),
      (err: Error) => {
        assert.match(err.message, /^campod-se: /);
        assert.doesNotMatch(err.message, /ansible-runner/);
        return true;
      },
    );
    // It says what it is doing before it does it, so a run that dies here is legible...
    assert.ok(lines.some((l) => l.includes('rebooting campod-se')), JSON.stringify(lines));
    // ...and it does NOT claim the stack stopped, because it did not.
    assert.ok(!lines.some((l) => l.includes('stack stopped')), JSON.stringify(lines));
  });
});
