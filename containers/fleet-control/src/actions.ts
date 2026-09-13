// The two things this service does to a node.
//
// `update` is the repeatable path: `git pull && coord pull && coord start`. No sudo, no apt,
// no Ansible. This is the one that currently needs a keyboard, SSH keys and a remembered
// command, and it is most of the day-to-day value (coordinator#223).
//
// `bootstrap` is the first-time path: clone, then `one_time.sh <role>` until it completes.
// It is slower and heavier -- on a 512 MB Zero 2 W the Ansible install alone is minutes.
//
// PROVENANCE: the bootstrap sequence below is written from docs/host-setup.md and
// docs/campod.md. The authoritative sequence is the one that comes out of the first hand-run
// bringup; when that produces a happy-path script, this should call it rather than restate
// it, so there is one source of truth instead of two that can drift.

import type { FleetNode } from './inventory.js';
import type { LineSink, SessionOptions } from './ssh.js';
import { withSession, rebootAndWait } from './ssh.js';
import { probeNode } from './probe.js';

const REPO = 'https://github.com/symmatree/coordinator.git';
const CHECKOUT = '$HOME/coordinator';

/** How many one_time.sh -> reboot cycles before we call it a loop rather than progress. */
const MAX_BOOTSTRAP_PASSES = 5;

export class ActionError extends Error {}

function note(sink: LineSink | undefined, msg: string): void {
  sink?.('stdout', `[fleet-control] ${msg}`);
}

