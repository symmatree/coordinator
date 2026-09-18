// Every image this service pushes, kept until someone deliberately wipes it.
//
// Not a retention workaround. Images are built on every PR and almost none matter; the ones
// actually deployed are exactly the ones worth keeping, and a cached artifact survives its
// build inputs disappearing in a way a rebuild does not. The bigger win is transfer: one
// image to five machines is one fetch and five local reads (coordinator#312).
//
// Nothing evicts. Entries go when the volume is wiped.

import { createHash } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { mkdir, readdir, readFile, rename, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { downloadArtifact, type Build, type GithubOptions } from './github.js';

/** What we know about a cached image, written beside it so the cache is self-describing. */
export interface CachedImage {
  role: string;
  /** Full sha of the commit the image was built from; matches the device's REVISION. */
  sha: string;
  ref: string;
  runId: number;
  /** sha256 of the zip as fetched, for the device to verify what it downloaded. */
  sha256: string;
  sizeBytes: number;
  fetchedAt: string;
  /** Commit subject, which for a squash merge carries the PR title. */
  title?: string;
}

/** `<role>-<sha>` -- role because artifacts are per role, sha because that is the identity. */
export function cacheKey(role: string, sha: string): string {
  return `${role}-${sha}`;
}

export class ImageCache {
  constructor(private readonly dir: string) {}

  private zipPath(key: string): string {
    return join(this.dir, `${key}.zip`);
  }
  private metaPath(key: string): string {
    return join(this.dir, `${key}.json`);
  }

  /** Everything held, newest fetch first. */
  async list(): Promise<CachedImage[]> {
    let names: string[];
    try {
      names = await readdir(this.dir);
    } catch {
      return [];
    }
    const out: CachedImage[] = [];
    for (const n of names.filter((n) => n.endsWith('.json'))) {
      try {
        out.push(JSON.parse(await readFile(join(this.dir, n), 'utf8')) as CachedImage);
      } catch {
        // A half-written or hand-edited entry should cost that entry, not the listing.
      }
    }
    return out.sort((a, b) => b.fetchedAt.localeCompare(a.fetchedAt));
  }

  async get(role: string, sha: string): Promise<CachedImage | undefined> {
    try {
      return JSON.parse(await readFile(this.metaPath(cacheKey(role, sha)), 'utf8')) as CachedImage;
    } catch {
      return undefined;
    }
  }

  /** Absolute path to the held zip, or undefined if it is not here. */
  async pathFor(role: string, sha: string): Promise<string | undefined> {
    const p = this.zipPath(cacheKey(role, sha));
    try {
      await stat(p);
      return p;
    } catch {
      return undefined;
    }
  }

  /**
   * Fetch an artifact into the cache if it is not already held, and return what we know.
   *
   * Downloads to a `.part` and renames, so an interrupted fetch never leaves a short file
   * that looks cached. The digest is computed while streaming rather than by re-reading
   * ~800 MiB afterwards.
   */
  async ensure(
    opts: GithubOptions,
    build: Build,
    role: string,
    artifactId: number,
  ): Promise<CachedImage> {
    const held = await this.get(role, build.sha);
    if (held && (await this.pathFor(role, build.sha))) return held;

    await mkdir(this.dir, { recursive: true });
    const key = cacheKey(role, build.sha);
    const finalPath = this.zipPath(key);
    const partPath = `${finalPath}.part`;

    const res = await downloadArtifact(opts, artifactId);
    if (!res.body) throw new Error(`empty body downloading artifact ${artifactId}`);

    const hash = createHash('sha256');
    let sizeBytes = 0;
    const source = Readable.fromWeb(res.body as Parameters<typeof Readable.fromWeb>[0]);
    source.on('data', (c: Buffer) => {
      hash.update(c);
      sizeBytes += c.length;
    });
    await pipeline(source, createWriteStream(partPath));
    await rename(partPath, finalPath);

    const meta: CachedImage = {
      role,
      sha: build.sha,
      ref: build.ref,
      runId: build.runId,
      sha256: hash.digest('hex'),
      sizeBytes,
      fetchedAt: new Date().toISOString(),
      title: build.title,
    };
    await writeFile(this.metaPath(key), `${JSON.stringify(meta, null, 2)}\n`);
    return meta;
  }
}
