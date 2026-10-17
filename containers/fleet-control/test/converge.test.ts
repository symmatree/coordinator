import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { converge, reimage, type ActionContext } from '../src/actions.js';

/**
 * That a converge SUBMITS A WORKFLOW rather than running ansible here.
 *
 * This file exists because #534 claimed to make that change and did not: the new modules
 * landed, the imports landed, and the function body kept calling ansible-runner. Nothing
 * caught it -- unused imports are not an error in this tsconfig, typecheck passed, and no
 * test touched `converge`. So the load-bearing assertion here is the negative one: there is
 * an `ansible-runner` on PATH that leaves a sentinel, and it must never run.
 */

const NODE = { name: 'campod-se', role: 'campod' as const, host: '10.0.5.237' };

function bench(opts: { phase?: string; message?: string } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'converge-'));
  const submitted = join(dir, 'submitted.json');
  const ranAnsible = join(dir, 'ansible-ran');

  writeFileSync(
    join(dir, 'kubectl'),
    `#!/usr/bin/env python3
import json, sys
argv = sys.argv[1:]
if "create" in argv:
    with open(${JSON.stringify(submitted)}, "w") as fh:
        fh.write(sys.stdin.read())
    print(json.dumps({"metadata": {"name": "wf-1"}}))
elif "logs" in argv:
    print("PLAY RECAP ****")
else:
    print(json.dumps({"metadata": {"name": "wf-1"},
                      "status": {"phase": ${JSON.stringify(opts.phase ?? 'Succeeded')},
                                 "message": ${JSON.stringify(opts.message ?? '')}}}))
`,
  );
  chmodSync(join(dir, 'kubectl'), 0o755);

  // The tripwire. If anything still runs the play in this process, this fires.
  writeFileSync(
    join(dir, 'ansible-runner'),
    `#!/bin/sh\ntouch ${JSON.stringify(ranAnsible)}\nexit 0\n`,
  );
  chmodSync(join(dir, 'ansible-runner'), 0o755);

  const realPath = process.env.PATH;
  const realFetch = globalThis.fetch;
  process.env.PATH = `${dir}:${realPath ?? ''}`;
  globalThis.fetch = (async (u: string) => {
    const body = /\/runs\/\d+\/artifacts/.test(String(u))
      ? { artifacts: [{ id: 7, name: `ansible-${'a'.repeat(40)}`, size_in_bytes: 1, expired: false, expires_at: 'x' }] }
      : { workflow_runs: [{ id: 1, head_sha: 'a'.repeat(40), head_branch: 'main', created_at: '2026-10-17T00:00:00Z' }] };
    return { ok: true, json: async () => body, text: async () => JSON.stringify(body) } as unknown as Response;
  }) as typeof fetch;

  const ctx = {
    inventory: { user: 'pi', nodes: [] },
    privateKeyPath: '/dev/null',
    sshTimeoutSec: 2,
    workflows: { namespace: 'fleet-control', template: 'fleet-control-playbook' },
    github: { repo: 'symmatree/coordinator', workflow: 'ansible-payload.yaml', ref: 'main' },
    bus: { url: '', settleMs: 10 },
  } as ActionContext;

  return {
    ctx,
    ranAnsible: () => existsSync(ranAnsible),
    sent: () => JSON.parse(readFileSync(submitted, 'utf8')) as Record<string, never>,
    restore: () => { process.env.PATH = realPath; globalThis.fetch = realFetch; },
  };
}

function paramsOf(sent: Record<string, never>): Record<string, string> {
  const spec = sent.spec as { arguments: { parameters: Array<{ name: string; value: string }> } };
  return Object.fromEntries(spec.arguments.parameters.map((p) => [p.name, p.value]));
}

test('a converge submits a workflow and never runs ansible in this process', async () => {
  const b = bench();
  const lines: string[] = [];
  try {
    await converge(NODE, b.ctx, { runId: 'r1', kind: 'converge' }, (_s, l) => lines.push(l));
    assert.equal(b.ranAnsible(), false, 'ansible-runner was spawned');
    const p = paramsOf(b.sent());
    assert.equal(p.playbook, 'deploy.yaml');
    assert.equal(p.host, '10.0.5.237');
    assert.equal(p['device-role'], 'campod');
    assert.equal(p.revision, 'a'.repeat(40));
    assert.equal(p['extra-vars'], '{}');
  } finally {
    b.restore();
  }
  // The workflow's name reaches the log before anything is waited on, so a run whose
  // watcher died is still traceable to the thing that outlived it.
  assert.ok(lines.some((l) => l.includes('workflow wf-1')), JSON.stringify(lines));
  assert.ok(lines.some((l) => l.includes('converged')), JSON.stringify(lines));
});

test('a full provision runs site.yaml, not deploy.yaml', async () => {
  const b = bench();
  try {
    await converge(NODE, b.ctx, { runId: 'r2', kind: 'provision' });
    assert.equal(b.ranAnsible(), false);
    assert.equal(paramsOf(b.sent()).playbook, 'site.yaml');
  } finally {
    b.restore();
  }
});

test('a reimage submits too, carrying its image as extra vars', async () => {
  const b = bench();
  try {
    await reimage(NODE, b.ctx, {
      runId: 'r3',
      image: { url: 'https://x/i.img', sha256: 'f'.repeat(64), sha: 'b'.repeat(40) },
    });
    assert.equal(b.ranAnsible(), false, 'ansible-runner was spawned');
    const p = paramsOf(b.sent());
    assert.equal(p.playbook, 'reimage.yaml');
    assert.deepEqual(JSON.parse(p['extra-vars'] ?? '{}'), {
      reimage_image_url: 'https://x/i.img',
      reimage_image_sha256: 'f'.repeat(64),
    });
    // device_role is a parameter of its own, not smuggled through extra-vars, because the
    // step sets it last and a caller must not be able to change it.
    assert.equal(p['device-role'], 'campod');
  } finally {
    b.restore();
  }
});

test('a failed workflow fails the run, naming the workflow to go and look at', async () => {
  const b = bench({ phase: 'Failed', message: 'step main exited 2' });
  try {
    await assert.rejects(
      () => converge(NODE, b.ctx, { runId: 'r4', kind: 'converge' }),
      (e: Error) =>
        e.message.includes('Failed') &&
        e.message.includes('step main exited 2') &&
        e.message.includes('wf-1'),
    );
  } finally {
    b.restore();
  }
});
