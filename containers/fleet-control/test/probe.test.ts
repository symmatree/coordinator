import { test } from 'node:test';
import assert from 'node:assert/strict';
import { probeNode } from '../src/probe.js';
import type { ActionContext } from '../src/actions.js';

const ctx = (over: Partial<ActionContext> = {}): ActionContext => ({
  inventory: { user: 'pi', nodes: [] },
  privateKeyPath: '/dev/null',
  sshTimeoutSec: 1,
  knownHostsPath: '/tmp/probe-test-known-hosts',
  ...over,
});

const node = { name: 'nowhere', role: 'campod', host: '203.0.113.1' };

test('a node that cannot be reached is a status, not an exception', async () => {
  // 203.0.113.0/24 is TEST-NET-3: reserved, routable nowhere. The point is that one bad
  // machine returns a row rather than emptying the screen for the rest.
  const got = await probeNode(node, ctx());
  assert.equal(got.node, 'nowhere');
  assert.equal(got.probe, undefined);
  assert.ok(got.error, 'carries a reason');
  assert.ok(got.probedAt, 'and when we asked');
});

test('the failure names a cause and how long it took', async () => {
  // The bug this covers: a process killed by our own timeout has EMPTY stderr, so the old
  // fallback rendered node-js's generic `Command failed: ssh ...`. That reads as though the
  // device refused us, when in fact it answered nothing in time -- which is a different
  // problem with a different fix, and it sent me looking for missing containers.
  const got = await probeNode(node, ctx());
  assert.doesNotMatch(
    got.error ?? '',
    /^Command failed: ssh/,
    'must not be the bare node-js message',
  );
  assert.match(got.error ?? '', /after \d+s|within \d+s/, 'says how long it spent');
});
