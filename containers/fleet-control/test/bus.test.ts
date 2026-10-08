import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type Server } from 'node:net';
import { Aedes } from 'aedes';
import { configured, nodeOfTopic, readPods, setDesired, type BusConfig } from '../src/bus.js';

/** A real broker on a real socket: the client under test does a real connect and subscribe. */
async function broker(retained: Record<string, string>): Promise<{
  cfg: BusConfig; seen: { topic: string; payload: string; retain: boolean }[]; stop: () => Promise<void>;
}> {
  const aedes = await Aedes.createBroker();
  const seen: { topic: string; payload: string; retain: boolean }[] = [];
  aedes.on('publish', (p, c) => {
    if (c && p.topic.startsWith('rekon/')) {
      seen.push({ topic: p.topic, payload: p.payload.toString(), retain: p.retain === true });
    }
  });
  for (const [topic, payload] of Object.entries(retained)) {
    aedes.publish({ topic, payload: Buffer.from(payload), qos: 0, retain: true } as never, () => {});
  }
  const server: Server = createServer(aedes.handle);
  const port: number = await new Promise((r) => server.listen(0, () => r((server.address() as { port: number }).port)));
  return {
    cfg: { url: `mqtt://127.0.0.1:${port}`, settleMs: 300 },
    seen,
    stop: () => new Promise((r) => server.close(() => aedes.close(() => r()))),
  };
}

const SE = JSON.stringify({
  state: 'ok', node: 'campod-se', build: 'c6dc61e', capture: true, stack: 'running',
  radio: 'closed', data_free_bytes: 12802234368,
  camera: { phase: 'capture', ready: true, frames: 412 },
  accel: { devices: [{ label: 'camera', present: true, self_test: 'pass' }] },
});

test('an empty url disables the bus rather than dialling nothing', () => {
  assert.equal(configured({ url: 'mqtt://x:1883', settleMs: 10 }), true);
  assert.equal(configured({ url: '', settleMs: 10 }), false);
});

test('the topic names the node, and nothing else is read as one', () => {
  assert.equal(nodeOfTopic('rekon/pod/campod-se/status'), 'campod-se');
  assert.equal(nodeOfTopic('rekon/pod/campod-se/desired/stack'), undefined);
  assert.equal(nodeOfTopic('rekon/capture/intent'), undefined);
  assert.equal(nodeOfTopic('rekon/pod/a/b/status'), undefined);
});

test('one subscribe gets every pod, because the documents are retained', async () => {
  // No poll and no persistent subscription: retained means the whole current picture arrives
  // right after SUBACK with nothing to assemble (docs/pod-bus.md).
  const b = await broker({ 'rekon/pod/campod-se/status': SE });
  try {
    const { pods, error } = await readPods(b.cfg);
    assert.equal(error, undefined);
    assert.equal(pods.length, 1);
    const p = pods[0];
    assert.equal(p?.node, 'campod-se');
    assert.equal(p?.ready, true);
    assert.equal(p?.frames, 412);
    assert.equal(p?.phase, 'capture');
    assert.equal(p?.dataFreeBytes, 12802234368);
    // The whole document rides along, so a field added on the writing side is not lost here --
    // the contract keeps no schema in two languages, and `accel` is passed through verbatim.
    assert.ok((p?.raw as { accel?: unknown }).accel);
  } finally { await b.stop(); }
});

test('a document that will not parse is reported against its node, not dropped', async () => {
  // The bus has already been bitten once by a payload read differently than its publisher meant.
  // A reader that silently skips what it cannot read hides that same class of bug.
  const b = await broker({ 'rekon/pod/campod-sw/status': '{broken', 'rekon/pod/campod-se/status': SE });
  try {
    const { pods } = await readPods(b.cfg);
    assert.equal(pods.length, 2);
    assert.equal(pods.find((p) => p.node === 'campod-sw')?.state, 'unreadable');
    assert.equal(pods.find((p) => p.node === 'campod-se')?.state, 'ok');
  } finally { await b.stop(); }
});

test('the document names its own node, over the topic', async () => {
  // A device states its own identity; a per-unit value configured from outside can be wrong,
  // and was (#272).
  const b = await broker({ 'rekon/pod/whatever/status': JSON.stringify({ node: 'campod-ne', state: 'ok' }) });
  try {
    assert.equal((await readPods(b.cfg)).pods[0]?.node, 'campod-ne');
  } finally { await b.stop(); }
});

test('`gone` is carried through, because absence and silence are different', async () => {
  const b = await broker({ 'rekon/pod/campod-nw/status': JSON.stringify({ node: 'campod-nw', state: 'gone' }) });
  try {
    assert.equal((await readPods(b.cfg)).pods[0]?.state, 'gone');
  } finally { await b.stop(); }
});

test('a broker that is not there is an error, not an empty fleet', async () => {
  // An empty result must not be able to mean "no broker" -- that is the difference between
  // "nothing is publishing" and "we could not look".
  const { pods, error } = await readPods({ url: 'mqtt://127.0.0.1:1', settleMs: 100 });
  assert.deepEqual(pods, []);
  assert.match(error ?? '', /ECONNREFUSED|closed/);
});

test('a desired state is published retained, as a bare word', async () => {
  // Retained because that is what a desired state IS on this bus: reconciled every pass and
  // picked up again after a reboot with nobody re-publishing. Bare word because one enum value
  // does not need a wrapper and `mosquitto_sub -v` stays readable.
  const b = await broker({});
  try {
    await setDesired(b.cfg, 'campod-se', 'stack', 'stopped');
    assert.deepEqual(b.seen, [{ topic: 'rekon/pod/campod-se/desired/stack', payload: 'stopped', retain: true }]);
  } finally { await b.stop(); }
});

test('a publish to a broker that is not there rejects rather than reporting success', async () => {
  await assert.rejects(() => setDesired({ url: 'mqtt://127.0.0.1:1', settleMs: 100 }, 'campod-se', 'stack', 'stopped'));
});
