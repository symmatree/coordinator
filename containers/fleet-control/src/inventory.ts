// The fleet roster: which nodes exist and how to reach them.
//
// Supplied by the deployment via FLEET_INVENTORY; there is no default. A host may be named or
// addressed -- whichever the deployment has.

import { readFileSync } from 'node:fs';
import { z } from 'zod';

export const FleetNode = z.object({
  /** Hostname as flashed: `coordinator`, `campod-ne`, ... */
  name: z.string().min(1),
  /** Hostname or IP to connect to. Defaults to `name`. */
  host: z.string().min(1).optional(),
  role: z.enum(['coordinator', 'campod']),
});
export type FleetNode = z.infer<typeof FleetNode>;

export const Inventory = z.object({
  user: z.string().min(1).default('pi'),
  nodes: z.array(FleetNode).min(1),
});
export type Inventory = z.infer<typeof Inventory>;

export function parseInventory(raw: unknown): Inventory {
  return Inventory.parse(raw);
}

export function loadInventory(path: string): Inventory {
  return parseInventory(JSON.parse(readFileSync(path, 'utf8')));
}

export function findNode(inv: Inventory, name: string): FleetNode | undefined {
  return inv.nodes.find((n) => n.name === name);
}

export function hostOf(node: FleetNode): string {
  return node.host ?? node.name;
}
