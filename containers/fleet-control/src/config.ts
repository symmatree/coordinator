// Configuration, all from the environment.
import { loadInventory, type Inventory } from './inventory.js';
import type { ActionContext } from './actions.js';
import type { GithubOptions } from './github.js';

export interface Config {
  inventory: Inventory;
  action: ActionContext;
  /** Where disk images are built and which ref the fleet tracks. */
  images: GithubOptions & { cacheDir: string; publicUrl: string };
  /** Where recovered flights land -- the datasets share. */
  flightsDir: string;
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
      // On the /state volume so recorded keys survive a pod restart -- otherwise every
      // restart is a fresh first contact and the check protects nothing.
      knownHostsPath: env('FLEET_KNOWN_HOSTS', '/state/known_hosts'),
    },
    images: {
      repo: env('FLEET_IMAGE_REPO', 'symmatree/dotfiles-symm'),
      workflow: env('FLEET_IMAGE_WORKFLOW', 'build-pi-image.yaml'),
      ref: env('FLEET_IMAGE_REF', 'main'),
      // Listing builds and artifacts is public; only downloading a zip needs this. Absent is
      // a working configuration -- the status screen does not touch it.
      token: process.env.FLEET_GITHUB_TOKEN || undefined,
      // Large and rebuildable, so it wants its own space rather than sharing /state with
      // known_hosts. Nothing evicts; it is wiped deliberately.
      cacheDir: env('FLEET_IMAGE_CACHE', '/images'),
      // Where a DEVICE can reach this service. It fetches the image itself with get_url, so
      // the in-cluster service name is no use to it.
      publicUrl: env('FLEET_PUBLIC_URL', 'https://fleet.tiles.symmatree.com').replace(/\/$/, ''),
    },
    // The datasets share, mounted from a static PV (tiles#764). Recovered flights go here.
    flightsDir: env('FLEET_FLIGHTS_DIR', '/mnt/flights'),
    port: Number(env('PORT', '8080')),
    host: env('HOST', '0.0.0.0'),
  };
}
