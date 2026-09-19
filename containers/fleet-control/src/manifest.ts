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
  /**
   * Fully-qualified git ref, e.g. `refs/heads/main` or `refs/tags/v1.2.3`.
   *
   * Ours, not OCI's, because OCI defines no key for "the ref this was built from" -- see
   * docs/build-and-provenance.md. Fully qualified so nothing has to guess whether a name is
   * a branch or a tag, or whether a tag carries a `v` its version does not.
   */
  sourceRef: 'FLEET_SOURCE_REF',
} as const;

/**
 * Keys accepted for the ref, in preference order.
 *
 * `ORG_OPENCONTAINERS_IMAGE_VERSION` is a fallback for images built before the label existed:
 * `docker/metadata-action` fills it from the branch, so it happens to carry `main` today. It
 * stops being the ref the moment anything is tagged with a version, which is exactly why it
 * is a fallback and not the answer. `..._REF_NAME` is the spelling the disk image used before
 * this changed. Both can go once nothing older is deployed.
 */
const REF_KEYS = [
  CONTROLLED.sourceRef,
  'ORG_OPENCONTAINERS_IMAGE_REF_NAME',
  'ORG_OPENCONTAINERS_IMAGE_VERSION',
] as const;

export interface Manifest {
  /** Browsable repo URL, for linking. */
  source?: string;
  /** Full git sha this was built from. */
  revision?: string;
  /**
   * The ref it was built from, fully qualified where the artifact says so -- `refs/heads/main`
   * or `refs/tags/v1.2.3`. Passed to the API verbatim; both forms resolve.
   */
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
  // First key present wins, but every one of them is consumed, so a fallback spelling does
  // not linger in `extra` and get rendered as though it were something else.
  let refName: string | undefined;
  for (const k of REF_KEYS) {
    const v = take(k);
    refName ??= v;
  }
  return {
    source: take(CONTROLLED.source),
    revision: take(CONTROLLED.revision),
    refName,
    extra,
  };
}

/** Short form for display. Full sha is what gets compared; this is what gets shown. */
export function shortSha(sha: string | undefined): string {
  return sha === undefined ? '' : sha.slice(0, 10);
}

// ---- the probe: several units in one document ------------------------------------------
//
// `coord version` emits one TOML table per versioned thing on a machine (coordinator#327).
// The table name is a human-readable label and is never parsed: a unit's kind and identity
// come from FLEET_UNIT_KIND and FLEET_UNIT_ID, because sniffing a kind out of a name prefix
// is guesswork, and because sanitised names can collide and silently merge two units.

/** Which button a row gets. Unknown kinds are carried rather than dropped. */
export type UnitKind = 'disk_image' | 'checkout' | 'container';

export interface ProbeUnit extends Manifest {
  kind: UnitKind | string;
  /** Stable and unique within a machine. Identity, not an action parameter. */
  id: string;
  /** The table name. Display only -- never matched on. */
  label: string;
}

export interface Probe {
  units: ProbeUnit[];
  /** The `[host]` table: probe version, stacks installed, whole-machine errors. */
  host: Record<string, string>;
}

const TABLE = /^\[([^\]]+)\]$/;

/** Split a document into tables, in file order. Lines outside any table are ignored. */
function tables(text: string): Array<{ name: string; body: string }> {
  const out: Array<{ name: string; body: string }> = [];
  let current: { name: string; body: string } | undefined;
  for (const raw of text.split('\n')) {
    const t = TABLE.exec(raw.trim());
    if (t?.[1] !== undefined) {
      current = { name: t[1], body: '' };
      out.push(current);
    } else if (current) {
      current.body += `${raw}\n`;
    }
  }
  return out;
}

/**
 * Parse `coord version` output.
 *
 * A table missing FLEET_UNIT_KIND or FLEET_UNIT_ID is skipped rather than guessed at -- the
 * whole point of carrying them is that identity does not depend on the label.
 */
export function parseProbe(text: string): Probe {
  const units: ProbeUnit[] = [];
  let host: Record<string, string> = {};
  for (const { name, body } of tables(text)) {
    const m = parseManifest(body);
    if (name === 'host') {
      host = m.extra;
      continue;
    }
    const kind = m.extra.FLEET_UNIT_KIND;
    const id = m.extra.FLEET_UNIT_ID;
    if (kind === undefined || id === undefined) continue;
    delete m.extra.FLEET_UNIT_KIND;
    delete m.extra.FLEET_UNIT_ID;
    units.push({ ...m, kind, id, label: name });
  }
  return { units, host };
}
