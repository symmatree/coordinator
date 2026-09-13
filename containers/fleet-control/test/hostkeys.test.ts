import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fingerprint, HostKeyStore } from '../src/hostkeys.js';

const fixtures = JSON.parse(
  readFileSync(new URL('./fixtures.json', import.meta.url), 'utf8'),
) as Record<string, { blob: string; fingerprint: string }>;

const ED = fixtures.ed25519!;
const RSA = fixtures.rsa2048!;
const keyA = Buffer.from(ED.blob, 'base64');
const keyB = Buffer.from(RSA.blob, 'base64');
const tmp = () => mkdtempSync(join(tmpdir(), 'fc-test-'));
const store = () => new HostKeyStore(join(tmp(), 'k.json'));

describe('fingerprint', () => {
  // These expectations come from `ssh-keygen -lf`, so this compares our implementation
  // against OpenSSH's rather than against itself. If it drifts, every host-key decision
  // this service makes is silently wrong.
  it('matches ssh-keygen for an ed25519 key', () => {
    assert.equal(fingerprint(keyA), ED.fingerprint);
  });

  it('matches ssh-keygen for an RSA key', () => {
    assert.equal(fingerprint(keyB), RSA.fingerprint);
  });

  it('strips base64 padding, as OpenSSH does', () => {
    assert.ok(!fingerprint(Buffer.from('anything')).includes('='));
  });
});

describe('HostKeyStore', () => {
  it('accepts and records an unknown host (first contact)', () => {
    const s = store();
    const r = s.verify('campod-ne', keyA);
    assert.equal(r.ok, true);
    assert.equal(r.state, 'first-contact');
    assert.equal(s.get('campod-ne'), ED.fingerprint);
  });

  it('accepts the same key on a later contact', () => {
    const s = store();
    s.verify('campod-ne', keyA);
    const r = s.verify('campod-ne', keyA);
    assert.equal(r.ok, true);
    assert.equal(r.state, 'match');
  });

  it('REJECTS a changed key rather than silently trusting it', () => {
    const s = store();
    s.verify('campod-ne', keyA);
    const r = s.verify('campod-ne', keyB);
    assert.equal(r.ok, false);
    assert.equal(r.state, 'mismatch');
    assert.equal(r.ok === false && r.expected, ED.fingerprint);
    assert.equal(r.fingerprint, RSA.fingerprint);
  });

  it('accepts the new key after forget() -- the reflash path', () => {
    const s = store();
    s.verify('campod-ne', keyA);
    assert.equal(s.verify('campod-ne', keyB).ok, false);
    assert.equal(s.forget('campod-ne'), true);
    const r = s.verify('campod-ne', keyB);
    assert.equal(r.ok, true);
    assert.equal(r.state, 'first-contact');
  });

  it('forget() reports false for a node it never held', () => {
    assert.equal(store().forget('campod-nw'), false);
  });

  it('persists across restarts, so trust is not reset by a pod bounce', () => {
    const path = join(tmp(), 'k.json');
    new HostKeyStore(path).verify('coordinator', keyA);
    const reopened = new HostKeyStore(path);
    assert.equal(reopened.get('coordinator'), ED.fingerprint);
    assert.equal(reopened.verify('coordinator', keyB).ok, false);
  });

  it('keeps nodes independent', () => {
    const s = store();
    s.verify('coordinator', keyA);
    s.verify('campod-sw', keyB);
    assert.equal(s.verify('coordinator', keyA).state, 'match');
    assert.equal(s.verify('campod-sw', keyB).state, 'match');
    assert.equal(s.verify('coordinator', keyB).ok, false);
  });

  it('reports ephemeral when the store cannot be persisted', () => {
    // A store whose parent is a regular file: mkdir gives ENOTDIR regardless of uid, so this
    // is deterministic on any machine and for root too. Trust then survives nothing, and the
    // service must be able to SAY so rather than silently degrade to no verification at all.
    const blocker = join(tmp(), 'iam-a-file');
    writeFileSync(blocker, 'not a directory');
    const s = new HostKeyStore(join(blocker, 'nested', 'here.json'));
    assert.equal(s.ephemeral, true);
    assert.equal(s.verify('coordinator', keyA).ok, true);
  });

  it('survives a truncated store file rather than throwing at startup', () => {
    const path = join(tmp(), 'k.json');
    writeFileSync(path, '{"keys": {"coordinator"');
    const s = new HostKeyStore(path);
    assert.equal(s.get('coordinator'), undefined);
    assert.deepEqual(JSON.parse(readFileSync(path, 'utf8')), { keys: {} });
  });
});
