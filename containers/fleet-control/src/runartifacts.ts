// Serving what ansible-runner left behind for a run, so reading it is a GET.
//
// #353 stopped deleting the runner's data directory when a play did not succeed. That made
// the detail recoverable in principle and reachable only by `kubectl exec` into the pod,
// which is not a way to ask a question. These are the routes that answer it.
//
// WHAT IS SERVED IS `job_events/` AND NOTHING ELSE, and that is a boundary rather than a
// convenience. Beside it the runner writes `command`, which records the **entire process
// environment** it launched ansible with -- verified by running ansible-runner 2.4.3 and
// reading the file. In this pod that environment contains `FLEET_GITHUB_TOKEN`. So the
// directory is not a thing to hand out wholesale, and neither is `env/`, which holds the
// extra vars. The events are the part that says what happened.

import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { jobEventsDir, runDir } from './ansible.js';

/** One runner event, as far as anything here cares. */
interface JobEvent {
  /** The human-readable console text ansible would have printed for this event. */
  stdout?: string;
}

/**
 * The run's event files, oldest first.
 *
 * Ordered by the counter each filename starts with (`8-be917450-....json`), numerically --
 * lexical order would put event 10 before event 2. Returns an empty list when the run kept
 * nothing, which is the ordinary case: a play that exited 0 has its directory removed.
 */
export async function eventFiles(runId: string): Promise<string[]> {
  let idents: string[];
  try {
    // One ansible-runner invocation per run, so one ident -- but read it rather than assume
    // the name, because it is the runner's to choose.
    idents = await readdir(join(runDir(runId), 'artifacts'));
  } catch {
    return [];
  }
  const found: string[] = [];
  for (const ident of idents) {
    const dir = jobEventsDir(runId, ident);
    let names: string[];
    try {
      names = await readdir(dir);
    } catch {
      continue;
    }
    found.push(
      ...names
        .filter((n) => n.endsWith('.json'))
        .map((n) => ({ n, counter: Number.parseInt(n, 10) }))
        .sort((a, b) => a.counter - b.counter)
        .map(({ n }) => join(dir, n)),
    );
  }
  return found;
}

/**
 * Every event, one JSON object per line.
 *
 * Re-serialised rather than streamed byte-for-byte, because the runner pretty-prints each
 * file and JSON Lines is what makes the whole thing greppable and `jq`-able in one pass.
 * A file that will not parse is skipped with a marker rather than aborting the response --
 * a response already half-sent cannot become an error code, and one bad event should not
 * cost the other five hundred.
 */
export async function* eventLines(files: string[]): AsyncGenerator<string> {
  for (const file of files) {
    try {
      yield `${JSON.stringify(JSON.parse(await readFile(file, 'utf8')))}\n`;
    } catch (err) {
      yield `${JSON.stringify({ event: 'fleet_control_unreadable', file, error: (err as Error).message })}\n`;
    }
  }
}

/**
 * The play as ansible printed it.
 *
 * Each event carries the console text for its own line in `stdout`, already carrying the
 * leading newlines and the colour codes -- so concatenating them in counter order reproduces
 * what a terminal running the playbook would have shown. Colour is kept: it is what ansible
 * emitted, and stripping it would be this service editing the record.
 */
export async function* logChunks(files: string[]): AsyncGenerator<string> {
  for (const file of files) {
    try {
      const ev = JSON.parse(await readFile(file, 'utf8')) as JobEvent;
      // Newline-terminated: each event's text occupies the line range it declares
      // (`start_line`/`end_line`), and without the terminator a task header and its result
      // run together on one line.
      if (ev.stdout !== undefined && ev.stdout.length > 0) yield `${ev.stdout}\n`;
    } catch {
      // Same reasoning as eventLines: a response in flight cannot become a 500. The events
      // route is where an unreadable file is visible.
    }
  }
}

/** Only ever a run id, so a URL segment cannot become a path. */
export function isRunId(id: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id);
}
