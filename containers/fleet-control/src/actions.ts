// What this service does to a node: converge it, stop it, reboot it, reimage it.
//
// Converge is the one that changes a device. THE OTHER THREE ARE NOT SMALLER CONVERGES --
// they are the things a converge does to the device on its way past, offered on their own
// because the operator wants them on their own: a stack that is off while you read the card,
// a reboot that closes a capture session, a card that is rewritten.
//
// Converge: ONE action, not two. `bootstrap` and `update` used to differ because a fresh card needed a
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
import { stopCapture } from './sessions.js';

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
  opts: { runId: string; reflashed?: boolean },
  sink?: EventSink,
): Promise<void> {
  const host = hostOf(node);
  note(sink, `converging ${node.name} (${host}) as ${node.role}`);

  if (opts.reflashed) {
    const forgotten = await forgetHostKey(host, ctx.knownHostsPath);
    note(sink, forgotten ? `cleared the recorded host key for ${host}` : `no recorded host key for ${host}`);
  }

  const rc = await runPlaybook({
    runId: opts.runId,
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
 * Stop the container set on a node, and nothing else.
 *
 * ONE SIGNAL OVER PLAIN SSH, no playbook. What sits between ssh and the kill is the whole
 * question on a box that cannot `stat` a file inside its own timeout (#362), and a signal
 * needs nothing of ours installed -- so this works on a card that has never converged, and on
 * one too loaded to run an ansible module.
 *
 * NOT A STICKY OFF. The boot unit's ExecStart is unconditional (#97), so the stack comes back
 * on the next power cycle and nothing has to remember to undo this. To keep a device down
 * across a reboot, disable the unit; that is a deliberate act and not a button.
 *
 * The wait for the containers to actually exit lives in `stopCapture`, shared with post-flight
 * and the playbooks, so there is one definition of what stopping means.
 */
export async function stop(node: FleetNode, ctx: ActionContext, sink?: EventSink): Promise<void> {
  const host = hostOf(node);
  note(sink, `stopping the stack on ${node.name} (${host})`);
  await stopCapture(node, ctx);
  note(sink, `${node.name} stopped; it will come back up on the next boot`);
}

/**
 * Stop the stack, then reboot and wait for the device to answer again.
 *
 * Stopping first is not politeness: it gives the containers OUR timeout rather than whatever
 * the shutdown allows, and on campod-se the camera took 19 seconds to go after the signal.
 *
 * The device ends this RUNNING, not quiesced -- the boot unit starts the stack again. That is
 * the point for the reason this button mostly exists: a capture session is a kernel boot id,
 * so **only a reboot closes one**, and nothing can be packaged off a device until its session
 * has been closed (#302).
 */
export async function reboot(
  node: FleetNode,
  ctx: ActionContext,
  opts: { runId: string },
  sink?: EventSink,
): Promise<void> {
  const host = hostOf(node);
  note(sink, `rebooting ${node.name} (${host})`);
  await stopCapture(node, ctx);
  note(sink, 'stack stopped; rebooting');

  const rc = await runPlaybook({
    runId: opts.runId,
    playbook: 'reboot.yaml',
    host,
    user: ctx.inventory.user,
    privateKeyPath: ctx.privateKeyPath,
    sshTimeoutSec: ctx.sshTimeoutSec,
    knownHostsPath: ctx.knownHostsPath,
    extraVars: {},
    sink,
  });

  if (rc !== 0) {
    throw new ActionError(
      `${node.name}: reboot failed (ansible-runner exit ${rc}). The device may be down or may ` +
        'be taking longer than the play waited; probe it to see whether it came back.',
    );
  }
  note(sink, `${node.name} is back, with the stack started again`);
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
  opts: { runId: string; image: { url: string; sha256: string; sha: string } },
  sink?: EventSink,
): Promise<void> {
  const host = hostOf(node);
  const { image } = opts;
  note(sink, `reimaging ${node.name} (${host}) as ${node.role}`);
  note(sink, `image built from ${image.sha.slice(0, 10)}`);
  note(sink, `device fetches ${image.url}`);

  const rc = await runPlaybook({
    runId: opts.runId,
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
