// Service configuration, all from the environment so the tanka env owns it.
import { HostKeyStore } from './hostkeys.js';
import { loadInventory, type Inventory } from './inventory.js';
import type { SessionOptions } from './ssh.js';

export interface Config {
  inventory: Inventory;
  inventoryPath: string;
  ssh: SessionOptions;
  port: number;
  host: string;
}

function env(name: string, fallback: string): string {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}

export function loadConfig(): Config {
  const inventoryPath = env('FLEET_INVENTORY', '/config/inventory.json');
  const hostKeysPath = env('FLEET_HOSTKEYS', '/state/hostkeys.json');
  return {
    inventoryPath,
    inventory: loadInventory(inventoryPath),
    ssh: {
      privateKeyPath: env('FLEET_SSH_KEY', '/secrets/ssh/id'),
      hostKeys: new HostKeyStore(hostKeysPath),
      timeoutMs: Number(env('FLEET_SSH_TIMEOUT_MS', '15000')),
    },
    port: Number(env('PORT', '8080')),
    host: env('HOST', '0.0.0.0'),
  };
}
