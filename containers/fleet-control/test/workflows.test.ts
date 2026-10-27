import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, chmodSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { follow, isFinished, listWorkflows, submitPlaybook } from '../src/workflows.js';

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

test('the node and action go on as labels, which is how the lock and the list find it', async () => {
  // A workflow outlives this process, so what it is and what it drives cannot live in this
  // process. If it is not a label it is lost.
  const k = fakeKubectl(CREATED);
  await withPath(k.dir, () => submitPlaybook(CFG, REQ));
  const labels = (JSON.parse(k.stdin()).metadata as { labels: Record<string, string>; generateName: string });
  assert.equal(labels.labels['fleet-control.symmatree.com/node'], 'campod-se');
  assert.equal(labels.labels['fleet-control.symmatree.com/action'], 'converge');
  assert.equal(labels.labels['fleet-control.symmatree.com/touches-campod-se'], 'true');
  // generateName, not name: two converges of one node must not collide.
  assert.equal(labels.generateName, 'converge-campod-se-');
});

test('extra vars are serialised into the parameter, not passed as an object', async () => {
  const k = fakeKubectl(CREATED);
  await withPath(k.dir, () =>
    submitPlaybook(CFG, { ...REQ, extraVars: { some_var: 'https://x/i.img' } }),
  );
  const spec = JSON.parse(k.stdin()).spec as { arguments: { parameters: Array<{ name: string; value: string }> } };
  const extra = spec.arguments.parameters.find((p) => p.name === 'extra-vars');
  assert.equal(extra?.value, '{"some_var":"https://x/i.img"}');
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

test('the list is every workflow in the namespace, newest first, labels read back', async () => {
  // All of them, not only ours: the bench cycle submits its own and holds the campods
  // while it runs, so a list that filtered on our label would hide what is in the way.
  const k = fakeKubectl(`
print(json.dumps({"items": [
  {"metadata": {"name": "bench-simflight-1", "creationTimestamp": "2026-10-19T12:29:38Z"},
   "status": {"phase": "Succeeded", "startedAt": "2026-10-19T12:29:38Z", "finishedAt": "2026-10-19T12:58:58Z"}},
  {"metadata": {"name": "converge-campod-se-c8v98", "creationTimestamp": "2026-10-19T12:29:51Z",
                "labels": {"fleet-control.symmatree.com/node": "campod-se",
                           "fleet-control.symmatree.com/action": "converge"}},
   "status": {"phase": "Running"}}
]}))
`);
  const all = await withPath(k.dir, () => listWorkflows(CFG));
  const [argv] = k.calls();
  assert.ok(!argv.includes('-l'), `no selector: ${argv.join(' ')}`);
  assert.deepEqual(all.map((w) => w.name), ['converge-campod-se-c8v98', 'bench-simflight-1']);
  assert.equal(all[0]?.node, 'campod-se');
  assert.equal(all[0]?.action, 'converge');
  assert.equal(all[0]?.phase, 'Running');
  assert.equal(all[1]?.node, undefined, 'a workflow we did not submit carries no node');
  assert.equal(all[1]?.finishedAt, '2026-10-19T12:58:58Z');
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

test('a finished stream is not re-read, so the play appears once', async () => {
  // campod-se's first successful record held the whole converge THREE times, on a clean
  // 145-line stride. `kubectl logs -f` exits normally when the pod finishes and `--tail=-1`
  // re-reads from the start, so retrying after a stream that worked appends another copy
  // every retryMs until the phase poll aborts it.
  const k = fakeKubectl(`
import os
p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polls")
n = int(open(p).read()) if os.path.exists(p) else 0
if "logs" in argv:
    print("PLAY [all] ****")
    print("PLAY RECAP ****")
else:
    open(p, "w").write(str(n + 1))
    print(json.dumps({"metadata": {"name": "w1"},
                      "status": {} if n < 3 else {"phase": "Succeeded"}}))
`);
  const lines: string[] = [];
  const state = await withPath(k.dir, () =>
    follow(CFG, 'w1', (_s, l) => lines.push(l), { pollMs: 10, retryMs: 5 }),
  );
  assert.equal(state.phase, 'Succeeded');
  assert.equal(lines.filter((l) => l.includes('PLAY RECAP')).length, 1, JSON.stringify(lines));
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
