// The two things this service does to a node.
//
// `bootstrap` runs ONCE PER CARD, after flash and first boot. `update` runs EVERY TIME a
// merged change needs to reach a node that is already set up. They are the same SSH-and-run;
// the operator knows which they want, so the service does not guess.
//
// The bootstrap sequence is the one recorded in docs/fleet-bringup.md stage 2, from the
// bring-up that actually happened on 2026-09-12 -- not reconstructed from the per-device docs.
// Where the two disagree, that doc wins, because it is a transcript.

import type { FleetNode } from './inventory.js';
import type { LineSink, SessionOptions } from './ssh.js';
import { withSession, rebootAndWait } from './ssh.js';
import { runDetached } from './detached.js';
import { probeNode } from './probe.js';

const REPO = 'https://github.com/symmatree/coordinator.git';
const CHECKOUT = '$HOME/coordinator';

export class ActionError extends Error {}

function note(sink: LineSink | undefined, msg: string): void {
  sink?.('stdout', `[fleet-control] ${msg}`);
}

/** A short command, run inline. Only for things that finish in seconds. */
async function must(
  node: FleetNode,
  opts: SessionOptions,
  command: string,
  sink: LineSink | undefined,
  what: string,
): Promise<void> {
  const r = await withSession(node, opts, (s) => s.exec(command, sink));
  if (r.code !== 0) {
    throw new ActionError(
      `${node.name}: ${what} failed (exit ${r.code}). Command: ${command}\n${r.stderr.trim()}`,
    );
  }
}

/**
 * A long command, run DETACHED on the node and followed from here.
 *
 * Everything measured in minutes goes through this. The work belongs to the node, not to our
 * SSH channel, so losing this pod mid-run cannot SIGHUP an apt transaction or an Ansible play
 * -- and a replacement pod re-attaches instead of starting a second copy. See src/detached.ts.
 */
async function mustDetached(
  node: FleetNode,
  opts: SessionOptions,
  name: string,
  command: string,
  sink: LineSink | undefined,
  what: string,
): Promise<void> {
  const r = await runDetached(node, opts, name, command, sink);
  if (r.code !== 0) {
    throw new ActionError(`${node.name}: ${what} failed (exit ${r.code}). See the output above.`);
  }
}

/**
 * Every time. Bring an already-set-up node to the merged state of the repo.
 *
 * Fails loudly if the node was never bootstrapped rather than letting `coord` report *no
 * stack*, which sends you looking in the wrong place: the real cause is a missing checkout,
 * because /opt/stacks/<role> is a symlink into it (#48).
 */
export async function update(
  node: FleetNode,
  opts: SessionOptions,
  sink?: LineSink,
): Promise<void> {
  const before = await probeNode(node, opts);
  if (before.stage === 'unreachable') {
    throw new ActionError(before.error ?? `${node.name} unreachable`);
  }
  if (!before.checkoutPresent) {
    throw new ActionError(
      `${node.name}: no checkout at ~/coordinator, so there is nothing to update and no stack ` +
        `to start. This node has not been bootstrapped.`,
    );
  }
  if (before.checkoutDirty) {
    // Config is git-authoritative with no on-box override (docs/deployment-model.md), so a
    // dirty tree is either a hand-edit that belongs in git or something unexpected. Either
    // way it is the operator's call, not ours to clobber.
    throw new ActionError(
      `${node.name}: the checkout has uncommitted changes. Config here is git-authoritative ` +
        `with no on-box override, so resolve this on the device before updating.`,
    );
  }

  note(sink, `updating ${node.name} (at ${before.checkoutHead ?? 'unknown'})`);
  await must(node, opts, `git -C ${CHECKOUT} pull --ff-only`, sink, 'git pull');
  // `coord pull` runs `compose down` first, so this is a full stop of the stack rather than a
  // rolling update. Measured at 4m22s for campod-camera on one node over lab WiFi (235 MB
  // compressed); four campods share one 2.4 GHz radio, so expect worse across the fleet.
  // Detached: minutes long, and half-pulled images with the stack down is not a state to
  // leave a vehicle in because a pod was rescheduled.
  await mustDetached(node, opts, 'coord-pull', 'coord pull', sink, 'coord pull');
  await must(node, opts, 'coord start', sink, 'coord start');

  const after = await probeNode(node, opts);
  note(sink, `${node.name}: now at ${after.checkoutHead ?? 'unknown'}, ${after.containers?.length ?? 0} container(s) up`);
  if ((after.containers?.length ?? 0) === 0) {
    // Not thrown -- the commands succeeded. But `coord start` starting nothing is a real and
    // recently-live failure mode (#240), and its only signal is printing no services.
    note(sink, `WARNING: no containers are running after 'coord start'.`);
  }
}

