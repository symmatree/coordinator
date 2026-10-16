import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type Server } from 'node:net';
import { Aedes } from 'aedes';
import { quiesce } from '../src/quiesce.js';
import type { ActionContext } from '../src/actions.js';

/**
 * A real broker, and a fake pod-link on the other side of it.
 *
 * The device half matters here: the thing being tested is a conversation, and the half of it
 * that was easiest to get wrong -- telling the retained status from the answer -- is only
 * exercised if something is actually retaining a status and then replying.
 */
async function bench(opts: {
  /** The status the broker already holds, as a device that has been up a while would. */
  retained?: Record<string, unknown>;
  /** What the fake device publishes when it sees a quiesce. Absent: it never answers. */
  reply?: (reason: string) => Record<string, unknown> | undefined;
  node?: string;
} = {}) {
  const node = opts.node ?? 'campod-se';
  const aedes = await Aedes.createBroker();
  const reasons: string[] = [];
  if (opts.retained) {
    aedes.publish({
      topic: `rekon/pod/${node}/status`,
      payload: Buffer.from(JSON.stringify(opts.retained)),
      qos: 0, retain: true,
    } as never, () => {});
  }
  aedes.on('publish', (p) => {
    if (p.topic !== `rekon/pod/${node}/quiesce`) return;
    reasons.push(p.payload.toString());
    const doc = opts.reply?.(p.payload.toString());
    if (!doc) return;
    aedes.publish({
      topic: `rekon/pod/${node}/status`,
      payload: Buffer.from(JSON.stringify(doc)),
      qos: 0, retain: true,
    } as never, () => {});
  });
  const server: Server = createServer(aedes.handle);
  const port: number = await new Promise((r) =>
    server.listen(0, () => r((server.address() as { port: number }).port)));
  const ctx = {
    inventory: { user: 'pi', nodes: [] },
    privateKeyPath: '/dev/null',
    sshTimeoutSec: 2,
    bus: { url: `mqtt://127.0.0.1:${port}`, settleMs: 200 },
  } as ActionContext;
  return { ctx, node, reasons, stop: () => new Promise<void>((r) => server.close(() => aedes.close(() => r()))) };
}

const IDLE = { state: 'ok', node: 'campod-se', capture: false };

test('a device that reports a fresh quiesce resolves, and is told why', async () => {
  const b = await bench({
    retained: { ...IDLE, last_quiesce_at_boot_s: 100 },
    reply: () => ({ ...IDLE, last_quiesce_at_boot_s: 900, last_quiesce_units: 'campod-camera campod-accel' }),
  });
  try {
    await quiesce(b.node, b.ctx, 'probe');
    // The reason reaches the device. It is what shows up in pod-link's log when someone asks
    // later why collection stopped, so it is not decoration.
    assert.deepEqual(b.reasons, ['probe']);
  } finally {
    await b.stop();
  }
});

test('the retained status from before is NOT taken for the answer', async () => {
  // The failure this exists to prevent: the device last quiesced an hour ago, the broker
  // replays that status on subscribe, and we report success without the device having
  // stopped anything -- then hand a still-capturing box to a caller that assumes it is idle.
  const b = await bench({ retained: { ...IDLE, last_quiesce_at_boot_s: 100 } });
  try {
    await assert.rejects(() => quiesce(b.node, b.ctx, 'probe', 1_500), /^Error: campod-se: did not report a quiesce/);
  } finally {
    await b.stop();
  }
});

test('an unchanged last_quiesce_at_boot_s is not an answer either', async () => {
  // An out-of-band heartbeat, published after we asked but before the units were out. Fresh
  // by arrival, stale by value -- which is why the discriminator is the value.
  const b = await bench({
    retained: { ...IDLE, last_quiesce_at_boot_s: 100 },
    reply: () => ({ ...IDLE, last_quiesce_at_boot_s: 100 }),
  });
  try {
    await assert.rejects(() => quiesce(b.node, b.ctx, 'probe', 1_500), /^Error: campod-se: did not report a quiesce/);
  } finally {
    await b.stop();
  }
});

test('a device with no retained status still has its first answer heard', async () => {
  // A box that just booted has published nothing yet. Nothing is replayed on subscribe, so
  // the first message to arrive IS the reply, and must not be spent establishing a baseline.
  const b = await bench({
    reply: () => ({ ...IDLE, last_quiesce_at_boot_s: 12, last_quiesce_units: 'campod-camera' }),
  });
  try {
    await quiesce(b.node, b.ctx, 'probe');
  } finally {
    await b.stop();
  }
});

test('a device that reports a quiesce error fails the caller', async () => {
  // Not advisory. The caller's next move is a command that assumes an idle box.
  const b = await bench({
    retained: { ...IDLE, last_quiesce_at_boot_s: 100 },
    reply: () => ({
      ...IDLE, last_quiesce_at_boot_s: 900,
      last_quiesce_units: 'campod-camera', last_quiesce_error: 'campod-camera still active after 60s',
    }),
  });
  try {
    await assert.rejects(() => quiesce(b.node, b.ctx, 'probe'),
      /^Error: campod-se: did not quiesce cleanly: campod-camera still active/);
  } finally {
    await b.stop();
  }
});

test('no bus configured is a failure, not a silent skip', async () => {
  // THE WHOLE POINT. There is no ssh fallback: if this returned quietly, every command the
  // service sends would go to a capturing device, which is the state the quiesce exists for.
  const ctx = {
    inventory: { user: 'pi', nodes: [] },
    privateKeyPath: '/dev/null',
    sshTimeoutSec: 2,
    bus: { url: '', settleMs: 200 },
  } as ActionContext;
  await assert.rejects(() => quiesce('campod-se', ctx, 'probe'), /^Error: campod-se: cannot quiesce, no pod bus configured/);
});

test('an unreachable broker fails rather than falling back to ssh', async () => {
  const ctx = {
    inventory: { user: 'pi', nodes: [] },
    privateKeyPath: '/dev/null',
    sshTimeoutSec: 2,
    // Nothing listens here. Port 1 is reserved and the connect is refused immediately.
    bus: { url: 'mqtt://127.0.0.1:1', settleMs: 200 },
  } as ActionContext;
  await assert.rejects(() => quiesce('campod-se', ctx, 'probe'));
});
