// Host-key trust is now OpenSSH's, because Ansible shells out to the ssh binary and reads
// ~/.ssh/known_hosts. This service keeps no store of its own; the only thing it needs is the
// one operation ssh has no automatic answer for -- forgetting a key when a card is reflashed.

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);

/** `ssh-keygen -R <host>`. Returns whether an entry was actually removed. */
export async function forgetHostKey(host: string): Promise<boolean> {
  try {
    const { stdout, stderr } = await run('ssh-keygen', ['-R', host]);
    // ssh-keygen says "not found in" when there was nothing to remove.
    return !`${stdout}${stderr}`.includes('not found in');
  } catch (err) {
    throw new Error(`could not clear the host key for ${host}: ${(err as Error).message}`);
  }
}
