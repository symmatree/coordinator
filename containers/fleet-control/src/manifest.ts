// Parse the fleet manifest: what a versioned thing says it is.
//
// One flat table of double-quoted strings, which is simultaneously valid TOML, a sourceable
// shell file, and a systemd EnvironmentFile (coordinator#326). The device emits it; we read
// it. Two rules make all three true at once, and the first is easy to violate by accident:
//
//   KEY="value"     no whitespace around `=` -- TOML allows `K = "v"`, `source` does not
//   KEY="value"     values always quoted, and containing no `$`, which `source` would expand
//
// Deliberately hand-written rather than a TOML dependency: the grammar we accept is one line
// shape, and the parser has to tolerate a device that emits something slightly wrong without
// taking the whole status screen down with it.

/** The keys the UI understands well enough to act on. Everything else is passed through. */
export const CONTROLLED = {
  source: 'ORG_OPENCONTAINERS_IMAGE_SOURCE',
  revision: 'ORG_OPENCONTAINERS_IMAGE_REVISION',
  refName: 'ORG_OPENCONTAINERS_IMAGE_REF_NAME',
} as const;

export interface Manifest {
  /** Browsable repo URL, for linking. */
  source?: string;
  /** Full git sha this was built from. */
  revision?: string;
  /** Branch or tag it was built off. */
  refName?: string;
  /**
   * Everything else, verbatim and in file order. Artifact-specific by design: an image adds
   * a field without anyone agreeing to it, and we show it in case it means something.
   */
  extra: Record<string, string>;
}

const LINE = /^([A-Za-z_][A-Za-z0-9_]*)="(.*)"$/;

/**
 * Parse a manifest. Unparseable lines are skipped rather than thrown on -- a malformed line
 * in one device's manifest should cost that line, not the whole screen.
 */
export function parseManifest(text: string): Manifest {
  const all: Record<string, string> = {};
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (line.length === 0 || line.startsWith('#')) continue;
    const m = LINE.exec(line);
    if (m === null) continue;
    const [, key, value] = m;
    if (key !== undefined && value !== undefined) all[key] = value;
  }
  const extra = { ...all };
  const take = (key: string): string | undefined => {
    const v = extra[key];
    delete extra[key];
    return v;
  };
  return {
    source: take(CONTROLLED.source),
    revision: take(CONTROLLED.revision),
    refName: take(CONTROLLED.refName),
    extra,
  };
}

/** Short form for display. Full sha is what gets compared; this is what gets shown. */
export function shortSha(sha: string | undefined): string {
  return sha === undefined ? '' : sha.slice(0, 10);
}
