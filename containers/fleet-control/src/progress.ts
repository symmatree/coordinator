// Collapse docker's progress redraws.
//
// With no TTY, `docker compose pull` cannot rewrite lines in place, so it re-emits the whole
// progress display as fresh lines, several times a second, on STDERR. Measured on campod-se's
// bootstrap: `coord pull` produced roughly 2900 of the run's 3115 lines, enough to bury
// everything that mattered and to blow the per-run cap on a longer pull.
//
// The naive fix -- drop a line if it repeats the one before it -- does nothing here, because
// docker interleaves layers:
//
//     a20d191cf9b9 Downloading 28.31MB
//     75782e20ea1f Extracting 1B
//     a20d191cf9b9 Downloading 28.31MB     <- identical to two lines earlier, not to the last
//     75782e20ea1f Extracting 1B
//
// So the state has to be PER LAYER, not positional. Two rules, both per (layer, stage):
//
//  1. An update identical to that layer's last emitted line is a redraw -- drop it.
//  2. A changed update is real progress, but `Downloading 28.31MB` -> `28.4MB` several times a
//     second is not worth a line each, so it is rate-limited.
//
// Terminal states (`Pull complete`, `Pulled`, `Already exists`, ...) always pass: they are the
// lines someone reading the log actually wants, and there is exactly one per layer. Anything
// that is not a progress line passes through untouched, so real output is never swallowed.

/** ` a20d191cf9b9 Extracting 108B` -- optional indent, id, verb, optional detail. */
const PROGRESS =
  /^\s*(\S+) (Pulling|Extracting|Downloading|Waiting|Verifying Checksum|Download complete|Pull complete|Already exists|Pulled|Pulling fs layer)\b(.*)$/;

/** Stages that happen once per layer and end it. Never suppressed. */
const TERMINAL = new Set(['Download complete', 'Pull complete', 'Already exists', 'Pulled']);

export interface CollapseOptions {
  /** Minimum gap between emitted updates for one layer+stage, ms. */
  minIntervalMs?: number;
  /** Injectable clock, so the rate limit is testable without waiting. */
  now?: () => number;
}

/**
 * Stateful filter. Returns the line to emit, or null when it is a redraw or an update arriving
 * faster than the rate limit.
 */
export function makeProgressCollapser(opts: CollapseOptions = {}): (line: string) => string | null {
  const minInterval = opts.minIntervalMs ?? 2_000;
  const now = opts.now ?? (() => Date.now());
  const last = new Map<string, { line: string; at: number }>();

  return (line: string) => {
    const m = PROGRESS.exec(line);
    if (!m) return line;

    const key = `${m[1]} ${m[2]}`;
    const seen = last.get(key);
    const t = now();

    if (TERMINAL.has(m[2]!)) {
      // Emit once; docker repeats these in every redraw of the display too.
      if (seen?.line === line) return null;
      last.set(key, { line, at: t });
      return line;
    }

    if (seen) {
      if (seen.line === line) return null; // pure redraw
      if (t - seen.at < minInterval) return null; // real progress, but too fast to be useful
    }
    last.set(key, { line, at: t });
    return line;
  };
}