/** Run a command, streaming output, and throw with context if it fails. */
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
 * The repeatable path. Fails loudly if the node was never bootstrapped rather than producing
 * `coord`'s confusing "no stack" message, which sends you looking in the wrong place: the
 * real cause is a missing checkout, because /opt/stacks/<role> is a symlink into it
 * (coordinator#48).
 */
export async function update(
  node: FleetNode,
  opts: SessionOptions,
  sink?: LineSink,
): Promise<void> {
  const before = await probeNode(node, opts);
  if (before.stage === 'unreachable') throw new ActionError(before.error ?? `${node.name} unreachable`);
  if (!before.checkoutPresent) {
    throw new ActionError(
      `${node.name}: no checkout at ~/coordinator, so there is nothing to update and no stack ` +
        `to start -- /opt/stacks/${node.role} is a symlink into it. Run bootstrap first.`,
    );
  }

  note(sink, `updating ${node.name} (was at ${before.checkoutHead ?? 'unknown'})`);
  if (before.checkoutDirty) {
    // Config is git-authoritative with no on-box override (docs/deployment-model.md). A dirty
    // tree means someone hand-edited the device, which `git pull` may now refuse or silently
    // clobber. Say so rather than deciding for them.
    throw new ActionError(
      `${node.name}: the checkout has uncommitted changes. Config on these devices is ` +
        `git-authoritative and there is no on-box override, so this is either a hand-edit that ` +
        `needs to go into git, or something unexpected. Resolve it on the device before updating.`,
    );
  }

  await must(node, opts, `git -C ${CHECKOUT} pull --ff-only`, sink, 'git pull');
  // `coord pull` runs `compose down` first -- a full stop of the stack, not a rolling update.
  await must(node, opts, 'coord pull', sink, 'coord pull');
  await must(node, opts, 'coord start', sink, 'coord start');

  const after = await probeNode(node, opts);
  note(sink, `${node.name} is now ${after.stage} at ${after.checkoutHead ?? 'unknown'}`);
  if (after.stage !== 'running') {
    // Not thrown: the commands succeeded. But `coord start` starting nothing is a real and
    // recently-live failure mode (coordinator#240 -- a campod stack whose only service carried
    // an inactive compose profile started nothing and said so only by printing no services).
    note(sink, `WARNING: ${node.name} is '${after.stage}', expected 'running' -- no containers are up.`);
  }
}

/**
 * The first-time path: blank card to a node running its stack.
 *
 * `one_time.sh` exits 1 when a reboot is pending. THAT IS A NORMAL PATH, NOT A FAILURE --
 * treating it as an error makes a working bootstrap look broken. So this loops: run, and if
 * it asks for a reboot, reboot and run again, until it completes.
 */
export async function bootstrap(
  node: FleetNode,
  opts: SessionOptions,
  sink?: LineSink,
): Promise<void> {
  const before = await probeNode(node, opts);
  if (before.stage === 'unreachable') throw new ActionError(before.error ?? `${node.name} unreachable`);
  note(sink, `bootstrapping ${node.name} (role ${node.role}, currently ${before.stage})`);

  if (before.fleetImage?.ROLE && before.fleetImage.ROLE !== node.role) {
    throw new ActionError(
      `${node.name}: inventory says role '${node.role}' but the card was flashed as ` +
        `'${before.fleetImage.ROLE}' (/etc/fleet-image). One of the two is wrong; bootstrapping ` +
        `with the wrong role lays down the wrong stack and the wrong data mount.`,
    );
  }

  if (!before.checkoutPresent) {
    note(sink, 'no checkout -- installing git and cloning');
    await must(node, opts, 'sudo -n apt-get update', sink, 'apt-get update');
    await must(
      node, opts,
      'sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git',
      sink, 'apt-get install git',
    );
    await must(node, opts, `git clone ${REPO} ${CHECKOUT}`, sink, 'git clone');
  } else {
    note(sink, `checkout present at ${before.checkoutHead ?? 'unknown'} -- pulling before bootstrap`);
    await must(node, opts, `git -C ${CHECKOUT} pull --ff-only`, sink, 'git pull');
  }

  for (let pass = 1; pass <= MAX_BOOTSTRAP_PASSES; pass++) {
    note(sink, `one_time.sh pass ${pass}/${MAX_BOOTSTRAP_PASSES}`);
    const bootId = await withSession(node, opts, (s) => s.bootId());
    const r = await withSession(node, opts, (s) =>
      s.exec(`cd ${CHECKOUT} && ./host/one_time.sh ${node.role}`, sink),
    );

    if (r.code === 0) {
      note(sink, `one_time.sh completed on pass ${pass}`);
      // A NEW connection, deliberately. Ansible adds `pi` to the docker group, and group
      // membership only lands in a new login session -- this is the non-interactive
      // equivalent of the `newgrp docker` step in docs/host-setup.md. Reusing the session
      // here makes every subsequent `coord` call fail on docker permissions.
      const after = await probeNode(node, opts);
      if (!after.inDockerGroup) {
        throw new ActionError(
          `${node.name}: bootstrap finished but ${node.user} still cannot talk to docker on a ` +
            `fresh login. Expected the docker group to be in effect by now.`,
        );
      }
      note(sink, 'docker group is in effect on a fresh session; starting the stack');
      await must(node, opts, 'coord pull', sink, 'coord pull');
      await must(node, opts, 'coord start', sink, 'coord start');
      const final = await probeNode(node, opts);
      note(sink, `${node.name} is now '${final.stage}'`);
      if (final.stage !== 'running') {
        note(sink, `WARNING: expected 'running', got '${final.stage}' -- no containers are up.`);
      }
      // Reachable is not working: nothing in the campod capture path has ever run on real
      // hardware, so a running container is not evidence that frames are landing.
      note(sink, 'NOTE: containers running means deployed, not that capture works -- verify separately.');
      return;
    }

    if (r.code === 1) {
      const probe = await probeNode(node, opts);
      if (probe.rebootRequired) {
        note(sink, 'one_time.sh asked for a reboot (exit 1 + reboot-required) -- this is normal');
        await rebootAndWait(node, opts, bootId, sink);
        continue;
      }
    }

    throw new ActionError(
      `${node.name}: one_time.sh exited ${r.code} on pass ${pass} without a pending reboot, so ` +
        `this is a real failure rather than the normal reboot path.\n${r.stderr.trim()}`,
    );
  }

  throw new ActionError(
    `${node.name}: one_time.sh still wanted a reboot after ${MAX_BOOTSTRAP_PASSES} passes. ` +
      `It is meant to converge; something is reinstalling a kernel/firmware change every pass.`,
  );
}
