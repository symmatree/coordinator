// Host-key trust is OpenSSH's: ansible shells out to the ssh binary, and both it and this
// module are pointed at one explicit known_hosts file. Explicit rather than the default,
// because the default is derived from the account's home directory and is therefore neither
// predictable in a container nor persistent across a pod restart.

import { execFile } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { promisify } from 'node:util';

const run = promisify(execFile);

/**
 * `ssh-keygen -R <host> -f <file>`. Returns whether an entry was actually removed.
 *
 * A missing file is NOT an error: nothing is recorded, so there is nothing to forget.
 * `ssh-keygen` exits non-zero for it, which failed every converge from a freshly started pod,
 * because the file only exists once ssh has connected at least once.
 */
export async function forgetHostKey(host: string, knownHostsPath: string): Promise<boolean> {
  try {
    mkdirSync(dirname(knownHostsPath), { recursive: true });
  } catch {
    // If the directory cannot be made, ssh-keygen will say so more usefully than we can.
  }
  try {
    const { stdout, stderr } = await run('ssh-keygen', ['-R', host, '-f', knownHostsPath]);
    // "not found in" is ssh-keygen's way of saying the host was not recorded.
    return !`${stdout}${stderr}`.includes('not found in');
  } catch (err) {
    const text = String((err as { stderr?: string }).stderr ?? (err as Error).message);
    if (/No such file or directory|Cannot stat/i.test(text)) return false;
    throw new Error(`could not clear the host key for ${host}: ${text.trim()}`);
  }
}
