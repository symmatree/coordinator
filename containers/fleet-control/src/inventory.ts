// Who is in the fleet, and how to reach them.
//
// Addresses are STATIC and configured, not resolved at runtime -- the owner's call, and there
// is known DNS instability on this network. What was observed directly (2026-09-13): the
// coordinator resolves consistently by name, and no campod resolved at all in the same
// window. No cause for that is established here, and the service should not depend on it
// being fixed. A fixed mapping makes a moved node a visible config change rather than a
// resolver behaviour.
//
// Refs: coordinator#236 (this service), coordinator#223 (the epic).

import { readFileSync } from 'node:fs';

export type Role = 'coordinator' | 'campod';

export interface FleetNode {
  /** Hostname as flashed -- `coordinator`, `campod-ne` ... Also the campod capture-dir name. */
  name: string;
  /** IP address. Deliberately not a name; see the note above. */
  address: string;
  role: Role;
  /** Login. One account, fleet-wide, with passwordless sudo (coordinator#238 item 4). */
  user: string;
  /** Set false for a unit that is not flashed yet, so it is listed but not probed. */
  enabled: boolean;
}

export interface Inventory {
  nodes: FleetNode[];
}

const ROLES = new Set<Role>(['coordinator', 'campod']);

/** Parse + validate an inventory document. Throws on anything malformed: a typo'd address
 *  here means the service quietly talks to the wrong host, so this fails loudly instead. */
export function parseInventory(raw: unknown): Inventory {
  if (typeof raw !== 'object' || raw === null) throw new Error('inventory: not an object');
  const nodes = (raw as { nodes?: unknown }).nodes;
  if (!Array.isArray(nodes)) throw new Error('inventory: `nodes` must be an array');

  const seen = new Set<string>();
  const parsed = nodes.map((n, i) => {
    const at = `inventory: nodes[${i}]`;
    if (typeof n !== 'object' || n === null) throw new Error(`${at}: not an object`);
    const o = n as Record<string, unknown>;

    const name = o.name;
    if (typeof name !== 'string' || name.length === 0) throw new Error(`${at}: missing name`);
    if (seen.has(name)) throw new Error(`${at}: duplicate name '${name}'`);
    seen.add(name);

    // An address is required only for a node we will actually contact. A not-yet-flashed
    // unit is listed so the roster is complete and enabling it later is a one-word change.
    const enabled = o.enabled === undefined ? true : o.enabled === true;
    const address = typeof o.address === 'string' ? o.address : '';
    if (enabled && address.length === 0) {
      throw new Error(`${at} (${name}): enabled node has no address`);
    }

    const role = o.role;
    if (typeof role !== 'string' || !ROLES.has(role as Role)) {
      throw new Error(`${at} (${name}): role must be one of ${[...ROLES].join(', ')}`);
    }

    return {
      name,
      address,
      role: role as Role,
      user: typeof o.user === 'string' ? o.user : 'pi',
      enabled,
    } satisfies FleetNode;
  });

  return { nodes: parsed };
}

export function loadInventory(path: string): Inventory {
  return parseInventory(JSON.parse(readFileSync(path, 'utf8')));
}

export function enabledNodes(inv: Inventory): FleetNode[] {
  return inv.nodes.filter((n) => n.enabled);
}

export function findNode(inv: Inventory, name: string): FleetNode | undefined {
  return inv.nodes.find((n) => n.name === name);
}
