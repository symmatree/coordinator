// Read the SSH private key and check ssh2 can actually use it.
//
// Done at startup because nothing else touches the key until an action runs: without this a
// pod mounts an unusable secret, reports Ready, and fails on the operator's first press.

import { readFileSync } from 'node:fs';
import ssh2 from 'ssh2';

export function loadPrivateKey(path: string): string {
  let raw: string;
  try {
    raw = readFileSync(path, 'utf8');
  } catch (err) {
    throw new Error(`cannot read the private key at ${path}: ${(err as Error).message}`);
  }
  const parsed = ssh2.utils.parseKey(raw);
  if (parsed instanceof Error) {
    throw new Error(`the private key at ${path} is not one ssh2 can use: ${parsed.message}`);
  }
  return raw;
}
