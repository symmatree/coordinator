import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendFileSync, mkdtempSync, renameSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FcStatusReader, parseTlog } from '../src/fcstatus.js';

const FC = 1;
const T0 = Date.parse('2026-11-03T15:00:00Z');

/** One tlog record: the ground's microsecond stamp, then a MAVLink v2 frame (CRC not checked). */
function record(ms: number, msgid: number, payload: Buffer, sysid = FC, compid = 1): Buffer {
  const stamp = Buffer.alloc(8);
  stamp.writeBigUInt64BE(BigInt(ms) * 1000n);
  const head = Buffer.from([0xfd, payload.length, 0, 0, 0, sysid, compid, msgid & 0xff, (msgid >> 8) & 0xff, msgid >> 16]);
  return Buffer.concat([stamp, head, payload, Buffer.from([0, 0])]);
}

function heartbeat(ms: number, armed: boolean, autopilot = 3): Buffer {
  return record(ms, 0, Buffer.from([0, 0, 0, 0, 2, autopilot, armed ? 0x81 : 0x01, 3, 3]));
}

function statustext(ms: number, severity: number, text: string, id = 0, seq = 0): Buffer {
  const p = Buffer.alloc(54);
  p[0] = severity;
  p.write(text, 1, 50, 'latin1');
  p.writeUInt16LE(id, 51);
  p[53] = seq;
  // A v2 sender trims trailing zeros; the reader has to pad them back.
  let n = p.length;
  while (n > 1 && p[n - 1] === 0) n--;
  return record(ms, 253, p.subarray(0, n));
}

function armAck(ms: number, result: number): Buffer {
  const p = Buffer.alloc(3);
  p.writeUInt16LE(400, 0);
  p[2] = result;
  return record(ms, 77, p, FC, 1);
}

test('a record cut off by the end of the buffer is left for the next read', () => {
  const whole = Buffer.concat([heartbeat(T0, false), statustext(T0 + 1000, 3, 'PreArm: GPS 1: Bad fix')]);
  const cut = whole.subarray(0, whole.length - 5);
  const { frames, used } = parseTlog(cut);
  assert.equal(frames.length, 1);
  assert.equal(used, heartbeat(T0, false).length);
  assert.equal(parseTlog(whole).frames.length, 2);
});

test('PreArm text, arm, an arm command result and disarm, newest first with ground times', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mlrs-'));
  const part = join(dir, '20261103T150000Z.tlog.part');
  writeFileSync(part, Buffer.concat([
    heartbeat(T0, false),
    statustext(T0 + 1000, 3, 'PreArm: GPS 1: Bad fix'),
    armAck(T0 + 2000, 4),
  ]));
  const r = new FcStatusReader(dir);
  let s = await r.status();
  assert.equal(s.armed, false);
  assert.equal(s.file, '20261103T150000Z.tlog.part');
  assert.deepEqual(s.events.map((e) => [e.t, e.kind, e.severity, e.text]), [
    ['2026-11-03T15:00:02.000Z', 'arm-command', 'failed', 'arm/disarm command failed'],
    ['2026-11-03T15:00:01.000Z', 'text', 'error', 'PreArm: GPS 1: Bad fix'],
  ]);

  // Appended since: only the new bytes are read, and the arm shows as an event.
  appendFileSync(part, Buffer.concat([armAck(T0 + 3000, 0), heartbeat(T0 + 3100, true)]));
  s = await r.status();
  assert.equal(s.armed, true);
  assert.equal(s.lastHeard, '2026-11-03T15:00:03.100Z');
  assert.deepEqual(s.events.slice(0, 2).map((e) => e.kind), ['armed', 'arm-command']);
  assert.equal(s.events.length, 4);
});

test('when tlog-split rolls at disarm, the rest of the old file is read under its new name', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mlrs-'));
  const first = join(dir, '20261103T150000Z.tlog.part');
  writeFileSync(first, heartbeat(T0, true));
  const r = new FcStatusReader(dir);
  await r.status();

  // Between two requests: the disarm and the text around it land, the file is closed and renamed,
  // and a new one starts.
  appendFileSync(first, Buffer.concat([statustext(T0 + 1000, 2, 'Potential Thrust Loss (3)'), heartbeat(T0 + 2000, false)]));
  renameSync(first, join(dir, '20261103T150000Z-armed-20261103T150000Z-disarmed-20261103T150002Z.tlog'));
  writeFileSync(join(dir, '20261103T150002Z.tlog.part'), statustext(T0 + 3000, 4, 'PreArm: Check mag field'));

  const s = await r.status();
  assert.equal(s.file, '20261103T150002Z.tlog.part');
  assert.deepEqual(s.events.map((e) => e.text), ['PreArm: Check mag field', 'disarmed', 'Potential Thrust Loss (3)']);
});

test('long status text arrives in chunks and is shown as one message', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mlrs-'));
  const a = 'PreArm: Battery 1 below minimum arming voltage of '.slice(0, 50);
  writeFileSync(join(dir, '20261103T150000Z.tlog.part'), Buffer.concat([
    heartbeat(T0, false),
    statustext(T0 + 1, 3, a, 7, 0),
    statustext(T0 + 2, 3, '22.00V', 7, 1),
  ]));
  const s = await new FcStatusReader(dir).status();
  assert.deepEqual(s.events.map((e) => e.text), [a + '22.00V']);
});

test('a heartbeat from something other than the autopilot does not set arm state', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mlrs-'));
  // MAV_AUTOPILOT_INVALID (8): a GCS or the radio, not the FC.
  writeFileSync(join(dir, '20261103T150000Z.tlog.part'), heartbeat(T0, true, 8));
  const s = await new FcStatusReader(dir).status();
  assert.equal(s.armed, undefined);
  assert.equal(s.lastHeard, undefined);
});

test('no open tlog is reported, not thrown', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mlrs-'));
  const s = await new FcStatusReader(dir).status();
  assert.match(s.error ?? '', /no open tlog/);
  const missing = await new FcStatusReader(join(dir, 'nope')).status();
  assert.match(missing.error ?? '', /ENOENT/);
});
