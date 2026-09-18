import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseProbe } from '../src/manifest.js';
import { enrichUnit, LookupCache, repoFromUrl, type Lookups } from '../src/status.js';

// The shape agreed for `coord version` in coordinator#327: one table per unit, kind and id
// carried as data so nothing has to be inferred from the table name.
const PROBE = `[disk_image]
FLEET_UNIT_KIND="disk_image"
FLEET_UNIT_ID="disk_image"
ORG_OPENCONTAINERS_IMAGE_SOURCE="https://github.com/symmatree/dotfiles-symm"
ORG_OPENCONTAINERS_IMAGE_REVISION="2bc2aecd7e00000000000000000000000000aaaa"
ORG_OPENCONTAINERS_IMAGE_REF_NAME="main"
FLEET_ROLE="campod"

[container_campod_camera]
FLEET_UNIT_KIND="container"
FLEET_UNIT_ID="campod-camera"
ORG_OPENCONTAINERS_IMAGE_SOURCE="https://github.com/symmatree/coordinator"
ORG_OPENCONTAINERS_IMAGE_REVISION="1111111111111111111111111111111111111111"
ORG_OPENCONTAINERS_IMAGE_REF_NAME="main"
FLEET_CONTAINER_STATE="running"

[host]
FLEET_PROBE_VERSION="1"
FLEET_STACKS="campod"
`;

test('units come back with kind and id as data, not sniffed from the label', () => {
  const p = parseProbe(PROBE);
  assert.equal(p.units.length, 2);
  assert.equal(p.units[0]?.kind, 'disk_image');
  assert.equal(p.units[1]?.kind, 'container');
  assert.equal(p.units[1]?.id, 'campod-camera');
  // The label is kept for display but is not the identity.
  assert.equal(p.units[1]?.label, 'container_campod_camera');
  assert.equal(p.host.FLEET_PROBE_VERSION, '1');
});

test('kind and id are consumed, not left rattling around in extras', () => {
  const p = parseProbe(PROBE);
  assert.equal(p.units[1]?.extra.FLEET_UNIT_KIND, undefined);
  assert.equal(p.units[1]?.extra.FLEET_CONTAINER_STATE, 'running');
});

test('a table without kind or id is skipped rather than guessed at', () => {
  const p = parseProbe('[mystery]\nSOMETHING="1"\n');
  assert.deepEqual(p.units, []);
});

test('two units whose labels would collide stay distinct', () => {
  // The reason identity is carried rather than derived: sanitising `campod-camera` and
  // `campod_camera` to one table name would otherwise merge two units silently.
  const p = parseProbe(
    '[container_campod_camera]\nFLEET_UNIT_KIND="container"\nFLEET_UNIT_ID="campod-camera"\n\n' +
      '[container_campod_camera]\nFLEET_UNIT_KIND="container"\nFLEET_UNIT_ID="campod_camera"\n',
  );
  assert.deepEqual(
    p.units.map((u) => u.id),
    ['campod-camera', 'campod_camera'],
  );
});

test('repoFromUrl handles the forms a manifest might carry', () => {
  assert.equal(repoFromUrl('https://github.com/symmatree/coordinator'), 'symmatree/coordinator');
  assert.equal(repoFromUrl('https://github.com/symmatree/coordinator.git'), 'symmatree/coordinator');
  assert.equal(repoFromUrl('git@github.com:symmatree/coordinator.git'), 'symmatree/coordinator');
  assert.equal(repoFromUrl('https://example.invalid/x/y'), undefined);
  assert.equal(repoFromUrl(undefined), undefined);
});

function fakeLookups(head: string, titles: Record<string, string> = {}): Lookups & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    async head(repo, ref) {
      calls.push(`head ${repo}#${ref}`);
      return head;
    },
    async title(repo, sha) {
      calls.push(`title ${repo}@${sha.slice(0, 6)}`);
      return titles[sha] ?? `title for ${sha.slice(0, 6)}`;
    },
  };
}

test('a unit at head is marked current', async () => {
  const unit = parseProbe(PROBE).units[0]!;
  const got = await enrichUnit(unit, fakeLookups('2bc2aecd7e00000000000000000000000000aaaa'));
  assert.equal(got.current, true);
  assert.equal(got.repo, 'symmatree/dotfiles-symm');
  assert.equal(got.headTitle, undefined, 'no second title when already at head');
});

test('a stale unit shows both titles, so the difference is readable', async () => {
  const unit = parseProbe(PROBE).units[0]!;
  const got = await enrichUnit(unit, fakeLookups('ffffffffffffffffffffffffffffffffffffffff'));
  assert.equal(got.current, false);
  assert.ok(got.title, 'what is installed');
  assert.ok(got.headTitle, 'what head is');
});

test('a lookup failure lands on the unit instead of taking the screen down', async () => {
  const unit = parseProbe(PROBE).units[0]!;
  const broken: Lookups = {
    head: async () => {
      throw new Error('GitHub 403 rate limited');
    },
    title: async () => 'unused',
  };
  const got = await enrichUnit(unit, broken);
  assert.match(got.lookupError ?? '', /rate limited/);
  assert.equal(got.current, undefined);
});

test('a unit with no source is passed through rather than dropped', async () => {
  const p = parseProbe('[odd]\nFLEET_UNIT_KIND="checkout"\nFLEET_UNIT_ID="/home/pi/x"\n');
  const got = await enrichUnit(p.units[0]!, fakeLookups('x'));
  assert.equal(got.id, '/home/pi/x');
  assert.equal(got.repo, undefined);
  assert.equal(got.current, undefined);
});

test('a sha title is fetched once and never again', async () => {
  // The property the 60/hour unauthenticated budget rests on: a commit's PR cannot change,
  // so repeated refreshes of the same fleet cost nothing for titles.
  let calls = 0;
  const cache = new LookupCache(undefined, 60_000, () => 0, {
    title: async () => {
      calls += 1;
      return 'a title';
    },
    head: async () => 'head',
  });
  await cache.title('r', 'sha');
  await cache.title('r', 'sha');
  await cache.title('r', 'sha');
  assert.equal(calls, 1);
  await cache.title('r', 'other');
  assert.equal(calls, 2, 'a different sha is a different answer');
});

test('head is re-asked once its window passes, because refs move', async () => {
  let now = 1_000_000;
  let calls = 0;
  const cache = new LookupCache(undefined, 60_000, () => now, {
    title: async () => 'unused',
    head: async () => {
      calls += 1;
      return `head${calls}`;
    },
  });
  assert.equal(await cache.head('r', 'main'), 'head1');
  now += 30_000;
  assert.equal(await cache.head('r', 'main'), 'head1', 'still inside the window');
  assert.equal(calls, 1);
  now += 31_000;
  assert.equal(await cache.head('r', 'main'), 'head2', 'window passed, asked again');
  assert.equal(calls, 2);
});
