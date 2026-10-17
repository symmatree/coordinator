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
// What the playbook does is documented with the playbook -- `host/ansible/` and
// docs/deployment-model.md. Here, exit 0 means converged; the device's own state comes off the
// pod bus, not from anything this file believes about the play.

import { hostOf, type FleetNode, type Inventory } from './inventory.js';
import type { BusConfig } from './bus.js';
import type { EventSink } from './runs.js';
import { SSH_NO_HOST_KEY_ARGS } from './ssh.js';
import { latestPlaybook } from './playbookartifact.js';
import { follow, submitPlaybook, workflowUrl, type WorkflowConfig } from './workflows.js';
import type { GithubOptions } from './github.js';
import { stopCapture } from './sessions.js';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);

export class ActionError extends Error {}

export interface ActionContext {
  inventory: Inventory;
  privateKeyPath: string;
  sshTimeoutSec: number;
  /** Where playbook workflows are submitted. */
  workflows: WorkflowConfig;
  /**
   * Which published playbook to run. Listing builds and artifacts is public; the token is
   * only needed by the step that downloads the zip.
   */
  github: GithubOptions;
  /**
   * The pod bus, because quiescing a device goes over it rather than over ssh.
   *
   * Here rather than passed per call: every function that touches a device already takes an
   * ActionContext, and the quiesce is a property of reaching a device at all.
   */
  bus: BusConfig;
}

function note(sink: EventSink | undefined, msg: string): void {
  sink?.('stdout', `[fleet-control] ${msg}`);
}

/**
 * Two operations, not one at two sizes.
 *
 * `converge` runs `deploy.yaml` and `provision` runs `site.yaml`. What each of those does is
 * documented with them -- `host/ansible/` and docs/deployment-model.md. The difference that is
 * this service's business: converge is the routine one and the default, provision is for a card
 * that has just been written.
 *
 * Which playbook runs used to be neither, because this service ran `site.yaml` for everything
 * after the playbook split in two (#453) -- so a routine converge paid the fresh-card path.
 */
export type ConvergeKind = 'converge' | 'provision';

const PLAYBOOK: Record<ConvergeKind, string> = {
  converge: 'deploy.yaml',
  provision: 'site.yaml',
};


/**
 * Submit one playbook run and watch it to its end.
 *
 * SUBMIT AND WATCH ARE DIFFERENT THINGS. Once the cluster has accepted the Workflow the
 * work is happening whether this process is still here or not, which is the point. What
 * follows is a watcher: it streams the step's log and waits for a phase. Losing it loses
 * the live view, and `findByRun` in workflows.ts is how a later process gets it back.
 */
async function runPlaybookWorkflow(
  node: FleetNode,
  ctx: ActionContext,
  opts: {
    runId: string;
    action: string;
    playbook: string;
    extraVars?: Record<string, string>;
    sink?: EventSink;
  },
): Promise<void> {
  const host = hostOf(node);
  const artifact = await latestPlaybook(ctx.github);
  note(
    opts.sink,
    `${opts.playbook} from ${artifact.revision.slice(0, 10)}, built ${artifact.createdAt}`,
  );

  const name = await submitPlaybook(ctx.workflows, {
    runId: opts.runId,
    node: node.name,
    action: opts.action,
    playbook: opts.playbook,
    host,
    deviceRole: node.role,
    artifactUrl: artifact.url,
    revision: artifact.revision,
    extraVars: opts.extraVars,
  });
  // A LINK, SAID BEFORE ANYTHING IS WAITED ON. The workflow outlives this process, so a run
  // whose watcher died still needs a handle -- and the UI is the thing that knows how to
  // show a workflow and its logs, which is not a thing this service should be explaining.
  const link = workflowUrl(ctx.workflows, name);
  note(opts.sink, `workflow ${link}`);

  const state = await follow(ctx.workflows, name, (stream, line) => opts.sink?.(stream, line));
  if (state.phase !== 'Succeeded') {
    throw new ActionError(
      `${node.name}: ${opts.action} ${state.phase ?? 'ended with no phase'}` +
        `${state.message !== undefined ? ` -- ${state.message}` : ''}. ${link}`,
    );
  }
}

export async function converge(
  node: FleetNode,
  ctx: ActionContext,
  opts: { runId: string; kind: ConvergeKind },
  sink?: EventSink,
): Promise<void> {
  const playbook = PLAYBOOK[opts.kind];
  note(sink, `${opts.kind} on ${node.name} (${hostOf(node)}) as ${node.role} -- ${playbook}`);
  await runPlaybookWorkflow(node, ctx, {
    runId: opts.runId,
    action: opts.kind,
    playbook,
    sink,
  });
  note(sink, `${node.name} ${opts.kind === 'converge' ? 'converged' : 'provisioned'}`);
}

