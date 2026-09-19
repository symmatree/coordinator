// Turn what a machine reports into what the screen shows.
//
// The service renders facts and offers buttons; it does not decide what a machine should be
// running and nothing converges toward a target (coordinator#326). "Out of date" is a label
// next to what head actually is, not a gate -- every button stays pressable.

import { commitTitle, refHead, registryImage, type RegistryImage } from './github.js';
import type { NodeStatus } from './probe.js';
import type { ProbeUnit } from './manifest.js';

/** What the screen adds to a reported unit. All of it is display. */
export interface UnitStatus extends ProbeUnit {
  /** `owner/repo`, parsed from the reported source URL. */
  repo?: string;
  /** Current head of the unit's ref, when we could ask. */
  head?: string;
  /** Whether the reported revision is that head. */
  current?: boolean;
  /** PR title for what is installed, and for what head is. */
  title?: string;
  headTitle?: string;
  /** Why the above is missing, when it is. Shown, not swallowed. */
  lookupError?: string;
}

export interface EnrichedNode extends Omit<NodeStatus, 'probe'> {
  units: UnitStatus[];
  /** The `[host]` table. Named apart from `host`, which is the address. */
  hostInfo: Record<string, string>;
}

/** `owner/repo` out of a browsable URL. Undefined rather than a guess if it is not one. */
export function repoFromUrl(url: string | undefined): string | undefined {
  if (url === undefined) return undefined;
  const m = /github\.com[/:]([^/]+)\/([^/.]+)/.exec(url);
  return m?.[1] !== undefined && m[2] !== undefined ? `${m[1]}/${m[2]}` : undefined;
}

/**
 * Lookup cache, and the reason a refresh is cheap.
 *
 * Unauthenticated GitHub allows 60 requests an hour, which a naive screen would spend in a
 * few refreshes. But a **sha's PR title never changes**, so it is cached for good; only
 * head-of-ref moves, and that is one call per repo per window. After the first probe a
 * refresh costs almost nothing, with or without a token.
 */
/** What enrichment needs. An interface so it can be exercised without the network. */
export interface Lookups {
  title(repo: string, sha: string): Promise<string>;
  head(repo: string, ref: string): Promise<string>;
  /** What a container tag points at now. */
  image(imageRef: string): Promise<RegistryImage | undefined>;
  /** Head sha of the newest successful build of the disk image. */
  buildSha(): Promise<string | undefined>;
}

export class LookupCache implements Lookups {
  private readonly titles = new Map<string, string>();
  private readonly heads = new Map<string, { head: string; at: number }>();
  /** Registry answers move when a tag is repushed, so they expire like a ref does. */
  private readonly images = new Map<string, { img: RegistryImage | undefined; at: number }>();
  private build?: { sha: string | undefined; at: number };

  constructor(
    token: string | undefined,
    /** How long a head-of-ref answer stays good. Refs move; shas do not. */
    private readonly headTtlMs = 60_000,
    private readonly now: () => number = Date.now,
    /** The network, injectable so the caching itself can be tested without it. */
    private readonly fetchers: Pick<Lookups, 'title' | 'head' | 'image'> & {
      buildSha: () => Promise<string | undefined>;
    } = {
      title: (repo, sha) => commitTitle(repo, sha, token),
      head: (repo, ref) => refHead(repo, ref, token),
      image: (ref) => registryImage(ref),
      buildSha: async () => undefined,
    },
  ) {}

  async image(imageRef: string): Promise<RegistryImage | undefined> {
    const hit = this.images.get(imageRef);
    if (hit && this.now() - hit.at < this.headTtlMs) return hit.img;
    const img = await this.fetchers.image(imageRef);
    this.images.set(imageRef, { img, at: this.now() });
    return img;
  }

  async buildSha(): Promise<string | undefined> {
    if (this.build && this.now() - this.build.at < this.headTtlMs) return this.build.sha;
    const sha = await this.fetchers.buildSha();
    this.build = { sha, at: this.now() };
    return sha;
  }

