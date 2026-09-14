// Host-key checking, with OpenSSH's semantics: record a key the first time, reject it if it
// later changes, and clear it explicitly when the card is reimaged.

import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, renameSync } from 'node:fs';
import { dirname } from 'node:path';

/** OpenSSH-style fingerprint of a public-key blob: `SHA256:` + unpadded base64. */
export function fingerprint(key: Buffer): string {
  return 'SHA256:' + createHash('sha256').update(key).digest('base64').replace(/=+$/, '');
}

export type VerifyOutcome =
  | { ok: true; state: 'new' | 'match'; fingerprint: string }
  | { ok: false; state: 'mismatch'; fingerprint: string; expected: string };

export class HostKeyStore {
  private keys = new Map<string, string>();

  constructor(private readonly storePath: string) {
    try {
      const parsed = JSON.parse(readFileSync(storePath, 'utf8')) as { keys?: Record<string, string> };
      for (const [k, v] of Object.entries(parsed.keys ?? {})) this.keys.set(k, v);
    } catch {
      // No store yet, or an unreadable one. Start empty; the first write creates it.
    }
  }

  get(name: string): string | undefined {
    return this.keys.get(name);
  }

  verify(name: string, key: Buffer): VerifyOutcome {
    const fp = fingerprint(key);
    const known = this.keys.get(name);
    if (known === undefined) {
      this.keys.set(name, fp);
      this.flush();
      return { ok: true, state: 'new', fingerprint: fp };
    }
    if (known === fp) return { ok: true, state: 'match', fingerprint: fp };
    return { ok: false, state: 'mismatch', fingerprint: fp, expected: known };
  }

  /** Drop a node's key. Reimaging generates new host keys, so bootstrap does this first. */
  forget(name: string): boolean {
    const had = this.keys.delete(name);
    if (had) this.flush();
    return had;
  }

  all(): Record<string, string> {
    return Object.fromEntries(this.keys);
  }

  private flush(): void {
    try {
      mkdirSync(dirname(this.storePath), { recursive: true });
      const tmp = `${this.storePath}.tmp`;
      writeFileSync(tmp, JSON.stringify({ keys: this.all() }, null, 2) + '\n');
      renameSync(tmp, this.storePath);
    } catch {
      // Keep the in-memory map either way; a store we cannot write is not worth failing over.
    }
  }
}
