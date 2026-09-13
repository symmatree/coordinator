// Read-only node probe: what state is this node actually in?
//
// The point of this file is the `stage` it derives. "Reachable" is not the useful question --
// a node can answer SSH while being blank, half-bootstrapped, bootstrapped-but-stopped, or
// running. Those need four different next actions, and telling them apart by hand is exactly
// the keyboard-and-remembered-commands work coordinator#236 exists to remove.
//
// Two things this deliberately does NOT do:
//
//  - It does not claim the capture path works. Nothing in the campod-camera image has ever
//    run on real hardware, so `running` here means containers are up, not that frames are
//    landing. `stage` is about deployment state; capture health is a separate question and
//    must not be implied by this one.
//  - It writes nothing. Every command below is a read. This runs against a node that may be
//    mid-flight or that someone else is working on.

import { readFileSync } from 'node:fs';
import type { FleetNode } from './inventory.js';
import type { SessionOptions } from './ssh.js';
import { withSession, HostKeyMismatchError } from './ssh.js';

/**
 * Units that are failed on a healthy fleet node, and are not a fault.
 *
 * `resize2fs_once.service` is a vendor leftover: it feeds btrfs subvolume notation to an
 * ext-only tool, so it fails on every card this fleet flashes. Observed failed on both
 * `coordinator` and `campod-sw` on 2026-09-13, on freshly-flashed images. Being masked in the
 * image; listed here so that a health check does not report a whole fleet broken on day one,
 * and so that removing it from the image does not require a change here.
 */
export const EXPECTED_FAILED_UNITS = new Set(['resize2fs_once.service']);

export type Stage =
  | 'unreachable'
  | 'blank'
  | 'partial'
  | 'bootstrapped'
  | 'running';

export interface FleetImage {
  IMAGE?: string;
  ROLE?: string;
  SOURCE?: string;
  BASE?: string;
}

export interface ContainerState {
  name: string;
  status: string;
}

export interface NodeProbe {
  node: string;
  address: string;
  role: string;
  stage: Stage;
  /** Present only when stage === 'unreachable'. */
  error?: string;
  hostKeyState?: string;

  hostname?: string;
  arch?: string;
  osCodename?: string;
  fleetImage?: FleetImage;

  checkoutPresent?: boolean;
  checkoutHead?: string;
  checkoutDirty?: boolean;

  stacks?: string[];
  coordOnPath?: boolean;
  dockerInstalled?: boolean;
  inDockerGroup?: boolean;
  containers?: ContainerState[];

  failedUnits?: string[];
  /** Failed units minus the known-expected ones -- the list worth reacting to. */
  unexpectedFailedUnits?: string[];
  rebootRequired?: boolean;
  dataMount?: string;
}

/** The remote half lives in probe.sh so shellcheck/shfmt lint it like any other script. */
const PROBE_SCRIPT = readFileSync(new URL('./probe.sh', import.meta.url), 'utf8');

export function parse(out: string): Record<string, string[]> {
  const map: Record<string, string[]> = {};
  for (const line of out.split('\n')) {
    if (line.length === 0) continue;
    const tab = line.indexOf('\t');
    if (tab === -1) continue;
    const k = line.slice(0, tab);
    const v = line.slice(tab + 1);
    (map[k] ??= []).push(v);
  }
  return map;
}

const one = (m: Record<string, string[]>, k: string): string | undefined => m[k]?.[0];
const bool = (m: Record<string, string[]>, k: string): boolean => one(m, k) === '1';

export function deriveStage(p: Omit<NodeProbe, 'stage'>): Stage {
  if (!p.dockerInstalled && !p.checkoutPresent) return 'blank';
  // Docker without a checkout, or a checkout without docker, both mean an interrupted
  // bootstrap -- `one_time.sh` installs docker and lays the /opt/stacks symlink together.
  if (!p.dockerInstalled || !p.checkoutPresent || (p.stacks?.length ?? 0) === 0) return 'partial';
  return (p.containers?.length ?? 0) > 0 ? 'running' : 'bootstrapped';
}

export async function probeNode(node: FleetNode, opts: SessionOptions): Promise<NodeProbe> {
  const base = { node: node.name, address: node.address, role: node.role };
  try {
    return await withSession(node, opts, async (s) => {
      const r = await s.exec(`bash -s <<'FLEETCONTROLPROBE'\n${PROBE_SCRIPT}\nFLEETCONTROLPROBE`);
      return { ...interpret(base, r.stdout), hostKeyState: s.hostKey.state };
    });
  } catch (err) {
    const msg =
      err instanceof HostKeyMismatchError
        ? err.message
        : `${(err as Error).message}. A node that never answers has at least three causes: ` +
          `still booting, wrong WiFi credentials, or a failed first boot that powered the board ` +
          `off (coordinator#236). The third is silent and terminal -- HDMI on the coordinator, ` +
          `serial on a campod, is how to tell them apart.`;
    return { ...base, stage: 'unreachable', error: msg };
  }
}

/** Turn raw probe.sh output into a NodeProbe. Pure, so it is testable without a network. */
export function interpret(
  base: { node: string; address: string; role: string },
  stdout: string,
): NodeProbe {
  const m = parse(stdout);

  const failedUnits = m.failed_unit ?? [];
  const containers = (m.container ?? []).map((c) => {
    const tab = c.indexOf('\t');
    return tab === -1
      ? { name: c, status: '' }
      : { name: c.slice(0, tab), status: c.slice(tab + 1) };
  });

  const partial: Omit<NodeProbe, 'stage'> = {
    ...base,
    hostname: one(m, 'hostname'),
    arch: one(m, 'arch'),
    osCodename: one(m, 'os_codename'),
    fleetImage: {
      IMAGE: one(m, 'fleet_IMAGE'),
      ROLE: one(m, 'fleet_ROLE'),
      SOURCE: one(m, 'fleet_SOURCE'),
      BASE: one(m, 'fleet_BASE'),
    },
    checkoutPresent: bool(m, 'checkout_present'),
    checkoutHead: one(m, 'checkout_head'),
    checkoutDirty: bool(m, 'checkout_dirty'),
    stacks: m.stack ?? [],
    coordOnPath: bool(m, 'coord_present'),
    dockerInstalled: bool(m, 'docker_present'),
    inDockerGroup: bool(m, 'docker_group'),
    containers,
    failedUnits,
    unexpectedFailedUnits: failedUnits.filter((u) => !EXPECTED_FAILED_UNITS.has(u)),
    rebootRequired: bool(m, 'reboot_required'),
    dataMount: one(m, 'data_mount'),
  };

  return { ...partial, stage: deriveStage(partial) };
}

export async function probeAll(nodes: FleetNode[], opts: SessionOptions): Promise<NodeProbe[]> {
  return Promise.all(nodes.map((n) => probeNode(n, opts)));
}
