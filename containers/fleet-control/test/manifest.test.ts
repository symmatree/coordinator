import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseManifest, shortSha } from '../src/manifest.js';

const REAL = `# Written by dotfiles-symm pi-image/build-image.sh at build time.
ORG_OPENCONTAINERS_IMAGE_SOURCE="https://github.com/symmatree/dotfiles-symm"
ORG_OPENCONTAINERS_IMAGE_REVISION="31bb65c20ba499ecc65c1e3c31e1a533b2c1cda5"
ORG_OPENCONTAINERS_IMAGE_REF_NAME="main"
FLEET_ROLE="campod"
FLEET_IMAGE="campod-pi-20260918.img"
FLEET_BASE="2026-09-15-raspios-trixie-arm64-lite.img.xz"
`;

test('pulls the controlled keys out and leaves the rest alone', () => {
  const m = parseManifest(REAL);
  assert.equal(m.revision, '31bb65c20ba499ecc65c1e3c31e1a533b2c1cda5');
  assert.equal(m.refName, 'main');
  assert.equal(m.source, 'https://github.com/symmatree/dotfiles-symm');
  // Artifact-specific keys are passed through verbatim, not enumerated or validated.
  assert.deepEqual(m.extra, {
    FLEET_ROLE: 'campod',
    FLEET_IMAGE: 'campod-pi-20260918.img',
    FLEET_BASE: '2026-09-15-raspios-trixie-arm64-lite.img.xz',
  });
});

test('a key nobody agreed on still shows up', () => {
  const m = parseManifest('FLEET_SOMETHING_NEW="42"\n');
  assert.equal(m.extra.FLEET_SOMETHING_NEW, '42');
});

test('comments and blank lines are ignored', () => {
  const m = parseManifest('\n# a comment\n\nFLEET_ROLE="campod"\n');
  assert.deepEqual(m.extra, { FLEET_ROLE: 'campod' });
});

test('spaces around = are rejected, because `source` rejects them too', () => {
  // TOML permits `K = "v"` and every non-shell parser accepts it, but sourcing the file
  // fails with `K: command not found`. Accepting it here would hide that from whoever
  // wrote the manifest (coordinator#326).
  const m = parseManifest('FLEET_ROLE = "campod"\n');
  assert.deepEqual(m.extra, {});
});

test('one bad line costs that line, not the file', () => {
  const m = parseManifest('FLEET_ROLE="campod"\nnonsense\nFLEET_IMAGE="x.img"\n');
  assert.deepEqual(m.extra, { FLEET_ROLE: 'campod', FLEET_IMAGE: 'x.img' });
});

test('an empty manifest parses to nothing rather than throwing', () => {
  const m = parseManifest('');
  assert.equal(m.revision, undefined);
  assert.deepEqual(m.extra, {});
});

test('shortSha survives an absent revision', () => {
  assert.equal(shortSha(undefined), '');
  assert.equal(shortSha('31bb65c20ba499ecc65c1e3c31e1a533b2c1cda5'), '31bb65c20b');
});

test('prefers FLEET_SOURCE_REF, fully qualified', () => {
  const m = parseManifest(
    'FLEET_SOURCE_REF="refs/heads/main"\nORG_OPENCONTAINERS_IMAGE_VERSION="main"\n',
  );
  assert.equal(m.refName, 'refs/heads/main');
  // Every accepted spelling is consumed, so a fallback does not show up again as an extra.
  assert.deepEqual(m.extra, {});
});

test('a tag ref keeps its own name, which need not match the version', () => {
  // The reason the ref is carried fully qualified: a release is version 1.2.3 and ref
  // refs/tags/v1.2.3, and neither can be derived from the other without guessing at a `v`.
  const m = parseManifest(
    'FLEET_SOURCE_REF="refs/tags/v1.2.3"\nORG_OPENCONTAINERS_IMAGE_VERSION="1.2.3"\n',
  );
  assert.equal(m.refName, 'refs/tags/v1.2.3');
});

test('falls back to VERSION for an image built before the label existed', () => {
  const m = parseManifest('ORG_OPENCONTAINERS_IMAGE_VERSION="main"\n');
  assert.equal(m.refName, 'main');
});

test('falls back to the old REF_NAME spelling the disk image used', () => {
  const m = parseManifest('ORG_OPENCONTAINERS_IMAGE_REF_NAME="main"\n');
  assert.equal(m.refName, 'main');
});

test('no ref at all is undefined rather than a guess', () => {
  const m = parseManifest('ORG_OPENCONTAINERS_IMAGE_REVISION="abc"\n');
  assert.equal(m.refName, undefined);
});
