// Host-key trust: record a node's key the first time we see it, and verify it after.
//
// Strict known-hosts checking is the wrong default HERE, and blanket-disabling it is worse.
// Reflashing a card generates new host keys, and reflash is a normal, expected step in this
// fleet's lifecycle (coordinator#236: a node is provisioned from a blank card). So the key
// for a host we have seen before WILL legitimately change. Strict checking turns every
// reflash into a hard failure; `ignore all keys` throws the protection away permanently.
//
// Trust-on-first-use keeps the useful half: the first contact is unverified (as it is with
// any first ssh), every later contact is verified against what we recorded, and a change is
// reported rather than silently accepted. Clearing a node's record is then an explicit
// operation tied to reflashing it -- `forget(name)` / `DELETE /nodes/:name/hostkey`.
//
// PERSISTENCE IS THE DEPLOYMENT'S JOB. Point `storePath` at a volume that survives a pod
// restart. If it does not survive, every restart is a fresh first-contact and the check
// degrades to no protection at all -- so `FleetHostKeys.ephemeral` is reported on the status
// route rather than left for someone to discover.

import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, renameSync } from 'node:fs';
import { dirname } from 'node:path';

/** OpenSSH-style fingerprint of a raw public-key blob: `SHA256:` + unpadded base64. */
export function fingerprint(key: Buffer): string {
  return 'SHA256:' + createHash('sha256').update(key).digest('base64').replace(/=+$/, '');
}

export type VerifyOutcome =
  | { ok: true; state: 'first-contact' | 'match'; fingerprint: string }
  | { ok: false; state: 'mismatch'; fingerprint: string; expected: string };

interface StoreFile {
  /** node name -> fingerprint */
  keys: Record<string, string>;
}

export class HostKeyStore {
  private keys = new Map<string, string>();
  /** True when the store could not be read AND cannot be written -- no trust is persisted. */
  readonly ephemeral: boolean;

  constructor(private readonly storePath: string) {
    try {
      const parsed = JSON.parse(readFileSync(storePath, 'utf8')) as StoreFile;
      for (const [k, v] of Object.entries(parsed.keys ?? {})) this.keys.set(k, v);
      this.ephemeral = false;
    } catch {
      // No store yet is normal on a first run; an unwritable path is not. Distinguish by
      // trying a write now rather than discovering it on the first successful connection.
      try {
        mkdirSync(dirname(storePath), { recursive: true });
        this.flush();
        this.ephemeral = false;
      } catch {
        this.ephemeral = true;
      }
    }
  }

  get(name: string): string | undefined {
    return this.keys.get(name);
  }

  /** Check a presented key against what we hold, recording it on first contact. */
  verify(name: string, key: Buffer): VerifyOutcome {
    const fp = fingerprint(key);
    const known = this.keys.get(name);
    if (known === undefined) {
      this.keys.set(name, fp);
      this.flush();
      return { ok: true, state: 'first-contact', fingerprint: fp };
    }
    if (known === fp) return { ok: true, state: 'match', fingerprint: fp };
    return { ok: false, state: 'mismatch', fingerprint: fp, expected: known };
  }

  /** Drop a node's recorded key. The operation to run when you reflash that card. */
  forget(name: string): boolean {
    const had = this.keys.delete(name);
    if (had) this.flush();
    return had;
  }

  all(): Record<string, string> {
    return Object.fromEntries(this.keys);
  }

  private flush(): void {
    if (this.ephemeral) return;
    const body: StoreFile = { keys: this.all() };
    // Write-then-rename: a crash mid-write must not leave a truncated store, which would
    // silently downgrade every node to first-contact on the next start.
    const tmp = `${this.storePath}.tmp`;
    writeFileSync(tmp, JSON.stringify(body, null, 2) + '\n');
    renameSync(tmp, this.storePath);
  }
}
