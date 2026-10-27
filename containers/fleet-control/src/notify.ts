// Tell the operator what happened, so the answer does not depend on a browser tab being open.
//
// A campod converge is twenty minutes or more, and #223 asks for "enough status to tell me what
// the run is doing and when it is done, so I know when I can pull the plug". The screen can now
// say that when you come back to it; a notification means you do not have to come back.
//
// Two events, for two different reasons:
//
//   run finished     the thing you started is over, and how it ended. Said by the WORKFLOW'S
//                    exit handler (notify-step.ts), not by this process: a converge or a
//                    collect outlives this pod, so the announcement has to as well.
//   service started  the pod has been replaced. Workflows carry on; what a roll ends is a
//                    stop, reboot or ground that was mid-request, and the log stream anyone
//                    had open. Worth knowing before starting something.
//
// Apprise is already deployed in `tiles`. A notify with no `tag` matches zero targets, which is
// recorded beside Alloy's own notifier -- so the tag is required, not optional.

import type { Build } from './build.js';

export interface NotifyConfig {
  /** Apprise's notify endpoint. Empty disables notification entirely. */
  url: string;
  /** Apprise routes by tag; an untagged notify reaches nobody. */
  tag: string;
}

/**
 * Post one notification. NEVER THROWS.
 *
 * A failed notification must not fail the thing it was reporting on: a converge that worked and
 * could not be announced is still a converge that worked. Failures go to the pod's stdout, which
 * log collection keeps.
 */
export async function notify(cfg: NotifyConfig, title: string, body: string): Promise<void> {
  if (cfg.url === '') return;
  try {
    const res = await fetch(cfg.url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ title, body, tag: cfg.tag, type: 'info' }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) {
      process.stderr.write(`[fleet-control] notify ${res.status}: ${(await res.text()).slice(0, 200)}\n`);
    }
  } catch (err) {
    process.stderr.write(`[fleet-control] notify failed: ${(err as Error).message}\n`);
  }
}

/** What a run's ending is worth saying. */
export function runEnded(action: string, node: string, status: string, lastLine?: string): [string, string] {
  return [
    `${node}: ${action} ${status}`,
    lastLine === undefined ? `${action} on ${node} ${status}.` : `${action} on ${node} ${status}.\n\n${lastLine}`,
  ];
}

/**
 * What a restart is worth saying, which is the build -- because the reason to care is "did the
 * roller replace me, and with what".
 */
export function serviceStarted(b: Build): [string, string] {
  const rev = b.revision === undefined || b.revision === '' ? 'no build manifest' : b.revision.slice(0, 10);
  return [
    'fleet-control started',
    [
      `${b.unit ?? 'fleet-control'} is up.`,
      `build ${rev}${b.refName === undefined || b.refName === '' ? '' : ` (${b.refName})`}`,
      'A stop, reboot or ground in flight was ended by this restart. Workflows carry on.',
    ].join('\n'),
  ];
}
