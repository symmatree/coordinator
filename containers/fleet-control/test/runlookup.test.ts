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
      workflows: {
        namespace: 'fleet-control',
        template: 'fleet-control-playbook',
        uiUrl: 'https://argo.example.invalid',
      },
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

test('a run this process never saw is a 404 that says where the work is', async () => {
  // A pod roll loses the registry while the Workflow carries on, and the link was in the
  // lines that died with it -- so the run id is all a caller has left to ask with.
  const s = serve(RUNNING);
  try {
    const res = await s.app.inject({ method: 'GET', url: '/runs/lost-run' });
    assert.equal(res.statusCode, 404);
    const body = res.json() as Record<string, unknown>;
    assert.equal(
      body.workflow,
      'https://argo.example.invalid/workflows/fleet-control/converge-campod-se-abc',
    );
    // No invented run: no status, no phase, no empty `lines` to misread.
    assert.equal(body.status, undefined);
    assert.equal(body.lines, undefined);
  } finally {
    await s.restore();
  }
});

test('a run this process is watching is served from memory, lines and all', async () => {
  // Ordering matters: ask the cluster first and a live run is answered with a 404.
  const s = serve(RUNNING);
  try {
    const run = s.runs.start('converge', 'campod-se', async (emit) => {
      emit('stdout', 'PLAY [all] ****');
      await new Promise((r) => setTimeout(r, 50));
    });
    const res = await s.app.inject({ method: 'GET', url: `/runs/${run.id}` });
    assert.equal(res.statusCode, 200);
    assert.ok(((res.json() as Record<string, unknown>).lines as unknown[]).length > 0);
  } finally {
    await s.restore();
  }
});

test('a run in neither is a plain 404, with no workflow to point at', async () => {
  const s = serve('[]');
  try {
    const res = await s.app.inject({ method: 'GET', url: '/runs/never-existed' });
    assert.equal(res.statusCode, 404);
    assert.equal((res.json() as Record<string, unknown>).workflow, undefined);
  } finally {
    await s.restore();
  }
});
