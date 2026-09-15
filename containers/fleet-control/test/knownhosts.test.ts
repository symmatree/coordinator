import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { forgetHostKey } from '../src/knownhosts.js';

// These drive the real ssh-keygen against real files, which is the point: the bug was in what
// the binary does, not in what we believed it does.
const tmp = () => join(mkdtempSync(join(tmpdir(), 'fc-kh-')), 'known_hosts');

describe('forgetHostKey', () => {
  it('is NOT an error when known_hosts does not exist', async () => {
    // The regression: a freshly started pod has never connected to anything, so the file is
    // absent and ssh-keygen exits non-zero. Every converge failed in under a second on this.
    assert.equal(await forgetHostKey('10.0.5.237', tmp()), false);
  });

  it('reports false when the host is simply not recorded', async () => {
    const path = tmp();
    writeFileSync(path, 'other.example ssh-ed25519 AAAAC3Nza\n');
    assert.equal(await forgetHostKey('10.0.5.237', path), false);
  });

  it('removes a recorded host and says so', async () => {
    const path = tmp();
    writeFileSync(path, '10.0.5.237 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAbgqMtIZuknRiE3MQ\n');
    assert.equal(await forgetHostKey('10.0.5.237', path), true);
    assert.ok(!readFileSync(path, 'utf8').includes('10.0.5.237'));
  });
});
