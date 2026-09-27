import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { build } from '../src/build.js';

const dir = mkdtempSync(join(tmpdir(), 'build-'));

test('the service reports the build baked into its image', () => {
  // A process cannot read its own image labels, so the Dockerfile writes the #326 manifest -- the
  // same flat quoted table the devices carry, read by the same parser.
  const path = join(dir, 'container-image');
  writeFileSync(path, [
    '# fleet-control build manifest, written by its Dockerfile.',
    'ORG_OPENCONTAINERS_IMAGE_SOURCE="https://github.com/symmatree/coordinator"',
    'ORG_OPENCONTAINERS_IMAGE_REVISION="4ee3e86c1d"',
    'FLEET_SOURCE_REF="refs/heads/main"',
    'FLEET_UNIT="fleet-control"',
  ].join('\n'));
  const b = build(path);
  assert.equal(b.unit, 'fleet-control');
  assert.equal(b.revision, '4ee3e86c1d');
  assert.equal(b.refName, 'refs/heads/main');
  assert.ok(b.uptimeSec >= 0);
  assert.ok(Date.parse(b.startedAt) > 0);
});

test('no manifest is a state, not a failure', () => {
  // `npm run dev` has no image. Reporting empty beats refusing to start over provenance, and
  // beats inventing a revision that would then be compared against a branch head.
  const b = build(join(dir, 'does-not-exist'));
  assert.equal(b.revision, undefined);
  assert.equal(b.unit, undefined);
  assert.ok(b.uptimeSec >= 0);
});
