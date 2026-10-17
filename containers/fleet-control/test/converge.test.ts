import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { converge, type ActionContext } from '../src/actions.js';

/**
 * What a converge submits: which playbook, against which node, with what.
 *
 * `converge` and `reimage` had no test at all before this, which is why #534 could leave
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
    workflows: { namespace: 'fleet-control', template: 'fleet-control-playbook' },
    github: { repo: 'symmatree/coordinator', workflow: 'ansible-payload.yaml', ref: 'main' },
    bus: { url: '', settleMs: 10 },
  } as ActionContext;

  return {
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
  // and the workflow's name comes back out in the log before anything is waited on -- so a
  // run whose watcher dies is still traceable to the thing that outlived it. Which playbook
  // name belongs to which action is not asserted; that is a constant, and a test of it only
  // means renaming one costs two edits.
  const b = bench();
  const lines: string[] = [];
  try {
    await converge(NODE, b.ctx, { runId: 'r1', kind: 'converge' }, (_s, l) => lines.push(l));
    const p = paramsOf(b.sent());
    assert.equal(p.revision, 'a'.repeat(40));
    assert.equal(p['artifact-url'], 'https://api.github.com/repos/symmatree/coordinator/actions/artifacts/7/zip');
  } finally {
    b.restore();
  }
  assert.ok(lines.some((l) => l.includes('workflow wf-1')), JSON.stringify(lines));
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
