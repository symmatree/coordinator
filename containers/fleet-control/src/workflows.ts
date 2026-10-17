// Running a playbook as an Argo Workflow instead of as a child of this process.
//
// coordinator#504. The template is declared in tiles
// (`tanka/environments/fleet-control`, `fleet-control-playbook`); this submits against it,
// follows it, and finds it again afterwards.
//
// WHY IT IS FOUND BY LABEL. The run that matters is the one in the cluster, not the entry in
// this process's memory. A pod roll used to kill a converge; now it only loses the watcher,
// and `findByRun` re-attaches. So nothing here may be the only record of a running workflow.
//
// Shelling out to kubectl rather than using a Kubernetes client, for the reason the
// Dockerfile gives about ansible and ssh: one credential path, and the automated steps are
// the documented manual ones. In-cluster it picks up the pod's ServiceAccount, which may
// create and read Workflows in this namespace and read pod logs, and nothing else.

import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);

/** Labels we set, and then select on. */
const RUN_LABEL = 'fleet-control.symmatree.com/run-id';
const NODE_LABEL = 'fleet-control.symmatree.com/node';
const ACTION_LABEL = 'fleet-control.symmatree.com/action';
/** Argo's own, for finding a workflow's pods. */
const WORKFLOW_LABEL = 'workflows.argoproj.io/workflow';

export interface WorkflowConfig {
  /** Where workflows are submitted. The same namespace this service runs in. */
  namespace: string;
  /** The WorkflowTemplate to submit against. */
  template: string;
  /** The Argo Workflows UI, for linking to a run rather than describing how to find it. */
  uiUrl: string;
}

/** Where a person goes to look at one. */
export function workflowUrl(cfg: WorkflowConfig, name: string): string {
  return `${cfg.uiUrl}/workflows/${cfg.namespace}/${name}`;
}

export interface PlaybookRequest {
  runId: string;
  node: string;
  /** converge | provision | reimage -- a label, for finding it later. */
  action: string;
  playbook: string;
  host: string;
  deviceRole: string;
  artifactUrl: string;
  revision: string;
  /** Anything the playbook needs beyond device_role. The step cannot use these to displace
   * what it sets itself: ansible merges repeated `-e` with the later winning, and the
   * step's own go last. */
  extraVars?: Record<string, string>;
}

/** What the cluster says about a submitted workflow. */
export interface WorkflowState {
  name: string;
  /** Argo's phase: Pending, Running, Succeeded, Failed, Error. Absent before it is set. */
  phase?: string;
  message?: string;
  finishedAt?: string;
}

function kubectl(cfg: WorkflowConfig, args: string[]): string[] {
  return ['-n', cfg.namespace, ...args];
}

/** kubectl with a manifest on stdin. `execFile` has no `input`; only the sync form does. */
function kubectlStdin(cfg: WorkflowConfig, args: string[], input: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn('kubectl', kubectl(cfg, args));
    let out = '';
    let err = '';
    child.stdout.on('data', (c: Buffer) => { out += c.toString(); });
    child.stderr.on('data', (c: Buffer) => { err += c.toString(); });
    child.on('error', reject);
    child.on('close', (code) =>
      code === 0
        ? resolve(out)
        : reject(new Error(`kubectl ${args[0]} exited ${code}: ${err.trim().slice(0, 400)}`)));
    child.stdin.end(input);
  });
}

/**
 * Submit one playbook run. Returns as soon as the cluster has accepted it.
 *
 * Deliberately does not wait: the point of the move is that the work outlives whoever asked
 * for it, so the submit and the watch are separate steps and only the first one matters for
 * the work actually happening.
 */
export async function submitPlaybook(
  cfg: WorkflowConfig,
  req: PlaybookRequest,
): Promise<string> {
  const manifest = {
    apiVersion: 'argoproj.io/v1alpha1',
    kind: 'Workflow',
    metadata: {
      generateName: `${req.action}-${req.node}-`,
      labels: {
        [RUN_LABEL]: req.runId,
        [NODE_LABEL]: req.node,
        [ACTION_LABEL]: req.action,
      },
    },
    spec: {
      workflowTemplateRef: { name: cfg.template },
      arguments: {
        parameters: [
          { name: 'playbook', value: req.playbook },
          { name: 'host', value: req.host },
          { name: 'device-role', value: req.deviceRole },
          { name: 'artifact-url', value: req.artifactUrl },
          { name: 'revision', value: req.revision },
          { name: 'extra-vars', value: JSON.stringify(req.extraVars ?? {}) },
        ],
      },
    },
  };

  const stdout = await kubectlStdin(cfg, ['create', '-o', 'json', '-f', '-'], JSON.stringify(manifest));
  const created = JSON.parse(stdout) as { metadata?: { name?: string } };
  const name = created.metadata?.name;
  if (name === undefined || name === '') {
    throw new Error('kubectl created a workflow but reported no name');
  }
  return name;
}

/** Read one workflow's state. Undefined if it is not there -- deleted, or never submitted. */
export async function readWorkflow(
  cfg: WorkflowConfig,
  name: string,
): Promise<WorkflowState | undefined> {
  try {
    const { stdout } = await run(
      'kubectl',
      kubectl(cfg, ['get', 'workflow', name, '-o', 'json']),
      { maxBuffer: 16 * 1024 * 1024 },
    );
    return stateOf(JSON.parse(stdout) as Record<string, unknown>);
  } catch {
    return undefined;
  }
}