/**
 * Once per card. Flashed-and-booted to a node running its stack.
 *
 * The sequence is docs/fleet-bringup.md stage 2. The two non-obvious steps:
 *
 *  - `/usr` ships READ-ONLY, so installing anything needs the remount first. This is not
 *    git-specific and not a defect -- it is the ordering consequence of a device bootstrapping
 *    itself, and a driver coming in from outside (this service) just performs it as a step.
 *  - The run ends with a REBOOT. `remount,ro` is refused on a live system (`mount point is
 *    busy`, exit 32), so a reboot is the only thing that returns `/usr` to the read-only
 *    invariant the image shipped with. It doubles as the test that the stack comes back on
 *    its own.
 */
export async function bootstrap(
  node: FleetNode,
  opts: SessionOptions,
  sink?: LineSink,
): Promise<void> {
  const before = await probeNode(node, opts);
  if (before.stage === 'unreachable') {
    throw new ActionError(before.error ?? `${node.name} unreachable`);
  }
  note(sink, `bootstrapping ${node.name} as role '${node.role}' (currently ${before.stage})`);

  if (before.fleetImage?.ROLE && before.fleetImage.ROLE !== node.role) {
    throw new ActionError(
      `${node.name}: inventory says role '${node.role}' but the card was flashed as ` +
        `'${before.fleetImage.ROLE}' (/etc/fleet-image). Bootstrapping with the wrong role lays ` +
        `down the wrong stack and the wrong data mount.`,
    );
  }

  note(sink, 'remounting /usr rw (it ships read-only; every package install needs this)');
  await must(node, opts, 'sudo -n mount -o remount,rw /usr', sink, 'remount /usr rw');

  if (!before.checkoutPresent) {
    // Detached: an apt transaction interrupted by a lost pod is exactly the half-applied state
    // that no amount of "the script is re-runnable" fixes.
    await mustDetached(
      node, opts, 'apt-git',
      'sudo -n apt-get update && ' +
        'sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git',
      sink, 'apt-get install git',
    );
    await must(node, opts, `git clone ${REPO} ${CHECKOUT}`, sink, 'git clone');
  } else {
    note(sink, `checkout already present at ${before.checkoutHead ?? 'unknown'} -- pulling`);
    await must(node, opts, `git -C ${CHECKOUT} pull --ff-only`, sink, 'git pull');
  }

  // ~13 minutes on a Zero 2 W (measured 12m38s on campod-se), most of it the Docker install.
  // Detached, for the whole reason src/detached.ts exists: this is the longest single thing
  // the service does and the one whose interruption is worst.
  note(sink, 'running one_time.sh detached (about 13 minutes on a Zero 2 W)');
  const bootId = await withSession(node, opts, (s) => s.bootId());
  const r = await runDetached(
    node, opts, 'one-time', `cd ${CHECKOUT} && ./host/one_time.sh ${node.role}`, sink,
  );

  if (r.code === 1) {
    // Exit 1 means "I opened the /usr hatch, reboot me and run me again". In the sequence
    // above it should not happen, because step 1 already left /usr writable -- the one
    // observed instance was a deliberate test artifact (#260). Handled because the script
    // documents it, not because it is expected.
    note(sink, 'one_time.sh exited 1 (asked for a reboot) -- rebooting and running it once more');
    await rebootAndWait(node, opts, bootId, sink);
    const second = await withSession(node, opts, (s) => s.bootId());
    const r2 = await runDetached(
      node, opts, 'one-time', `cd ${CHECKOUT} && ./host/one_time.sh ${node.role}`, sink,
    );
    if (r2.code !== 0) {
      throw new ActionError(
        `${node.name}: one_time.sh exited ${r2.code} on its second pass. Its output is above ` +
          `(detached runs merge stderr into the streamed log).`,
      );
    }
    void second;
  } else if (r.code !== 0) {
    throw new ActionError(
      `${node.name}: one_time.sh exited ${r.code}. Its output is above (detached runs merge ` +
        `stderr into the streamed log).`,
    );
  }
  note(sink, 'one_time.sh complete');

  // Reboot before pulling images: it closes the /usr hatch, and it means `coord pull` runs
  // against the device in the state it is actually meant to be in.
  const beforeFinalBoot = await withSession(node, opts, (s) => s.bootId());
  await rebootAndWait(node, opts, beforeFinalBoot, sink);

  await mustDetached(node, opts, 'coord-pull', 'coord pull', sink, 'coord pull');
  await must(node, opts, 'coord start', sink, 'coord start');

  const after = await probeNode(node, opts);
  note(sink, `${node.name}: stage '${after.stage}', ${after.containers?.length ?? 0} container(s) up`);
  // Reachable is not working. Nothing downstream of a camera being present is proven --
  // capture, the exposure cap, the focus control and the accel path are all untested
  // (docs/fleet-bringup.md stage 2).
  note(sink, 'NOTE: containers running means deployed, NOT that capture works.');
}
