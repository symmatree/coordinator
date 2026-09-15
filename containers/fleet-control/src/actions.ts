// The two things this service does to a node.
//
//   bootstrap -- a freshly flashed card to a node running its stack
//   update    -- bring an already-set-up node to the merged state of the repo
//
// The bootstrap sequence follows docs/fleet-bringup.md stage 2.

import { hostOf, type FleetNode, type Inventory } from './inventory.js';
import type { LineSink, SessionOptions } from './ssh.js';
import { withSession, rebootAndWait } from './ssh.js';
import { runDetached } from './detached.js';

export class ActionError extends Error {}

export interface ActionContext {
  inventory: Inventory;
  ssh: SessionOptions;
  /** Where `bootstrap` clones from. */
  repoUrl: string;
  checkoutPath: string;
}

function note(sink: LineSink | undefined, msg: string): void {
  sink?.('stdout', `[fleet-control] ${msg}`);
}

/** A short command, run inline. */
async function must(
  node: FleetNode,
  ctx: ActionContext,
  command: string,
  sink: LineSink | undefined,
  what: string,
): Promise<void> {
  const r = await withSession(node, ctx.ssh, (s) => s.exec(command, sink));
  if (r.code !== 0) {
    throw new ActionError(`${node.name}: ${what} failed (exit ${r.code}).\n${r.stderr.trim()}`);
  }
}

/**
 * A long command, run detached on the node and followed from here, so losing this pod does not
 * SIGHUP the work. See src/detached.ts.
 */
async function mustDetached(
  node: FleetNode,
  ctx: ActionContext,
  name: string,
  command: string,
  sink: LineSink | undefined,
  what: string,
): Promise<void> {
  const r = await runDetached(node, ctx.ssh, name, command, sink);
  if (r.code !== 0) {
    throw new ActionError(`${node.name}: ${what} failed (exit ${r.code}). Output above.`);
  }
}

/** Pull the repo and the images, and start the stack. */
export async function update(
  node: FleetNode,
  ctx: ActionContext,
  sink?: LineSink,
): Promise<void> {
  note(sink, `updating ${node.name} (${hostOf(node)})`);
  await must(node, ctx, `git -C ${ctx.checkoutPath} pull --ff-only`, sink, 'git pull');
  await mustDetached(node, ctx, 'coord-pull', 'coord pull -q', sink, 'coord pull');
  await must(node, ctx, 'coord start', sink, 'coord start');
  note(sink, `${node.name} updated`);
}

/**
 * First-time setup for a freshly flashed card.
 *
 * `/usr` ships read-only and has to be remounted before anything can be installed -- including
 * git, which is how `one_time.sh` arrives, so this cannot move into the playbook it starts.
 * The closing reboot is what puts `/usr` back: `remount,ro` is refused on a live system.
 */
export async function bootstrap(
  node: FleetNode,
  ctx: ActionContext,
  sink?: LineSink,
): Promise<void> {
  note(sink, `bootstrapping ${node.name} (${hostOf(node)}) as ${node.role}`);

  // A reflashed card has new host keys, and bootstrap is what you run on a reflashed card.
  if (ctx.ssh.hostKeys.forget(node.name)) {
    note(sink, `cleared the recorded host key for ${node.name}`);
  }

  await must(node, ctx, 'sudo -n mount -o remount,rw /usr', sink, 'remount /usr rw');
  await mustDetached(
    node, ctx, 'apt-git',
    'sudo -n apt-get update && ' +
      'sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git',
    sink, 'apt-get install git',
  );
  await must(node, ctx, `git clone ${ctx.repoUrl} ${ctx.checkoutPath}`, sink, 'git clone');

  const bootId = await withSession(node, ctx.ssh, (s) => s.bootId());
  const r = await runDetached(
    node, ctx.ssh, 'one-time', `cd ${ctx.checkoutPath} && ./host/one_time.sh ${node.role}`, sink,
  );

  if (r.code === 1) {
    // one_time.sh exits 1 to ask for a reboot before being run again.
    note(sink, 'one_time.sh asked for a reboot');
    await rebootAndWait(node, ctx.ssh, bootId, sink);
    const r2 = await runDetached(
      node, ctx.ssh, 'one-time', `cd ${ctx.checkoutPath} && ./host/one_time.sh ${node.role}`, sink,
    );
    if (r2.code !== 0) {
      throw new ActionError(`${node.name}: one_time.sh exited ${r2.code} on its second pass. Output above.`);
    }
  } else if (r.code !== 0) {
    throw new ActionError(`${node.name}: one_time.sh exited ${r.code}. Output above.`);
  }

  // Pull BEFORE the reboot. `one_time.sh` installs a boot unit that runs `coord start` on
  // every boot (#256), which on a card that has never pulled fetches the whole set --
  // unattended, and taking minutes. Pulling afterwards would race it, and `coord pull` runs
  // `compose down` first, so it would tear down what the boot unit was mid-way through
  // starting. Doing it here keeps the fetch attributable to this step.
  await mustDetached(node, ctx, 'coord-pull', 'coord pull -q', sink, 'coord pull');

  // The closing reboot returns /usr to read-only, and now also checks that the stack comes
  // back on its own -- with the images already local it needs no network.
  await rebootAndWait(node, ctx.ssh, await withSession(node, ctx.ssh, (s) => s.bootId()), sink);
  await must(node, ctx, 'coord start', sink, 'coord start');
  note(sink, `${node.name} bootstrapped`);
}
