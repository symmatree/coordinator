import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildServer } from '../src/api.js';
import { RunRegistry } from '../src/runs.js';
import type { Config } from '../src/config.js';

/**
 * `GET /runs/:id` when this process has never heard of the run.
 *
 * The registry is in memory and a workflow outlives it, so after a pod roll the run id is
 * all a caller has left -- the workflow's name was in the lines that died with it.
 */

/** A kubectl that answers `get workflows -l ...` from a fixed list. */
function fakeKubectl(items: string): string {
  const dir = mkdtempSync(join(tmpdir(), 'runlookup-'));
  writeFileSync(
    join(dir, 'kubectl'),
    `#!/usr/bin/env python3\nprint(${JSON.stringify(`{"items": ${items}}`)})\n`,
  );
  chmodSync(join(dir, 'kubectl'), 0o755);
  return dir;
}

function serve(items: string, runs = new RunRegistry()) {
  const dir = fakeKubectl(items);
  const realPath = process.env.PATH;
  process.env.PATH = `${dir}:${realPath ?? ''}`;
  const cfg = {
    inventory: { user: 'pi', nodes: [] },
    action: {
      inventory: { user: 'pi', nodes: [] },
      privateKeyPath: '/dev/null',
      sshTimeoutSec: 2,
      workflows: { namespace: 'fleet-control', template: 'fleet-control-playbook' },
      github: { repo: 'o/r', workflow: 'ansible-payload.yaml', ref: 'main' },
      bus: { url: '', settleMs: 10 },
    },
    bus: { url: '', settleMs: 10 },
    notify: {},
    // buildServer reaches into these at construction time; nothing under test uses them.
    images: { repo: 'o/r', workflow: 'w.yaml', ref: 'main', cacheDir: '/tmp', publicUrl: 'http://x' },
    cluster: {},
    flightsDir: '/tmp',
  } as unknown as Config;
  const app = buildServer(cfg, runs);
  return { app, runs, restore: async () => { process.env.PATH = realPath; await app.close(); } };
}

const RUNNING = '[{"metadata": {"name": "converge-campod-se-abc"}, "status": {"phase": "Running"}}]';

test('a run this process never saw is answered from the cluster, not 404d', async () => {
  const s = serve(RUNNING);
  try {
    const res = await s.app.inject({ method: 'GET', url: '/runs/lost-run' });
    assert.equal(res.statusCode, 200);
    const body = res.json() as Record<string, unknown>;
    assert.equal(body.workflow, 'converge-campod-se-abc');
    assert.equal(body.status, 'running');
    // Marked, so a caller can tell this from a run that was actually watched -- in
    // particular that `lines` being empty means "not kept here", not "produced no output".
    assert.equal(body.adopted, true);
    assert.deepEqual(body.lines, []);
  } finally {
    await s.restore();
  }
});

test('a run this process is watching is served from memory, lines and all', async () => {
  // Ordering matters: ask the cluster first and a live run loses its lines to an adopted
  // stub that knows less than the thing it replaced.
  const s = serve(RUNNING);
  try {
    const run = s.runs.start('converge', 'campod-se', async (emit) => {
      emit('stdout', 'PLAY [all] ****');
      await new Promise((r) => setTimeout(r, 50));
    });
    const res = await s.app.inject({ method: 'GET', url: `/runs/${run.id}` });
    const body = res.json() as Record<string, unknown>;
    assert.equal(body.adopted, undefined);
    assert.ok((body.lines as unknown[]).length > 0, JSON.stringify(body.lines));
  } finally {
    await s.restore();
  }
});

test('a run in neither is still a 404', async () => {
  const s = serve('[]');
  try {
    const res = await s.app.inject({ method: 'GET', url: '/runs/never-existed' });
    assert.equal(res.statusCode, 404);
  } finally {
    await s.restore();
  }
});

test("Argo's three terminal phases land on two statuses", async () => {
  // `Error` is Argo's for "the controller could not run this at all". It is terminal and it
  // is a failure to the caller, and treating it as still-running would hang a poller.
  for (const [phase, status] of [
    ['Succeeded', 'succeeded'],
    ['Failed', 'failed'],
    ['Error', 'failed'],
    ['Pending', 'running'],
  ] as const) {
    const s = serve(`[{"metadata": {"name": "w"}, "status": {"phase": "${phase}"}}]`);
    try {
      const body = (await s.app.inject({ method: 'GET', url: '/runs/x' })).json() as Record<string, unknown>;
      assert.equal(body.status, status, `${phase} -> ${String(body.status)}`);
    } finally {
      await s.restore();
    }
  }
});
