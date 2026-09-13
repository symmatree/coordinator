// Thin CLI over the same actions the HTTP API exposes -- handy for bench work and for
// verifying behaviour against real hardware without standing the service up.
import { loadInventory, enabledNodes, findNode } from './inventory.js';
import { HostKeyStore } from './hostkeys.js';
import { probeAll, probeNode } from './probe.js';
import type { SessionOptions } from './ssh.js';

const inventoryPath = process.env.FLEET_INVENTORY ?? 'inventory.json';
const opts: SessionOptions = {
  privateKeyPath: process.env.FLEET_SSH_KEY ?? `${process.env.HOME}/.ssh/OnePKey`,
  hostKeys: new HostKeyStore(process.env.FLEET_HOSTKEYS ?? '/tmp/fleet-hostkeys.json'),
};

const inv = loadInventory(inventoryPath);
const target = process.argv[2];
const nodes = target ? [findNode(inv, target)].filter((n) => n !== undefined) : enabledNodes(inv);
if (nodes.length === 0) {
  console.error(`no such node: ${target}`);
  process.exit(2);
}

const results = target ? [await probeNode(nodes[0]!, opts)] : await probeAll(nodes, opts);
console.log(JSON.stringify(results, null, 2));
