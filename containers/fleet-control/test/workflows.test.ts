import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, chmodSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { findByRun, follow, isFinished, submitPlaybook } from '../src/workflows.js';

const CFG = { namespace: 'fleet-control', template: 'fleet-control-playbook' };

/**
 * A `kubectl` on PATH that records its argv and stdin and answers from a script.
 *
 * Driving the real binary against a fake cluster would test kubectl; this tests what we
 * send it and what we make of what comes back, which is where our bugs are.
 */
function fakeKubectl(behaviour: string): { dir: string; calls: () => string[][]; stdin: () => string } {
  const dir = mkdtempSync(join(tmpdir(), 'kubectl-'));
  const log = join(dir, 'calls.jsonl');
  const stdinFile = join(dir, 'stdin');
  writeFileSync(
    join(dir, 'kubectl'),
    `#!/usr/bin/env python3
import json, sys, os
argv = sys.argv[1:]
with open(${JSON.stringify(log)}, "a") as fh:
    fh.write(json.dumps(argv) + "\\n")
stdin = sys.stdin.read() if "-f" in argv and "-" in argv else ""
if stdin:
    with open(${JSON.stringify(stdinFile)}, "w") as fh:
        fh.write(stdin)
${behaviour}
`,
  );
  chmodSync(join(dir, 'kubectl'), 0o755);
  return {
    dir,
    calls: () =>
      (existsSync(log) ? readFileSync(log, 'utf8') : '')
        .split('\n')
        .filter((l) => l.trim() !== '')
        .map((l) => JSON.parse(l) as string[]),
    stdin: () => (existsSync(stdinFile) ? readFileSync(stdinFile, 'utf8') : ''),
  };
}

function withPath<T>(dir: string, fn: () => Promise<T>): Promise<T> {
  const real = process.env.PATH;
  process.env.PATH = `${dir}:${real ?? ''}`;
  return fn().finally(() => { process.env.PATH = real; });
}

const REQ = {
  runId: 'run-abc',
  node: 'campod-se',
  action: 'converge',
  playbook: 'deploy.yaml',
  host: '10.0.5.237',
  deviceRole: 'campod',
  artifactUrl: 'https://api.github.com/repos/o/r/actions/artifacts/7/zip',
  revision: 'c'.repeat(40),
};

const CREATED = `
if "create" in argv:
    print(json.dumps({"metadata": {"name": "converge-campod-se-xyz12"}}))
`;

test('submitting names the template and passes every parameter', async () => {
  const k = fakeKubectl(CREATED);
  const name = await withPath(k.dir, () => submitPlaybook(CFG, REQ));
  assert.equal(name, 'converge-campod-se-xyz12');

  const [argv] = k.calls();
  assert.deepEqual(argv, ['-n', 'fleet-control', 'create', '-o', 'json', '-f', '-']);

  const sent = JSON.parse(k.stdin()) as Record<string, never>;
  const spec = sent.spec as { workflowTemplateRef: { name: string }; arguments: { parameters: Array<{ name: string; value: string }> } };
  assert.equal(spec.workflowTemplateRef.name, 'fleet-control-playbook');
  const params = Object.fromEntries(spec.arguments.parameters.map((p) => [p.name, p.value]));
  assert.deepEqual(params, {
    playbook: 'deploy.yaml',
    host: '10.0.5.237',
    'device-role': 'campod',
    'artifact-url': REQ.artifactUrl,
    revision: REQ.revision,
    // Always sent, empty when the caller has none, so the template never falls back to a
    // default that could drift from what this thinks it is sending.
    'extra-vars': '{}',
  });
});

test('the run id goes on as a label, which is how it is found again', async () => {
  // THE POINT OF THE WHOLE CHANGE. A workflow outlives this process, so the mapping from a
  // run to its workflow cannot live in this process. If it is not a label it is lost.
  const k = fakeKubectl(CREATED);
  await withPath(k.dir, () => submitPlaybook(CFG, REQ));
  const labels = (JSON.parse(k.stdin()).metadata as { labels: Record<string, string>; generateName: string });
  assert.equal(labels.labels['fleet-control.symmatree.com/run-id'], 'run-abc');
  assert.equal(labels.labels['fleet-control.symmatree.com/node'], 'campod-se');
  assert.equal(labels.labels['fleet-control.symmatree.com/action'], 'converge');
  // generateName, not name: two converges of one node must not collide.
  assert.equal(labels.generateName, 'converge-campod-se-');
});

test('extra vars are serialised into the parameter, not passed as an object', async () => {
  const k = fakeKubectl(CREATED);
  await withPath(k.dir, () =>
    submitPlaybook(CFG, { ...REQ, extraVars: { reimage_image_url: 'https://x/i.img' } }),
  );
  const spec = JSON.parse(k.stdin()).spec as { arguments: { parameters: Array<{ name: string; value: string }> } };
  const extra = spec.arguments.parameters.find((p) => p.name === 'extra-vars');
  assert.equal(extra?.value, '{"reimage_image_url":"https://x/i.img"}');
});

test('a kubectl that fails to create says why, rather than yielding no name', async () => {
  const k = fakeKubectl(`
sys.stderr.write("workflowtemplates.argoproj.io \\"fleet-control-playbook\\" not found\\n")
sys.exit(1)
`);
  await withPath(k.dir, () =>
    assert.rejects(() => submitPlaybook(CFG, REQ), /not found/),
  );
});

