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
    const forgotten = await forgetHostKey(host);
    note(sink, forgotten ? `cleared the recorded host key for ${host}` : `no recorded host key for ${host}`);
  }

  const rc = await runPlaybook({
    host,
    user: ctx.inventory.user,
    privateKeyPath: ctx.privateKeyPath,
    sshTimeoutSec: ctx.sshTimeoutSec,
    // manage_checkout creates or updates the on-device clone that /opt/stacks/<role> symlinks
    // into. It defaults off in the playbook so an operator's working tree is never reset --
    // but every node this service drives is a managed fleet node, not someone's bench.
    extraVars: { device_role: node.role, manage_checkout: true },
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
