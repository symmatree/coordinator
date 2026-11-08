import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Writable } from 'node:stream';
import { buildServer } from '../src/api.js';
import type { Config } from '../src/config.js';

/** Only what the routes exercised here read; the rest of a Config is never touched. */
function server() {
  const lines: Record<string, unknown>[] = [];
  const stream = new Writable({
    write(chunk, _enc, done) {
      for (const l of String(chunk).split('\n')) if (l !== '') lines.push(JSON.parse(l));
      done();
    },
  });
  const inventory = { nodes: [{ name: 'campod-se', role: 'campod' }] };
  const cfg = {
    inventory,
    action: { github: {} },
    flightsDir: mkdtempSync(join(tmpdir(), 'flights-')),
    bus: { url: '', settleMs: 0 },
    cluster: { groundTlogs: mkdtempSync(join(tmpdir(), 'tlogs-')) },
  } as unknown as Config;
  const app = buildServer(cfg, { level: 'info', stream });
  const requests = () => lines.filter((l) => l.msg === 'request');
  return { app, requests };
}

test('one line per request: route template, arguments, caller and outcome', async () => {
  const { app, requests } = server();
  await app.inject({
    method: 'POST', url: '/nodes/campod-se/stack?desired=stopped',
    headers: { 'user-agent': 'bench_cycle', 'x-forwarded-for': '10.0.1.7' },
  });
  await app.inject({
    method: 'PUT', url: '/flights/2026-11-08-hover/notes',
    payload: { text: 'hover test, wind 3 m/s' },
  });
  const [stack, notes] = requests();
  assert.deepEqual(
    { ...stack, level: undefined, time: undefined, pid: undefined, hostname: undefined, reqId: undefined },
    {
      level: undefined, time: undefined, pid: undefined, hostname: undefined, reqId: undefined,
      msg: 'request',
      route: '/nodes/:name/stack',
      method: 'POST',
      url: '/nodes/campod-se/stack?desired=stopped',
      params: { name: 'campod-se' },
      query: { desired: 'stopped' },
      ua: 'bench_cycle',
      forwardedFor: '10.0.1.7',
      status: 501,
      ms: stack?.ms,
    },
  );
  assert.equal(typeof stack?.ms, 'number');
  assert.equal(notes?.route, '/flights/:flight/notes');
  assert.deepEqual(notes?.body, { text: 'hover test, wind 3 m/s' });
  assert.equal(notes?.status, 200);
});

test('a large or non-JSON body is logged by its size', async () => {
  const { app, requests } = server();
  const text = 'x'.repeat(5000);
  await app.inject({ method: 'PUT', url: '/flights/f/notes', payload: { text } });
  await app.inject({
    method: 'PUT', url: '/flights/f/notes', payload: '# notes\n', headers: { 'content-type': 'text/markdown' },
  });
  const [big, md] = requests();
  assert.deepEqual(big?.body, { bytes: JSON.stringify({ text }).length });
  assert.deepEqual(md?.body, { bytes: 8 });
});

test('an unknown url is logged with no route; /healthz is not logged', async () => {
  const { app, requests } = server();
  await app.inject({ method: 'GET', url: '/healthz' });
  await app.inject({ method: 'GET', url: '/runs/abc' });
  const lines = requests();
  assert.equal(lines.length, 1);
  assert.equal(lines[0]?.route, null);
  assert.equal(lines[0]?.url, '/runs/abc');
  assert.equal(lines[0]?.status, 404);
});