/**
 * Stop the container set on a node, and nothing else.
 *
 * ONE MESSAGE ON THE BUS, no playbook and no ssh. What sits between us and the stop is the
 * whole question on a box that cannot `stat` a file inside its own timeout (#362): pod-link
 * is already resident and already connected, so stopping costs a publish rather than an sshd
 * fork and a shell faulted in off a card that is the thing being contended for.
 *
 * WHAT THIS GIVES UP. The ssh signal needed nothing of ours installed, so it reached a card
 * that had never converged. This needs pod-link running. A device that has not been
 * provisioned, or whose pod-link is down, cannot be stopped from here at all -- the trade is
 * deliberate (it is the box in trouble that could least afford the old path), and ssh by hand
 * is the way through.
 *
 * NOT A STICKY OFF. The units are enabled at boot, so the stack comes back on the next power
 * cycle and nothing has to remember to undo this. To keep a device down across a reboot,
 * disable the unit; that is a deliberate act and not a button.
 *
 * The wait for the containers to actually exit is the device's: pod-link polls systemd until
 * each unit is inactive before it answers, so there is one definition of what stopping means
 * and it is held where the units are.
 */
export async function stop(node: FleetNode, ctx: ActionContext, sink?: EventSink): Promise<void> {
  const host = hostOf(node);
  note(sink, `stopping the stack on ${node.name} (${host})`);
  await stopCapture(node, ctx);
  note(sink, `${node.name} stopped; it will come back up on the next boot`);
}

/**
 * Reboot a device. One command, gated on nothing.
 *
 * NO STOP FIRST. A reboot is the way out of a stuck box -- the thing that was being done by
 * hand over ssh, or by pulling power -- so it must not depend on anything else working. The
 * containers get systemd's shutdown signal on the way down; if you want them stopped on our
 * timeout instead, that is the Stop button, pressed first, deliberately.
 *
 * NOTHING WAITS for it to come back, the same as everything else here (#326): the device
 * answers again or it does not, and the status screen is the check. In the normal case the
 * run ends and the operator pulls the plug -- there is nothing after this in the UI.
 *
 * `systemctl --no-block` queues the job and returns rather than blocking on the transition,
 * so ssh gets a real exit status instead of racing sshd's own shutdown. That race is not
 * fully closable from here, so a connection that drops is read as the reboot starting.
 */
export async function reboot(node: FleetNode, ctx: ActionContext, sink?: EventSink): Promise<void> {
  const host = hostOf(node);
  note(sink, `rebooting ${node.name} (${host})`);

  try {
    await run(
      'ssh',
      [
        '-i', ctx.privateKeyPath,
        '-o', 'BatchMode=yes',
        ...SSH_NO_HOST_KEY_ARGS,
        '-o', `ConnectTimeout=${ctx.sshTimeoutSec}`,
        `${ctx.inventory.user}@${host}`,
        'sudo systemctl --no-block reboot',
      ],
      { maxBuffer: 1024 * 1024, timeout: 60_000 },
    );
  } catch (err) {
    const e = err as { stderr?: string; message?: string; killed?: boolean };
    if (e.killed === true) throw new ActionError(`${node.name}: reboot request gave up after 60s`);
    const why = (e.stderr ?? '').trim() || e.message || 'ssh failed';
    if (!rebootStarted(why)) {
      throw new ActionError(`${node.name}: ${why.split('\n').slice(-2).join(' ').slice(0, 300)}`);
    }
    note(sink, 'connection dropped, which is what a reboot looks like from here');
  }

  note(sink, `${node.name} is going down; refresh status to see it come back`);
}

/**
 * Is this ssh failure the device going down, rather than a device we never reached?
 *
 * sshd is killed a moment after the request is accepted, so the exit status can be lost in
 * transit even though the command ran. These are the shapes that means, and they are
 * distinguishable from `Connection refused` / `Connection timed out` / a key rejection, which
 * all mean we never got a command in at all.
 */
export function rebootStarted(stderr: string): boolean {
  return /closed by remote host|Connection reset|Broken pipe/i.test(stderr);
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

  await runPlaybookWorkflow(node, ctx, {
    runId: opts.runId,
    action: 'reimage',
    playbook: 'reimage.yaml',
    // The play asserts both of these before it quiesces anything, so a missing one fails
    // there rather than part-way through a reflash.
    extraVars: { reimage_image_url: image.url, reimage_image_sha256: image.sha256 },
    sink,
  });
  note(sink, `${node.name} armed; it will drop off the network and come back on the new image`);
}
