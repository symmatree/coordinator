// A registry of in-flight and finished action runs.
//
// Actions take minutes (`coord pull` moves hundreds of MB; a bootstrap installs Ansible on a
// 512 MB Zero). So the API starts a run and returns its id immediately rather than holding a
// request open for the duration, and the caller polls or streams. That is also what makes the
// actions reachable by something that is not a browser: `curl` can start a run and follow it.
//
// In-memory and bounded. History across restarts is not a goal -- the run log is an
// operational view of what is happening now, not a record of the fleet. What IS durable is
// the node state itself, which `probe` reads back from the node at any time.

import { randomUUID } from 'node:crypto';

export type RunStatus = 'running' | 'succeeded' | 'failed';

export interface RunLine {
  t: string;
  stream: 'stdout' | 'stderr';
  line: string;
}

export interface Run {
  id: string;
  action: string;
  node: string;
  status: RunStatus;
  startedAt: string;
  endedAt?: string;
  error?: string;
  lines: RunLine[];
}

/** Keep the most recent N runs; a long-lived pod should not grow without bound. */
const MAX_RUNS = 50;
/** Per-run line cap. A pathological log must not exhaust the pod's memory. */
const MAX_LINES = 5_000;

type Listener = (line: RunLine) => void;

export class RunRegistry {
  private runs = new Map<string, Run>();
  private listeners = new Map<string, Set<Listener>>();
  /** Fired once per watcher when a run finishes, after its last line. */
  private enders = new Map<string, Set<() => void>>();

  /** Is an action already running against this node? */
  activeFor(node: string): Run | undefined {
    for (const r of this.runs.values()) {
      if (r.node === node && r.status === 'running') return r;
    }
    return undefined;
  }

  list(): Run[] {
    return [...this.runs.values()].sort((a, b) => b.startedAt.localeCompare(a.startedAt));
  }

  get(id: string): Run | undefined {
    return this.runs.get(id);
  }

  /**
   * Watch a run. `onEnd` fires once when it finishes, after the last line.
   *
   * Without it a watcher has no way to learn the run is over except by recognising the text
   * of the final line, which is not an interface -- and a stream that never says it is done
   * is one its reader holds open until its own timeout expires.
   */
  subscribe(id: string, fn: Listener, onEnd?: () => void): () => void {
    const set = this.listeners.get(id) ?? new Set();
    set.add(fn);
    this.listeners.set(id, set);
    if (onEnd) {
      const ends = this.enders.get(id) ?? new Set();
      ends.add(onEnd);
      this.enders.set(id, ends);
    }
    return () => {
      set.delete(fn);
      if (onEnd) this.enders.get(id)?.delete(onEnd);
    };
  }

  /**
   * Start an action. Returns the run immediately; the work continues in the background.
   *
   * Refuses to start a second action against a node that already has one running -- two
   * concurrent `coord pull`s on the same device fight over the docker daemon and the checkout,
   * and the failure is confusing rather than loud.
   */
  start(action: string, node: string, work: (emit: Listener) => Promise<void>): Run {
    const busy = this.activeFor(node);
    if (busy) {
      throw new Error(
        `${node} already has '${busy.action}' running (run ${busy.id}). ` +
          `Wait for it or look at why it is stuck; do not run two at once against one device.`,
      );
    }

    const run: Run = {
      id: randomUUID(),
      action,
      node,
      status: 'running',
      startedAt: new Date().toISOString(),
      lines: [],
    };
    this.runs.set(run.id, run);
    this.prune();

    const emit: Listener = (l) => {
      if (run.lines.length < MAX_LINES) run.lines.push(l);
      else if (run.lines.length === MAX_LINES) {
        run.lines.push({ t: l.t, stream: 'stderr', line: `[fleet-control] output truncated at ${MAX_LINES} lines` });
      }
      for (const fn of this.listeners.get(run.id) ?? []) fn(l);
    };

    void work(emit)
      .then(() => {
        run.status = 'succeeded';
      })
      .catch((err: unknown) => {
        run.status = 'failed';
        run.error = err instanceof Error ? err.message : String(err);
        emit({ t: new Date().toISOString(), stream: 'stderr', line: run.error });
      })
      .finally(() => {
        run.endedAt = new Date().toISOString();
        for (const fn of this.listeners.get(run.id) ?? []) {
          fn({ t: run.endedAt, stream: 'stdout', line: `[fleet-control] run ${run.status}` });
        }
        this.listeners.delete(run.id);
        for (const fn of this.enders.get(run.id) ?? []) fn();
        this.enders.delete(run.id);
      });

    return run;
  }

  private prune(): void {
    if (this.runs.size <= MAX_RUNS) return;
    const finished = this.list()
      .filter((r) => r.status !== 'running')
      .slice(MAX_RUNS);
    for (const r of finished) this.runs.delete(r.id);
  }
}

/** Adapt a LineSink-shaped callback onto a run emitter. */
export function sinkFor(emit: Listener) {
  return (stream: 'stdout' | 'stderr', line: string) =>
    emit({ t: new Date().toISOString(), stream, line });
}
