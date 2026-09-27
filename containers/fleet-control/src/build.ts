// What build this service is, and how long this process has been up.
//
// Both exist for the same reason: the pod is replaced whenever its image digest moves
// (argo-tag-watcher), and a replacement kills whatever run was in flight. So "which build am I
// talking to, and has it just restarted" is a question the operator has to be able to ask.
//
// A process cannot read its own image labels, so the Dockerfile writes /etc/container-image in
// the #326 manifest format -- the same flat quoted table the devices carry, parsed by the same
// parser. Empty values for a local build, reported as empty rather than invented.

import { readFileSync } from 'node:fs';
import { parseManifest, type Manifest } from './manifest.js';

/** When this process started. Module load is close enough and needs nothing passed in. */
const startedAt = new Date();

export interface Build {
  unit?: string;
  source?: string;
  revision?: string;
  refName?: string;
  startedAt: string;
  uptimeSec: number;
}

/**
 * Read once PER PATH: the file is baked into the image and cannot change under a running
 * process, so one read is enough -- but keyed by path, or the argument would be ignored after
 * the first call and the function would not mean what its signature says.
 */
const cached = new Map<string, Manifest>();

function manifest(path: string): Manifest {
  const hit = cached.get(path);
  if (hit !== undefined) return hit;
  let m: Manifest;
  try {
    m = parseManifest(readFileSync(path, 'utf8'));
  } catch {
    // No manifest is a legitimate state -- a local `npm run dev` has no image. Report empty
    // rather than failing to start over provenance, and rather than inventing a revision that
    // would then be compared against a branch head.
    m = { source: undefined, revision: undefined, refName: undefined, extra: {} };
  }
  cached.set(path, m);
  return m;
}

export function build(path = process.env.FLEET_BUILD_MANIFEST ?? '/etc/container-image'): Build {
  const m = manifest(path);
  return {
    unit: m.extra.FLEET_UNIT,
    source: m.source,
    revision: m.revision,
    refName: m.refName,
    startedAt: startedAt.toISOString(),
    uptimeSec: Math.round((Date.now() - startedAt.getTime()) / 1000),
  };
}
