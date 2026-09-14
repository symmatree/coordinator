// Configuration, all from the environment.
import { HostKeyStore } from './hostkeys.js';
import { loadInventory, type Inventory } from './inventory.js';
import type { SessionOptions } from './ssh.js';

export interface Config {
  inventory: Inventory;
  ssh: SessionOptions;
  repoUrl: string;
  checkoutPath: string;
  port: number;
  host: string;
}

function env(name: string, fallback: string): string {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}

function required(name: string): string {
  const v = process.env[name];
  if (v === undefined || v === '') throw new Error(`${name} is required`);
  return v;
}

export function loadConfig(): Config {
  const inventory = loadInventory(required('FLEET_INVENTORY'));
  return {
    inventory,
    ssh: {
      user: inventory.user,
      privateKeyPath: env('FLEET_SSH_KEY', '/secrets/ssh/id'),
      hostKeys: new HostKeyStore(env('FLEET_HOSTKEYS', '/state/hostkeys.json')),
      timeoutMs: Number(env('FLEET_SSH_TIMEOUT_MS', '15000')),
    },
    repoUrl: env('FLEET_REPO_URL', 'https://github.com/symmatree/coordinator.git'),
    checkoutPath: env('FLEET_CHECKOUT_PATH', '$HOME/coordinator'),
    port: Number(env('PORT', '8080')),
    host: env('HOST', '0.0.0.0'),
  };
}
