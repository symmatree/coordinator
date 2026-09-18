// What this service does to a node: converge it.
//
// ONE action, not two. `bootstrap` and `update` used to differ because a fresh card needed a
// remount, an apt install, a clone, and an exit-1 reboot dance that an already-provisioned
// node did not. #263 deletes all of that: the same playbook handles a virgin unit and a
// running one, and presenting two buttons that run identical commands would be a lie about
// what the service does.
//
// The playbook stops data collection before it starts, reboots and waits when something
// actually changed, and starts the stack again on the way out. Exit 0 means converged.

import { hostOf, type FleetNode, type Inventory } from './inventory.js';
import { converge as runPlaybook, type EventSink } from './ansible.js';
import { forgetHostKey } from './knownhosts.js';

export class ActionError extends Error {}

export interface ActionContext {
  inventory: Inventory;
  privateKeyPath: string;
  sshTimeoutSec: number;
  knownHostsPath: string;
}

function note(sink: EventSink | undefined, msg: string): void {
  sink?.('stdout', `[fleet-control] ${msg}`);
}

/**
 * Converge a node to the merged state of the repo.
 *
 * `reflashed` clears the recorded SSH host key first. A reflashed card legitimately presents a
 * new key for the same address, which is a *changed* key rather than an unknown one --
 * `accept-new` does not help, and Ansible surfaces the refusal as a bare UNREACHABLE with the
 * ssh error buried. Clearing it is a caller decision, so it stays here and the playbook
 * encodes no trust policy.
 */
export async function converge(
  node: FleetNode,
  ctx: ActionContext,
  opts: { reflashed?: boolean } = {},
  sink?: EventSink,
): Promise<void> {
  const host = hostOf(node);
  note(sink, `converging ${node.name} (${host}) as ${node.role}`);

  if (opts.reflashed) {
    const forgotten = await forgetHostKey(host, ctx.knownHostsPath);
    note(sink, forgotten ? `cleared the recorded host key for ${host}` : `no recorded host key for ${host}`);
  }

  const rc = await runPlaybook({
    host,
    user: ctx.inventory.user,
    privateKeyPath: ctx.privateKeyPath,
    sshTimeoutSec: ctx.sshTimeoutSec,
    knownHostsPath: ctx.knownHostsPath,
    extraVars: { device_role: node.role },
    sink,
  });

  if (rc !== 0) {
    throw new ActionError(
      `${node.name}: converge failed (ansible-runner exit ${rc}). See the output above; the ` +
        `play recap names the failing task and host.`,
    );
  }
  note(sink, `${node.name} converged`);
}

/**
 * Reimage a node: stage a disk image in its FAT partition and arm the tryboot flasher.
 *
 * The play stages and arms; it does not verify (#324). Whether it worked is answered the
 * same way everything else is -- probe the machine afterwards and see what it says it is.
 * There is no in-flight state here worth protecting: the device drops off the network, comes
 * back or does not, and the status screen is the completion check.
 *
 * Worst case is a card pull, which is what a reflash costs today.
 */
export async function reimage(
  node: FleetNode,
  ctx: ActionContext,
  image: { url: string; sha256: string; sha: string },
  sink?: EventSink,
): Promise<void> {
  const host = hostOf(node);
  note(sink, `reimaging ${node.name} (${host}) as ${node.role}`);
  note(sink, `image built from ${image.sha.slice(0, 10)}`);
  note(sink, `device fetches ${image.url}`);

  const rc = await runPlaybook({
    playbook: 'reimage.yaml',
    host,
    user: ctx.inventory.user,
    privateKeyPath: ctx.privateKeyPath,
    sshTimeoutSec: ctx.sshTimeoutSec,
    knownHostsPath: ctx.knownHostsPath,
    extraVars: {
      device_role: node.role,
      reimage_image_url: image.url,
      reimage_image_sha256: image.sha256,
      // The play clears the recorded host key itself, because reimaging is what changes it --
      // it is recording the consequence of its own action rather than trusting a stranger.
      reimage_known_hosts: ctx.knownHostsPath,
    },
    sink,
  });

  if (rc !== 0) {
    throw new ActionError(
      `${node.name}: reimage failed (ansible-runner exit ${rc}). The device may still be on ` +
        'its old image; probe it to see what it reports.',
    );
  }
  note(sink, `${node.name} armed; it will drop off the network and come back on the new image`);
}
