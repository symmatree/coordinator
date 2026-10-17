// Configuration, all from the environment.
import { loadInventory, type Inventory } from './inventory.js';
import type { ActionContext } from './actions.js';
import type { GithubOptions } from './github.js';
import type { BusConfig } from './bus.js';

export interface Config {
  inventory: Inventory;
  action: ActionContext;
  /** Where disk images are built and which ref the fleet tracks. */
  images: GithubOptions & { cacheDir: string; publicUrl: string };
  /** Where recovered flights land -- the datasets share. */
  flightsDir: string;
  /** Where notifications go. An empty url disables them. See notify.ts. */
  notify: { url: string; tag: string };
  /** The pod bus. An empty url disables it. See bus.ts and docs/pod-bus.md. */
  bus: BusConfig;
  /** Where the ground station's own record of a flight lives. See cluster.ts. */
  cluster: {
    mimirUrl: string;
    mimirTenant: string;
    /** Where tlog-split writes its per-flight tlogs (tiles#794), mounted read-only. */
    groundTlogs: string;
  };
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
  const bus = {
    // The broker is mochi-mqtt on the coordinator, listening on every interface, so this is
    // the coordinator's own address -- not a cluster service.
    url: env('FLEET_MQTT_URL', ''),
    // Retained messages arrive right after SUBACK with no count to expect, so the read ends by
    // stopping rather than by being satisfied. Long enough for four pods on a 2.4 GHz hop.
    settleMs: Number(env('FLEET_MQTT_SETTLE_MS', '1500')),
  };
  return {
    inventory,
    action: {
      inventory,
      privateKeyPath: env('FLEET_SSH_KEY', '/secrets/ssh/id'),
      // Ansible's own default is 10s, which a Zero under a converge misses; the handshake
      // failures on campod-se are what this number is sized against.
      sshTimeoutSec: Number(env('FLEET_SSH_TIMEOUT_SEC', '90')),
      // A device is quiesced over the bus, not over ssh, so reaching one needs this.
      bus,
      // Playbook runs happen in a pod of their own. The template is declared in tiles,
      // tanka/environments/fleet-control; this is only where to submit against it.
      workflows: {
        // The namespace this pod runs in, which is where its workflows go. The default is
        // the one the tanka environment deploys into; override it if that ever moves.
        namespace: env('FLEET_NAMESPACE', 'fleet-control'),
        template: env('FLEET_WORKFLOW_TEMPLATE', 'fleet-control-playbook'),
      },
      // Where the published playbooks come from. A different repo and workflow from the
      // device images above, and the same token serves both.
      github: {
        repo: env('FLEET_PLAYBOOK_REPO', 'symmatree/coordinator'),
        workflow: env('FLEET_PLAYBOOK_WORKFLOW', 'ansible-payload.yaml'),
        ref: env('FLEET_PLAYBOOK_REF', 'main'),
        token: process.env.FLEET_GITHUB_TOKEN || undefined,
      },
    },
    images: {
      repo: env('FLEET_IMAGE_REPO', 'symmatree/dotfiles-symm'),
      workflow: env('FLEET_IMAGE_WORKFLOW', 'build-pi-image.yaml'),
      ref: env('FLEET_IMAGE_REF', 'main'),
      // Listing builds and artifacts is public; only downloading a zip needs this. Absent is
      // a working configuration -- the status screen does not touch it.
      token: process.env.FLEET_GITHUB_TOKEN || undefined,
      // Large and rebuildable, so it wants its own volume. Nothing evicts; it is wiped
      // deliberately.
      cacheDir: env('FLEET_IMAGE_CACHE', '/images'),
      // Where a DEVICE can reach this service. It fetches the image itself with get_url, so
      // the in-cluster service name is no use to it.
      publicUrl: env('FLEET_PUBLIC_URL', 'https://fleet.tiles.symmatree.com').replace(/\/$/, ''),
    },
    // The datasets share, mounted from a static PV (tiles#764). Recovered flights go here.
    flightsDir: env('FLEET_FLIGHTS_DIR', '/mnt/flights'),
    notify: {
      url: env('FLEET_NOTIFY_URL', ''),
      // Apprise routes by tag and an untagged notify reaches nobody, so this defaults to the
      // cluster name the way Alloy's notifier does rather than to empty.
      tag: env('FLEET_NOTIFY_TAG', 'tiles'),
    },
    bus,
    cluster: {
      mimirUrl: env('FLEET_MIMIR_URL', 'http://mimir-gateway.mimir.svc'),
      // The Mimir tenant is the cluster name (charts/.../alloy-application.yaml), so it is
      // configured rather than assumed -- there is more than one cluster.
      mimirTenant: env('FLEET_MIMIR_TENANT', 'tiles'),
      groundTlogs: env('FLEET_GROUND_TLOGS', '/mnt/ground-tlogs'),
    },
    port: Number(env('PORT', '8080')),
    host: env('HOST', '0.0.0.0'),
  };
}