/**
 * Find a run's workflow without having been told its name.
 *
 * This is what makes a pod roll survivable: the service can come back, ask the cluster what
 * is running on its behalf, and re-attach. Held in memory instead, the mapping would die
 * with the thing it exists to outlive.
 */
export async function findByRun(
  cfg: WorkflowConfig,
  runId: string,
): Promise<WorkflowState | undefined> {
  const found = await listWorkflows(cfg, `${RUN_LABEL}=${runId}`);
  return found[0];
}

/** Every workflow this service submitted, newest first. */
export async function listWorkflows(
  cfg: WorkflowConfig,
  selector = RUN_LABEL,
): Promise<WorkflowState[]> {
  const { stdout } = await run(
    'kubectl',
    kubectl(cfg, ['get', 'workflows', '-l', selector, '-o', 'json']),
    { maxBuffer: 64 * 1024 * 1024 },
  );
  const body = JSON.parse(stdout) as { items?: Array<Record<string, unknown>> };
  return (body.items ?? [])
    .map(stateOf)
    .sort((a, b) => (a.name < b.name ? 1 : -1));
}

function stateOf(obj: Record<string, unknown>): WorkflowState {
  const meta = (obj.metadata ?? {}) as Record<string, unknown>;
  const status = (obj.status ?? {}) as Record<string, unknown>;
  return {
    name: String(meta.name ?? ''),
    phase: typeof status.phase === 'string' ? status.phase : undefined,
    message: typeof status.message === 'string' ? status.message : undefined,
    finishedAt: typeof status.finishedAt === 'string' ? status.finishedAt : undefined,
  };
}

/** Terminal phases, as Argo spells them. */
export function isFinished(phase: string | undefined): boolean {
  return phase === 'Succeeded' || phase === 'Failed' || phase === 'Error';
}

/**
 * Follow a workflow to its end: its step's output into `sink`, then its phase.
 *
 * The log is the step's pod log, which Alloy also ships to Loki -- so this is the live view
 * and Loki is the durable one. Losing this stream loses nothing that is not written down
 * somewhere else, which is why it is allowed to fail quietly and the phase poll is not.
 */
export async function follow(
  cfg: WorkflowConfig,
  name: string,
  sink: (stream: 'stdout' | 'stderr', line: string) => void,
  opts: { pollMs?: number; retryMs?: number; signal?: AbortSignal } = {},
): Promise<WorkflowState> {
  const pollMs = opts.pollMs ?? 5_000;
  // The phase decides when this is over, and the log stream is stopped rather than waited
  // for. Waiting on it instead meant a workflow that ended quickly -- a failed submit, a
  // step that exits in a second -- held the caller for the whole log-retry budget while
  // kubectl went on looking for a pod that had already gone.
  const stop = new AbortController();
  if (opts.signal !== undefined) {
    opts.signal.addEventListener('abort', () => stop.abort(), { once: true });
  }
  const logs = streamLogs(cfg, name, sink, stop.signal, opts.retryMs);
  try {
    for (;;) {
      if (stop.signal.aborted) return { name, phase: 'Error', message: 'stopped watching' };
      const state = await readWorkflow(cfg, name);
      if (state === undefined) {
        // Gone while we were watching: deleted, or its TTL expired under us.
        return { name, phase: 'Error', message: 'the workflow is no longer in the cluster' };
      }
      if (isFinished(state.phase)) return state;
      await new Promise((r) => setTimeout(r, pollMs));
    }
  } finally {
    stop.abort();
    await logs.catch(() => {});
  }
}

/**
 * `kubectl logs -f` on the workflow's pods.
 *
 * Retries while the pod is being created, because a workflow accepted by the API server has
 * no pod for a second or two and `kubectl logs` on a label that matches nothing exits
 * immediately rather than waiting.
 */
function streamLogs(
  cfg: WorkflowConfig,
  name: string,
  sink: (stream: 'stdout' | 'stderr', line: string) => void,
  signal?: AbortSignal,
  retryMs = 5_000,
): Promise<void> {
  return new Promise<void>((resolve) => {
    let attempts = 0;
    const attach = (): void => {
      if (signal?.aborted === true) return resolve();
      attempts += 1;
      const child = spawn(
        'kubectl',
        // stdin closed: kubectl logs reads none, and an open one makes anything that does
        // wait forever on a pipe nobody writes to.
        kubectl(cfg, [
          'logs', '-f',
          '-l', `${WORKFLOW_LABEL}=${name}`,
          '-c', 'main',
          '--tail=-1',
          '--max-log-requests=5',
        ]),
        { stdio: ['ignore', 'pipe', 'pipe'] },
      );
      signal?.addEventListener('abort', () => child.kill('SIGTERM'), { once: true });
      const feed = (stream: 'stdout' | 'stderr') => {
        let buf = '';
        return (chunk: Buffer): void => {
          buf += chunk.toString();
          const lines = buf.split('\n');
          buf = lines.pop() ?? '';
          for (const l of lines) if (l.trim() !== '') sink(stream, l);
        };
      };
      child.stdout.on('data', feed('stdout'));
      // kubectl's own complaints, not the step's. Kept, because "no pod yet" shows up here.
      child.stderr.on('data', feed('stderr'));
      child.on('close', () => {
        // A workflow whose pod has not appeared yet: try again, a few times, then give up
        // and let the phase poll carry the run.
        if (attempts < 12 && signal?.aborted !== true) {
          const timer = setTimeout(attach, retryMs);
          signal?.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true });
          return;
        }
        resolve();
      });
      child.on('error', () => resolve());
    };
    attach();
  });
}
