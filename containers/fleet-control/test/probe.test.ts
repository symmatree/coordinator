import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { interpret, deriveStage, EXPECTED_FAILED_UNITS } from '../src/probe.js';

const base = { node: 'campod-sw', address: '10.0.2.49', role: 'campod' };
const t = (k: string, v: string) => `${k}\t${v}`;

/**
 * The real state of campod-sw, read-only, on 2026-09-13 -- a freshly flashed card that had
 * booted but had never been bootstrapped. Field VALUES are as observed on the hardware; the
 * tab-separated framing is what probe.sh emits. This is the exact state the service has to
 * recognise, because it is the state a node is in when it is handed over for provisioning.
 */
const FRESHLY_FLASHED = [
  t('hostname', 'campod-sw'),
  t('arch', 'aarch64'),
  t('os_codename', 'bookworm'),
  t('fleet_IMAGE', 'campod-pi-20260912.img.xz'),
  t('fleet_ROLE', 'campod'),
  t('fleet_SOURCE', '16b1d689eae5b253ad809d7fc58149a8637c3c8c'),
  t('fleet_BASE', '2025-05-13-raspios-bookworm-arm64-lite.img.xz'),
  t('checkout_present', '0'),
  t('coord_present', '0'),
  t('docker_present', '0'),
  t('docker_group', '0'),
  t('failed_unit', 'resize2fs_once.service'),
  t('reboot_required', '0'),
  t('data_mount', '/var/lib/campod=btrfs rw,noatime,compress=zstd:3,subvol=/@data'),
].join('\n');

describe('interpret -- a freshly flashed, un-bootstrapped node', () => {
  const p = interpret(base, FRESHLY_FLASHED);

  it('calls it blank, not merely reachable', () => {
    assert.equal(p.stage, 'blank');
  });

  it('reads /etc/fleet-image, so staleness can be reported rather than assumed', () => {
    assert.equal(p.fleetImage?.IMAGE, 'campod-pi-20260912.img.xz');
    assert.equal(p.fleetImage?.ROLE, 'campod');
  });

  it('does not claim a checkout, a stack, or docker', () => {
    assert.equal(p.checkoutPresent, false);
    assert.deepEqual(p.stacks, []);
    assert.equal(p.dockerInstalled, false);
    assert.deepEqual(p.containers, []);
  });

  it('sees resize2fs_once failed but does not call it a fault', () => {
    assert.deepEqual(p.failedUnits, ['resize2fs_once.service']);
    assert.deepEqual(p.unexpectedFailedUnits, []);
  });

  it('confirms the data subvolume is the btrfs @data mount, not a dir on @var', () => {
    assert.ok(p.dataMount?.includes('subvol=/@data'));
  });
});

describe('interpret -- a real failure is still surfaced', () => {
  it('separates an unexpected failed unit from the known-benign one', () => {
    const p = interpret(base, FRESHLY_FLASHED + '\n' + t('failed_unit', 'docker.service'));
    assert.deepEqual(p.failedUnits, ['resize2fs_once.service', 'docker.service']);
    assert.deepEqual(p.unexpectedFailedUnits, ['docker.service']);
  });

  it('keeps resize2fs_once as the only expected failure', () => {
    assert.deepEqual([...EXPECTED_FAILED_UNITS], ['resize2fs_once.service']);
  });
});

describe('interpret -- parsing', () => {
  it('keeps values containing tabs intact (container name + status)', () => {
    const p = interpret(base, t('container', 'campod-camera\tUp 3 minutes'));
    assert.deepEqual(p.containers, [{ name: 'campod-camera', status: 'Up 3 minutes' }]);
  });

  it('collects repeated keys in order', () => {
    const p = interpret(base, [t('stack', 'campod'), t('stack', 'other')].join('\n'));
    assert.deepEqual(p.stacks, ['campod', 'other']);
  });

  it('ignores blank and malformed lines rather than throwing', () => {
    const p = interpret(base, ['', 'no-tab-here', t('hostname', 'campod-sw'), ''].join('\n'));
    assert.equal(p.hostname, 'campod-sw');
  });

  it('surfaces a pending reboot -- a normal one_time.sh path, not a failure', () => {
    assert.equal(interpret(base, t('reboot_required', '1')).rebootRequired, true);
  });
});

describe('deriveStage', () => {
  const s = (o: Record<string, unknown>) =>
    deriveStage({ ...base, ...o } as Parameters<typeof deriveStage>[0]);

  it('blank: nothing installed', () => {
    assert.equal(s({ dockerInstalled: false, checkoutPresent: false }), 'blank');
  });

  it('partial: a checkout but no docker -- bootstrap was interrupted', () => {
    assert.equal(s({ dockerInstalled: false, checkoutPresent: true }), 'partial');
  });

  it('partial: docker but no checkout -- the deploy symlink has no target', () => {
    assert.equal(s({ dockerInstalled: true, checkoutPresent: false }), 'partial');
  });

  it('partial: checkout and docker but no stack laid down', () => {
    assert.equal(s({ dockerInstalled: true, checkoutPresent: true, stacks: [] }), 'partial');
  });

  it('bootstrapped: ready, but nothing running', () => {
    assert.equal(
      s({ dockerInstalled: true, checkoutPresent: true, stacks: ['campod'], containers: [] }),
      'bootstrapped',
    );
  });

  it('running: containers are up', () => {
    assert.equal(
      s({
        dockerInstalled: true,
        checkoutPresent: true,
        stacks: ['campod'],
        containers: [{ name: 'campod-camera', status: 'Up 1 minute' }],
      }),
      'running',
    );
  });
});
