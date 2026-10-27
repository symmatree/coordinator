import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { converge, type ActionContext } from '../src/actions.js';

/**
 * What a converge submits: which playbook, against which node, with what.
 *
 * `converge` had no test at all before this, which is why #534 could leave
 * them calling ansible-runner while claiming otherwise.
 */

const NODE = { name: 'campod-se', role: 'campod' as const, host: '10.0.5.237' };

function bench(opts: { phase?: string; message?: string } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'converge-'));
  const submitted = join(dir, 'submitted.json');

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
    workflows: {
      namespace: 'fleet-control',
      template: 'fleet-control-playbook',
      uiUrl: 'https://argo.example.invalid',
    },
    github: { repo: 'symmatree/coordinator', workflow: 'ansible-payload.yaml', ref: 'main' },
    bus: { url: '', settleMs: 10 },
  } as ActionContext;

  return {
    dir,
    ctx,
    sent: () => JSON.parse(readFileSync(submitted, 'utf8')) as Record<string, never>,
    restore: () => { process.env.PATH = realPath; globalThis.fetch = realFetch; },
  };
}

function paramsOf(sent: Record<string, never>): Record<string, string> {
  const spec = sent.spec as { arguments: { parameters: Array<{ name: string; value: string }> } };
  return Object.fromEntries(spec.arguments.parameters.map((p) => [p.name, p.value]));
}

test('the resolved artifact is what the workflow is told to run', async () => {
  // The seam between three modules: latestPlaybook picks a build, submitPlaybook sends it,
  // and the workflow's name and link come back -- the only handle there is, since nothing
  // here waits for it (coordinator#566). Which playbook name belongs to which action is
  // not asserted; that is a constant, and a test of it only means renaming one costs two
  // edits.
  const b = bench();
  try {
    const sub = await converge(NODE, b.ctx, 'converge');
    const p = paramsOf(b.sent());
    assert.equal(p.revision, 'a'.repeat(40));
    assert.equal(p['artifact-url'], 'https://api.github.com/repos/symmatree/coordinator/actions/artifacts/7/zip');
    assert.equal(sub.workflow, 'wf-1');
    assert.equal(sub.revision, 'a'.repeat(40));
    // A link, not just a name: the UI is where someone goes to see what a workflow did.
    assert.equal(sub.url, 'https://argo.example.invalid/workflows/fleet-control/wf-1');
  } finally {
    b.restore();
  }
});

test('a submit that the cluster refuses is an error, not a workflow name', async () => {
  const b = bench();
  // Replace the fake kubectl's create with a refusal.
  writeFileSync(join(b.dir, 'kubectl'), '#!/bin/sh\necho "forbidden" >&2\nexit 1\n');
  try {
    await assert.rejects(() => converge(NODE, b.ctx, 'converge'), /kubectl create exited 1/);
  } finally {
    b.restore();
  }
});
