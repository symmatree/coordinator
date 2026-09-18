// Turn what a machine reports into what the screen shows.
//
// The service renders facts and offers buttons; it does not decide what a machine should be
// running and nothing converges toward a target (coordinator#326). "Out of date" is a label
// next to what head actually is, not a gate -- every button stays pressable.

import { commitTitle, refHead } from './github.js';
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
}

export class LookupCache implements Lookups {
  private readonly titles = new Map<string, string>();
  private readonly heads = new Map<string, { head: string; at: number }>();

  constructor(
    token: string | undefined,
    /** How long a head-of-ref answer stays good. Refs move; shas do not. */
    private readonly headTtlMs = 60_000,
    private readonly now: () => number = Date.now,
    /** The network, injectable so the caching itself can be tested without it. */
    private readonly fetchers: Lookups = {
      title: (repo, sha) => commitTitle(repo, sha, token),
      head: (repo, ref) => refHead(repo, ref, token),
    },
  ) {}

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

/** Annotate one unit. A failed lookup is recorded on the unit, never thrown. */
export async function enrichUnit(unit: ProbeUnit, cache: Lookups): Promise<UnitStatus> {
  const repo = repoFromUrl(unit.source);
  if (repo === undefined || unit.revision === undefined || unit.refName === undefined) {
    return { ...unit, repo };
  }
  try {
    const head = await cache.head(repo, unit.refName);
    const [title, headTitle] = await Promise.all([
      cache.title(repo, unit.revision),
      head === unit.revision ? Promise.resolve(undefined) : cache.title(repo, head),
    ]);
    return { ...unit, repo, head, current: head === unit.revision, title, headTitle };
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
