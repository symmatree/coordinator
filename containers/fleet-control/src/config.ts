// Configuration, all from the environment.
import { loadInventory, type Inventory } from './inventory.js';
import type { ActionContext } from './actions.js';

export interface Config {
  inventory: Inventory;
  action: ActionContext;
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
    action: {
      inventory,
      privateKeyPath: env('FLEET_SSH_KEY', '/secrets/ssh/id'),
      // Ansible's own default is 10s, which a Zero under a converge misses; the handshake
      // failures on campod-se are what this number is sized against.
      sshTimeoutSec: Number(env('FLEET_SSH_TIMEOUT_SEC', '90')),
    },
    port: Number(env('PORT', '8080')),
    host: env('HOST', '0.0.0.0'),
  };
}
