// What this service does to a node: converge it, stop it, reboot it.
//
// Converge is the one that changes a device. THE OTHER TWO ARE NOT SMALLER CONVERGES --
// they are the things a converge does to the device on its way past, offered on their own
// because the operator wants them on their own: a stack that is off while you read the card,
// a reboot that closes a capture session.
//
// Converge: ONE action, not two. `bootstrap` and `update` used to differ because a fresh card needed a
// remount, an apt install, a clone, and an exit-1 reboot dance that an already-provisioned
// node did not. #263 deletes all of that: the same playbook handles a virgin unit and a
// running one, and presenting two buttons that run identical commands would be a lie about
// what the service does.
//
// NOTHING HERE IS A RUN. A converge is submitted and the workflow's name comes back; stop and
// reboot do their one thing and return. The service used to wrap all of these in an
// in-memory run with a log and a status (coordinator#566), which a pod roll lost -- and the
// workflow it shadowed did not need shadowing. What a workflow did is read from the cluster.
//
// What the playbook does is documented with the playbook -- `host/ansible/` and
// docs/deployment-model.md. Here, a submitted workflow is a submitted workflow; the device's
// own state comes off the pod bus, not from anything this file believes about the play.

import { hostOf, type FleetNode, type Inventory } from './inventory.js';
import { configured as busConfigured, requestReboot, type BusConfig } from './bus.js';
import { SSH_NO_HOST_KEY_ARGS } from './ssh.js';
import { latestPlaybook } from './playbookartifact.js';
import { submitPlaybook, workflowUrl, type WorkflowConfig } from './workflows.js';
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

/**
 * Two operations, not one at two sizes.
 *
 * `converge` runs `deploy.yaml` and `provision` runs `site.yaml`. What each of those does is
 * documented with them -- `host/ansible/` and docs/deployment-model.md. The difference that is
 * this service's business: converge is the routine one and the default, provision is for a card
 * that has just been written.
 */
export type ConvergeKind = 'converge' | 'provision';

const PLAYBOOK: Record<ConvergeKind, string> = {
  converge: 'deploy.yaml',
  provision: 'site.yaml',
};

/** What a submit hands back: the handle, and what it was told to run. */
export interface Submitted {
  workflow: string;
  url: string;
  playbook: string;
  /** The commit the playbook archive was built from. */
  revision: string;
}

/**
 * Submit one playbook run against a node. Returns once the cluster has accepted it.
 *
 * SUBMIT IS THE WHOLE ACTION. Once the cluster has the Workflow the work happens whether
 * this process is still here or not, so there is nothing for this side to hold. The name
 * is the handle: `GET /workflows/:name` for the phase, `/workflows/:name/log` for the play's
 * own output, and the Argo UI for everything else.
 *
 * Which playbook archive runs is the newest published on the tracked ref, not this
 * service's revision -- a playbook merge does not move ours.
 */
export async function converge(
  node: FleetNode,
  ctx: ActionContext,
  kind: ConvergeKind,
): Promise<Submitted> {
  const playbook = PLAYBOOK[kind];
  const artifact = await latestPlaybook(ctx.github);
  const workflow = await submitPlaybook(ctx.workflows, {
    node: node.name,
    action: kind,
    playbook,
    host: hostOf(node),
    deviceRole: node.role,
    artifactUrl: artifact.url,
    revision: artifact.revision,
  });
  const url = workflowUrl(ctx.workflows, workflow);
  // The pod's own log is where a submit is recorded; the workflow is the record of the run.
  process.stdout.write(
    `[fleet-control] ${kind} ${node.name}: ${playbook} from ${artifact.revision.slice(0, 10)} -> ${url}\n`,
  );
  return { workflow, url, playbook, revision: artifact.revision };
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
export async function stop(node: FleetNode, ctx: ActionContext): Promise<void> {
  await stopCapture(node, ctx);
}

/** How a reboot was requested, and the one thing worth saying about it. */
export interface Rebooted {
  via: 'bus' | 'ssh';
  note: string;
}

/**
 * Reboot a device. One command, gated on nothing.
 *
 * OVER THE BUS WHEN THERE IS ONE. pod-link is already connected and subscribed to
 * `rekon/pod/<node>/reboot`, so the request costs a publish; the ssh path below costs an
 * association, a TCP connection, an sshd fork, sudo and systemctl on a 512 MB board that
 * has usually just finished writing a capture. It is also the path that fails when ssh
 * fails: on 2026-10-18 campod-sw's ssh went dead 39 s after disarm, the reboot at the end
 * of the run "gave up after 60s", and the box stayed up with a card that had not been
 * cleanly flushed -- while it was publishing on the bus the whole time.
 *
 * NO STOP FIRST. A reboot is the way out of a stuck box -- the thing that was being done by
 * hand over ssh, or by pulling power -- so it must not depend on anything else working. The
 * containers get systemd's shutdown signal on the way down; if you want them stopped on our
 * timeout instead, that is the Stop button, pressed first, deliberately.
 *
 * NOTHING WAITS for it to come back, the same as everything else here (#326): the device
 * answers again or it does not, and the status screen is the check. That matters more over
 * the bus than over ssh, because a publish succeeds whether or not anything acts on it --
 * the caller's check is the node's uptime going backwards, not this function returning.
 *
 * ssh remains the fallback, for a box with no bus configured and for a pod-link that is not
 * running. `systemctl --no-block` queues the job and returns rather than blocking on the
 * transition, so ssh gets a real exit status instead of racing sshd's own shutdown. That
 * race is not fully closable from here, so a connection that drops is read as the reboot
 * starting.
 */
export async function reboot(node: FleetNode, ctx: ActionContext): Promise<Rebooted> {
  const host = hostOf(node);
  let busFailed: string | undefined;

  if (busConfigured(ctx.bus)) {
    try {
      await requestReboot(ctx.bus, node.name, `fleet-control: reboot ${node.name}`);
      return { via: 'bus', note: 'requested over the pod bus; uptime going backwards is the confirmation' };
    } catch (err) {
      // Fall through to ssh: the broker lives on the coordinator, so a coordinator that is
      // already down or rebooting takes the bus with it, and that is exactly when a reboot
      // of something else is being asked for.
      busFailed = (err as Error).message;
    }
  }

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
    return {
      via: 'ssh',
      note: `${busFailed === undefined ? '' : `bus reboot failed (${busFailed}); `}` +
        'connection dropped, which is what a reboot looks like from here',
    };
  }

  return {
    via: 'ssh',
    note: `${busFailed === undefined ? '' : `bus reboot failed (${busFailed}); `}` +
      `${node.name} is going down; refresh status to see it come back`,
  };
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
