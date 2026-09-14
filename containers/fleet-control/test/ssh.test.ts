import { describe, it, after } from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync } from 'node:crypto';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import ssh2 from 'ssh2';
const { Server } = ssh2;
import { NodeSession, type SessionOptions } from '../src/ssh.js';
import { HostKeyStore } from '../src/hostkeys.js';
import type { FleetNode } from '../src/inventory.js';

const rsa = () =>
  generateKeyPairSync('rsa', {
    modulusLength: 2048,
    privateKeyEncoding: { type: 'pkcs1', format: 'pem' },
    publicKeyEncoding: { type: 'pkcs1', format: 'pem' },
  });

const dir = mkdtempSync(join(tmpdir(), 'fc-ssh-'));
const hostKey = rsa().privateKey;
const clientKeyPath = join(dir, 'id');
writeFileSync(clientKeyPath, rsa().privateKey);

/**
 * A server that authenticates, then destroys the socket the moment a command is run --
 * which is what a node does when it reboots.
 */
function dropOnExec(): Promise<{ port: number; close: () => void }> {
  const server = new Server({ hostKeys: [hostKey] }, (client) => {
    client.on('authentication', (ctx) => ctx.accept());
    client.on('ready', () => {
      client.on('session', (accept) => {
        const session = accept();
        session.on('exec', (acceptExec) => {
          // Answer normally, THEN reset the TCP connection a moment later -- which is the
          // real sequence: the reboot command's promise settles, and the reset lands
          // afterwards when nothing is pending.
          const stream = acceptExec();
          stream.exit(0);
          stream.end();
          setTimeout(() => {
            // @ts-expect-error -- reaching for the raw socket is the point of the test
            const sock = client._sock;
            if (sock?.resetAndDestroy) sock.resetAndDestroy();
            else sock?.destroy(new Error('ECONNRESET'));
          }, 30);
        });
      });
    });
    client.on('error', () => {});
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve({ port: (server.address() as { port: number }).port, close: () => server.close() });
    });
  });
}

const opts = (port: number): SessionOptions => ({
  user: 'pi',
  privateKeyPath: clientKeyPath,
  hostKeys: new HostKeyStore(join(dir, `hk-${port}.json`)),
  timeoutMs: 5_000,
});

const testNode: FleetNode = { name: 'test-node', host: '127.0.0.1', role: 'campod' };

describe('NodeSession -- a peer that drops the connection', () => {
  const servers: Array<() => void> = [];
  after(() => servers.forEach((c) => c()));

  it('does not kill the process when the peer resets mid-command', async () => {
    // Regression: ssh2's Client emits 'error' on its own emitter, asynchronously, after the
    // pending promise settles. With no listener that terminates the PROCESS -- which is what
    // happened on campod-se's bootstrap, because bootstrap reboots the node on purpose.
    const { port, close } = await dropOnExec();
    servers.push(close);

    const session = await NodeSession.open(testNode, { ...opts(port), port });
    await session.exec('true');

    // Do NOT dispose: the point is an error arriving while the session sits idle, which is
    // where the real crash happened -- after the reboot command had already settled.
    await new Promise((r) => setTimeout(r, 400));
    assert.ok(true, 'process survived the reset');
    session.dispose();
  });
});
