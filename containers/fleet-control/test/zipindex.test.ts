import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { imgMemberName, zipMemberNames } from '../src/zipindex.js';

/**
 * Build a real stored-mode zip. Hand-rolled so the test needs no `zip` binary and no
 * dependency -- the parser is exercised against actual bytes rather than a mock.
 */
function makeZip(entries: Array<{ name: string; body: string }>): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  for (const { name, body } of entries) {
    const nameBuf = Buffer.from(name, 'utf8');
    const data = Buffer.from(body, 'utf8');

    const lfh = Buffer.alloc(30);
    lfh.writeUInt32LE(0x04034b50, 0);
    lfh.writeUInt16LE(20, 4);
    lfh.writeUInt32LE(data.length, 18); // compressed size (stored)
    lfh.writeUInt32LE(data.length, 22); // uncompressed size
    lfh.writeUInt16LE(nameBuf.length, 26);
    locals.push(lfh, nameBuf, data);

    const cdh = Buffer.alloc(46);
    cdh.writeUInt32LE(0x02014b50, 0);
    cdh.writeUInt16LE(20, 4);
    cdh.writeUInt16LE(20, 6);
    cdh.writeUInt32LE(data.length, 20);
    cdh.writeUInt32LE(data.length, 24);
    cdh.writeUInt16LE(nameBuf.length, 28);
    cdh.writeUInt32LE(offset, 42); // local header offset
    centrals.push(cdh, nameBuf);

    offset += lfh.length + nameBuf.length + data.length;
  }
  const cd = Buffer.concat(centrals);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(cd.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, eocd]);
}

function write(name: string, buf: Buffer): string {
  const p = join(mkdtempSync(join(tmpdir(), 'zipidx-')), name);
  writeFileSync(p, buf);
  return p;
}

test('reads member names without extracting', () => {
  const p = write('a.zip', makeZip([{ name: 'campod-pi-20260918.img', body: 'not really an image' }]));
  return zipMemberNames(p).then((names) => {
    assert.deepEqual(names, ['campod-pi-20260918.img']);
  });
});

test('finds the one .img among other members', async () => {
  const p = write(
    'b.zip',
    makeZip([
      { name: 'README.txt', body: 'x' },
      { name: 'campod-pi-20260918.img', body: 'y' },
    ]),
  );
  assert.equal(await imgMemberName(p), 'campod-pi-20260918.img');
});

test('refuses to guess when there is not exactly one .img', async () => {
  const none = write('c.zip', makeZip([{ name: 'README.txt', body: 'x' }]));
  await assert.rejects(() => imgMemberName(none), /found 0/);

  const two = write(
    'd.zip',
    makeZip([
      { name: 'a.img', body: 'x' },
      { name: 'b.img', body: 'y' },
    ]),
  );
  await assert.rejects(() => imgMemberName(two), /found 2/);
});

test('says so plainly when handed something that is not a zip', async () => {
  const p = write('e.zip', Buffer.from('this is not a zip file at all'));
  await assert.rejects(() => zipMemberNames(p), /not a zip/);
});
