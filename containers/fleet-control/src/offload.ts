// Getting a packaged session off a device and into a flight directory.
//
// The shape is #31's: PACKAGE on the device, TRANSFER and VERIFY here, then DELETE -- three
// steps, in that order, so "the vehicle is no longer the source of truth" is a claim rather
// than a hope. Nothing is deleted that has not been read back and hashed.
//
// The flight directory is named BEFORE anything is selected, and every node's contribution
// lands in the same one. The alternative -- copy per node, join afterwards -- makes "a
// partially assembled flight" a state something has to detect and clean up; naming first
// means the directory either has every node's contribution or is visibly short one.

import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { hostOf, type FleetNode } from './inventory.js';
import type { ActionContext } from './actions.js';
import { deleteSessions, packageSession, type Bundle } from './sessions.js';

/** What a flight directory records about one collected session. */
export interface Collected {
  node: string;
  session: string;
  file: string;
  bytes: number;
  sha256: string;
  collectedAt: string;
  /** Whether the source was removed from the device afterwards. */
  sourceDeleted: boolean;
}

/** `flight.json`: what this directory is supposed to contain, so short is visible. */
export interface FlightRecord {
  flight: string;
  createdAt: string;
  collected: Collected[];
}

/** A flight name has to be a single safe path segment -- it becomes a directory. */
export function validFlightName(name: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name) && name !== '.' && name !== '..';
}

/**
 * Stream a file off a device, hashing as it goes.
 *
 * `ssh cat` rather than scp: one process, and the digest falls out of the same pass instead
 * of re-reading hundreds of megabytes afterwards. Writes to `.part` and renames, so an
 * interrupted transfer never leaves a short file that looks complete.
 */
async function fetchFile(
  node: FleetNode,
  ctx: ActionContext,
  remotePath: string,
  destPath: string,
): Promise<{ bytes: number; sha256: string }> {
  const part = `${destPath}.part`;
  const child = spawn('ssh', [
    '-i', ctx.privateKeyPath,
    '-o', 'BatchMode=yes',
    '-o', 'StrictHostKeyChecking=accept-new',
    '-o', `UserKnownHostsFile=${ctx.knownHostsPath}`,
    '-o', `ConnectTimeout=${ctx.sshTimeoutSec}`,
    `${ctx.inventory.user}@${hostOf(node)}`,
    // Quoted: the path comes from the device's own package output, but it is still a string
    // going into a remote shell and there is no reason to let it be anything else.
    'cat', `'${remotePath.replace(/'/g, "'\\''")}'`,
  ]);

  const hash = createHash('sha256');
  let bytes = 0;
  child.stdout.on('data', (c: Buffer) => {
    hash.update(c);
    bytes += c.length;
  });
  let stderr = '';
  child.stderr.on('data', (c: Buffer) => {
    stderr += c.toString('utf8');
  });

  const exited = new Promise<number>((resolve, reject) => {
    child.on('error', (e) => reject(new Error(`could not run ssh: ${e.message}`)));
    child.on('close', (code) => resolve(code ?? -1));
  });

  await pipeline(child.stdout, createWriteStream(part));
  const code = await exited;
  if (code !== 0) {
    throw new Error(`${node.name}: fetching ${remotePath} failed (exit ${code}) ${stderr.trim()}`);
  }
  await rename(part, destPath);
  return { bytes, sha256: hash.digest('hex') };
}

export interface OffloadOptions {
  /** Where flight directories live -- the datasets share. */
  flightsDir: string;
  /** Delete the session from the device once the bundle verifies. */
  deleteAfter?: boolean;
  note?: (line: string) => void;
}

/**
 * Package, fetch, verify and (optionally) delete one session.
 *
 * VERIFICATION IS AGAINST THE DEVICE'S OWN DIGEST, computed while it built the bundle. A
 * hash we calculated over bytes we received proves only that we hashed what we got; matching
 * the sender's proves the bytes are the ones it wrote.
 *
 * Deletion happens only after that match. A failure anywhere leaves the session where it is.
 */
export async function offloadSession(
  node: FleetNode,
  ctx: ActionContext,
  session: string,
  flight: string,
  opts: OffloadOptions,
): Promise<Collected> {
  const say = opts.note ?? (() => {});
  const dir = join(opts.flightsDir, flight);
  await mkdir(dir, { recursive: true });

  say(`${node.name}: packaging ${session.slice(0, 8)}`);
  const bundle: Bundle = await packageSession(node, ctx, session);
  say(`${node.name}: packaged ${(bundle.bytes / 1e6).toFixed(0)} MB in ${bundle.seconds}s`);

  const file = `${node.name}_${session}.tar.gz`;
  const got = await fetchFile(node, ctx, bundle.bundle, join(dir, file));
  say(`${node.name}: fetched ${(got.bytes / 1e6).toFixed(0)} MB`);

  if (got.sha256 !== bundle.sha256) {
    throw new Error(
      `${node.name}: ${session} did not survive the transfer -- device said ${bundle.sha256.slice(0, 12)}, got ${got.sha256.slice(0, 12)}. Nothing deleted.`,
    );
  }
  say(`${node.name}: verified ${bundle.sha256.slice(0, 12)}`);

  let sourceDeleted = false;
  if (opts.deleteAfter === true) {
    const res = await deleteSessions(node, ctx, [session]);
    sourceDeleted = true;
    say(`${node.name}: freed ${(res.bytes / 1e6).toFixed(0)} MB on the device`);
  }

  const collected: Collected = {
    node: node.name,
    session,
    file,
    bytes: got.bytes,
    sha256: got.sha256,
    collectedAt: new Date().toISOString(),
    sourceDeleted,
  };
  await recordCollected(dir, flight, collected);
  return collected;
}

/**
 * Append to the flight's record.
 *
 * Rewritten after each session rather than once at the end: a run that dies half way leaves a
 * directory that says what is in it, which is the difference between a short flight and an
 * unlabelled pile of tarballs.
 */
async function recordCollected(dir: string, flight: string, entry: Collected): Promise<void> {
  const path = join(dir, 'flight.json');
  let record: FlightRecord;
  try {
    record = JSON.parse(await readFile(path, 'utf8')) as FlightRecord;
  } catch {
    record = { flight, createdAt: new Date().toISOString(), collected: [] };
  }
  record.collected = record.collected
    .filter((c) => !(c.node === entry.node && c.session === entry.session))
    .concat(entry);
  await writeFile(path, `${JSON.stringify(record, null, 2)}\n`);
}
