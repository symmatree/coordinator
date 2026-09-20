/**
 * Stop capture and WAIT for it to actually be gone.
 *
 * Nothing can talk to a capturing campod in a useful time: dockerd measured 2.1-22.0s per
 * call with capture running against 136-223ms without it, and a probe makes five such
 * round-trips. So every command we send a device stops collection first.
 *
 * The wait is the part that is easy to leave out and then lose. `pkill` returns as soon as
 * the signal is sent, not when the processes are gone -- on campod-se that was 1.1s for the
 * accel and 19.0s for the camera, both AFTER the command had returned. Without the loop the
 * very next round-trip races the shutdown it just asked for.
 *
 * Plain POSIX sh in one ssh invocation: no Python on the target, no script to read. Same
 * command as host/ansible/{site,reimage}.yaml send over `raw`.
 *
 * `sudo`, and it is load-bearing. A container's dumb-init is PID 1 in the container and
 * runs as ROOT on the host; we connect as `pi`, who cannot signal it. Without this the
 * signal goes nowhere, the wait below then spins its full 40s because pgrep keeps finding
 * the processes, and the real command runs against a box that is still capturing. The
 * playbooks did not have this bug because the play is `become: true`.
 *
 * Bounded at 40s. If something has not exited by then, carrying on and letting the real
 * command report what it finds beats hanging here.
 *
 * Not a sticky off -- the boot unit's ExecStart is unconditional (#256) -- so a power cycle
 * brings the stack back, and at the bench there is one before flight by construction.
 */
export const QUIESCE = 'sudo pkill -x -TERM dumb-init; i=0; while pgrep -x dumb-init >/dev/null 2>&1 && [ $i -lt 40 ]; do sleep 1; i=$((i+1)); done';

/** `<quiesce>; <cmd>` -- `;` so the status we get back is the real command's. */
export function quiesced(cmd: string): string {
  return `${QUIESCE}; ${cmd}`;
}