  async title(repo: string, sha: string): Promise<string> {
    const key = `${repo}@${sha}`;
    const hit = this.titles.get(key);
    if (hit !== undefined) return hit;
    const got = await this.fetchers.title(repo, sha);
    this.titles.set(key, got);
    return got;
  }

  async head(repo: string, ref: string): Promise<string> {
    const key = `${repo}#${ref}`;
    const hit = this.heads.get(key);
    if (hit && this.now() - hit.at < this.headTtlMs) return hit.head;
    const head = await this.fetchers.head(repo, ref);
    this.heads.set(key, { head, at: this.now() });
    return head;
  }
}

/**
 * Is this unit the artifact its branch would produce right now?
 *
 * NOT `revision === branch head`. That was wrong and reported correctly-built artifacts as
 * stale: every build here is path-filtered, so an artifact's revision is the last commit that
 * touched ITS paths and is almost never branch head. A doc edit made every container on every
 * machine go red. Measured: campod-camera's image carried 26736fb, which was exactly the last
 * commit touching its paths, while main was at ecff0ee.
 *
 * So currency is asked per kind, against the thing that actually determines the artifact:
 *
 * - **container** -- the digest its tag points at now, versus the digest the device pulled.
 *   Exact, content-based, and needs no knowledge of which paths trigger which build.
 * - **disk image** -- the newest successful build of it, rather than the newest commit.
 * - **anything else** (the checkout, and the payload that will replace it) -- branch head,
 *   which is right for a whole-repo working tree because that genuinely is what it tracks.
 */
async function currency(
  unit: ProbeUnit,
  repo: string,
  cache: Lookups,
): Promise<{ head?: string; current?: boolean }> {
  if (unit.kind === 'container') {
    const ref = unit.extra.FLEET_CONTAINER_IMAGE;
    // RepoDigests is `ghcr.io/owner/name@sha256:...`; compare the digest, not the prefix.
    const installed = unit.extra.FLEET_CONTAINER_IMAGE_DIGEST?.split('@').pop();
    if (ref === undefined || installed === undefined) return {};
    const now = await cache.image(ref);
    if (now === undefined) return {};
    return { head: now.revision, current: installed === now.digest };
  }

  if (unit.kind === 'disk_image') {
    const built = await cache.buildSha();
    if (built === undefined) return {};
    return { head: built, current: built === unit.revision };
  }

  if (unit.refName === undefined) return {};
  const head = await cache.head(repo, unit.refName);
  return { head, current: head === unit.revision };
}

/** Annotate one unit. A failed lookup is recorded on the unit, never thrown. */
export async function enrichUnit(unit: ProbeUnit, cache: Lookups): Promise<UnitStatus> {
  const repo = repoFromUrl(unit.source);
  // A container needs no refName -- its currency is a digest question -- so only the repo and
  // the revision are required, and those are what name the change for display.
  if (repo === undefined || unit.revision === undefined) {
    return { ...unit, repo };
  }
  try {
    const { head, current } = await currency(unit, repo, cache);
    const [title, headTitle] = await Promise.all([
      cache.title(repo, unit.revision),
      // Only name the other side when there IS one and it differs -- "what you would get"
      // is only meaningful if it is not what you have.
      head === undefined || head === unit.revision || current === true
        ? Promise.resolve(undefined)
        : cache.title(repo, head),
    ]);
    return { ...unit, repo, head, current, title, headTitle };
  } catch (err) {
    return { ...unit, repo, lookupError: (err as Error).message };
  }
}

/** Annotate everything a probe returned. Machines that did not answer pass through as-is. */
export async function enrich(statuses: NodeStatus[], cache: Lookups): Promise<EnrichedNode[]> {
  return Promise.all(
    statuses.map(async ({ probe, ...rest }) => ({
      ...rest,
      hostInfo: probe?.host ?? {},
      units: await Promise.all((probe?.units ?? []).map((u) => enrichUnit(u, cache))),
    })),
  );
}