test('a create that prints no name is an error, not an empty workflow name', async () => {
  const k = fakeKubectl(`
if "create" in argv:
    print(json.dumps({"metadata": {}}))
`);
  await withPath(k.dir, () =>
    assert.rejects(() => submitPlaybook(CFG, REQ), /reported no name/),
  );
});

test('a run is found by its label without being told the workflow name', async () => {
  const k = fakeKubectl(`
print(json.dumps({"items": [{"metadata": {"name": "converge-campod-se-xyz12"},
                             "status": {"phase": "Running"}}]}))
`);
  const found = await withPath(k.dir, () => findByRun(CFG, 'run-abc'));
  assert.equal(found?.name, 'converge-campod-se-xyz12');
  assert.equal(found?.phase, 'Running');
  const [argv] = k.calls();
  assert.ok(argv.includes('-l'), argv.join(' '));
  assert.equal(argv[argv.indexOf('-l') + 1], 'fleet-control.symmatree.com/run-id=run-abc');
});

test('a run with no workflow is undefined, not an invented one', async () => {
  const k = fakeKubectl('print(json.dumps({"items": []}))');
  assert.equal(await withPath(k.dir, () => findByRun(CFG, 'run-nope')), undefined);
});

test('following returns the terminal phase and its message', async () => {
  const k = fakeKubectl(`
if "logs" in argv:
    print("PLAY [all] ****")
    print("ok: [10.0.5.237]")
else:
    print(json.dumps({"metadata": {"name": "w1"},
                      "status": {"phase": "Failed", "message": "step main failed"}}))
`);
  const lines: string[] = [];
  const state = await withPath(k.dir, () =>
    follow(CFG, 'w1', (_s, l) => lines.push(l), { pollMs: 10, retryMs: 5 }),
  );
  assert.equal(state.phase, 'Failed');
  assert.equal(state.message, 'step main failed');
  // The step's own output reaches the run log, unparsed.
  assert.ok(lines.includes('PLAY [all] ****'), JSON.stringify(lines));
});

test('a workflow the controller never acts on fails, rather than running forever', async () => {
  // The 2026-10-17 outage, reproduced: the controller could not list WorkflowTaskResult, so
  // it reconciled nothing and the object sat with NO phase. The watch polled indefinitely,
  // the caller's run stayed `running`, and no marker a client could match ever appeared --
  // the failure was invisible to everyone except whoever read the controller's log.
  const k = fakeKubectl(`
if "logs" in argv:
    sys.stderr.write("No resources found in fleet-control namespace.\\n")
    sys.exit(1)
print(json.dumps({"metadata": {"name": "stuck"}, "status": {}}))
`);
  const lines: string[] = [];
  const state = await withPath(k.dir, () =>
    follow(CFG, 'stuck', (_s, l) => lines.push(l), { pollMs: 10, retryMs: 5, startupMs: 60 }),
  );
  assert.equal(state.phase, 'Error');
  assert.match(state.message ?? '', /accepted but never started/);
  assert.match(state.message ?? '', /phase unset/);

  // And kubectl's complaint about the pod that never appeared is said once, as ours, not
  // once per retry as the step's output.
  const waiting = lines.filter((l) => l.includes('waiting for the step'));
  assert.equal(waiting.length, 1, JSON.stringify(lines));
  assert.equal(lines.filter((l) => l.includes('No resources found')).length, 1, JSON.stringify(lines));
});

test('a slow start is not a failed one: Pending then Running survives the bound', async () => {
  // The bound is on STARTING, not on running. A converge is twenty minutes of apt and must
  // not be killed for taking them.
  const k = fakeKubectl(`
import os
if "logs" in argv:
    sys.exit(1)
n = 0
p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polls")
if os.path.exists(p):
    n = int(open(p).read())
open(p, "w").write(str(n + 1))
phase = "Pending" if n < 2 else ("Running" if n < 6 else "Succeeded")
print(json.dumps({"metadata": {"name": "slow"}, "status": {"phase": phase}}))
`);
  const state = await withPath(k.dir, () =>
    // A startup bound shorter than the time spent Running: if it applied after the workflow
    // had started, this would come back Error instead of Succeeded.
    follow(CFG, 'slow', () => {}, { pollMs: 10, retryMs: 5, startupMs: 60 }),
  );
  assert.equal(state.phase, 'Succeeded');
});

test('a workflow that vanished mid-watch ends the run instead of polling forever', async () => {
  // Deleted by hand, or its TTL expired under us. Either way the watcher must stop.
  const k = fakeKubectl(`
if "logs" in argv:
    sys.exit(1)
sys.stderr.write('Error from server (NotFound)\\n')
sys.exit(1)
`);
  const state = await withPath(k.dir, () => follow(CFG, 'gone', () => {}, { pollMs: 10, retryMs: 5 }));
  assert.equal(state.phase, 'Error');
  assert.match(state.message ?? '', /no longer in the cluster/);
});

test('Argo spells its terminal phases three ways and all three end a run', () => {
  for (const p of ['Succeeded', 'Failed', 'Error']) assert.equal(isFinished(p), true, p);
  for (const p of ['Pending', 'Running', undefined]) assert.equal(isFinished(p), false, String(p));
});
