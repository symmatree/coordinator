import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync } from 'node:crypto';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadPrivateKey } from '../src/keys.js';

const dir = mkdtempSync(join(tmpdir(), 'fc-keys-'));
const write = (name: string, body: string) => {
  const p = join(dir, name);
  writeFileSync(p, body);
  return p;
};

describe('loadPrivateKey', () => {
  it('accepts a key ssh2 can use', () => {
    const { privateKey } = generateKeyPairSync('rsa', {
      modulusLength: 2048,
      privateKeyEncoding: { type: 'pkcs1', format: 'pem' },
      publicKeyEncoding: { type: 'pkcs1', format: 'pem' },
    });
    assert.equal(loadPrivateKey(write('good', privateKey)), privateKey);
  });

  it('REJECTS a key ssh2 cannot use, at startup rather than on first action', () => {
    // PKCS8 is one such: ssh2 answers "Unsupported key format". The point is not which
    // formats it takes, but that an unusable key stops the service starting.
    const { privateKey } = generateKeyPairSync('ed25519', {
      privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
      publicKeyEncoding: { type: 'spki', format: 'pem' },
    });
    assert.throws(() => loadPrivateKey(write('pkcs8', privateKey)), /not one ssh2 can use/);
  });

  it('names the path when the key is missing', () => {
    assert.throws(() => loadPrivateKey(join(dir, 'nope')), /cannot read the private key/);
  });

  it('names the path when the file is not a key at all', () => {
    assert.throws(() => loadPrivateKey(write('junk', 'nope')), /not one ssh2 can use/);
  });
});
