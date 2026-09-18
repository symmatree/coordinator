// What GitHub knows: which builds exist, what a sha belongs to, and whether it is current.
//
// Discovery needs no credential -- the repos are public, and listing workflow runs and their
// artifacts works unauthenticated (verified against the live API). Only downloading an
// artifact zip does: that endpoint answers `401 Requires authentication` even for a public
// repo. So the status screen works with no token at all and only the image fetch needs one.

const API = 'https://api.github.com';

export interface GithubOptions {
  /** `owner/repo` holding the image build. */
  repo: string;
  /** Workflow file name, e.g. `build-pi-image.yaml`. */
  workflow: string;
  /** Branch or tag the fleet tracks. */
  ref: string;
  /** Only needed to download an artifact; discovery works without it. */
  token?: string;
}

export interface Build {
  runId: number;
  /** Full sha the build was made from. */
  sha: string;
  ref: string;
  createdAt: string;
  /** Subject of the commit, which for a squash merge carries the PR title and number. */
  title?: string;
}

export interface Artifact {
  id: number;
  name: string;
  sizeBytes: number;
  /** GitHub deletes artifacts on a retention clock; an expired one cannot be fetched. */
  expired: boolean;
  expiresAt: string;
}

async function api(path: string, token?: string): Promise<unknown> {
  const res = await fetch(`${API}${path}`, {
    headers: {
      accept: 'application/vnd.github+json',
      'x-github-api-version': '2022-11-28',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!res.ok) {
    throw new Error(`GitHub ${res.status} for ${path}: ${(await res.text()).slice(0, 200)}`);
  }
  return res.json();
}

/**
 * Successful builds on the tracked ref, newest first.
 *
 * The run carries the identity -- head sha, ref, when -- so nothing has to be reconstructed
 * from the artifact afterwards. We asked for it by that identity, so we know what it is.
 */
export async function listBuilds(opts: GithubOptions, limit = 10): Promise<Build[]> {
  const q = `status=success&branch=${encodeURIComponent(opts.ref)}&per_page=${limit}`;
  const body = (await api(
    `/repos/${opts.repo}/actions/workflows/${opts.workflow}/runs?${q}`,
    opts.token,
  )) as { workflow_runs?: Array<Record<string, unknown>> };
  return (body.workflow_runs ?? []).map((r) => ({
    runId: r.id as number,
    sha: r.head_sha as string,
    ref: (r.head_branch as string) ?? opts.ref,
    createdAt: r.created_at as string,
    title: (r.display_title as string) || undefined,
  }));
}

/** The artifacts a run produced. Role selection is the caller's; this just lists. */
export async function listArtifacts(opts: GithubOptions, runId: number): Promise<Artifact[]> {
  const body = (await api(`/repos/${opts.repo}/actions/runs/${runId}/artifacts`, opts.token)) as {
    artifacts?: Array<Record<string, unknown>>;
  };
  return (body.artifacts ?? []).map((a) => ({
    id: a.id as number,
    name: a.name as string,
    sizeBytes: a.size_in_bytes as number,
    expired: a.expired as boolean,
    expiresAt: a.expires_at as string,
  }));
}

/**
 * Whether a sha is the current head of a ref.
 *
 * This is a displayed fact, not a gate. "Not head" is shown next to what head actually is and
 * the operator decides; nothing here refuses to act on it.
 */
export async function isHeadOfRef(
  repo: string,
  ref: string,
  sha: string,
  token?: string,
): Promise<{ head: string; current: boolean }> {
  const body = (await api(`/repos/${repo}/commits/${encodeURIComponent(ref)}`, token)) as {
    sha: string;
  };
  return { head: body.sha, current: body.sha === sha };
}

/**
 * The title of the PR a sha came from, which is what makes "the build I expected is not here
 * yet" legible as a title that has not appeared.
 *
 * Asks which PRs contain the commit rather than reading the commit subject. A squash merge
 * would carry the title in the subject, but `dotfiles-symm` uses merge commits, whose subject
 * is `Merge pull request #64 from symmatree/feat/...` -- the branch name, not the title.
 * This endpoint answers for both styles, and needs no credential.
 *
 * Falls back to the commit subject when no PR contains the sha, which is the case for a
 * commit pushed straight to the branch.
 */
export async function commitTitle(repo: string, sha: string, token?: string): Promise<string> {
  const prs = (await api(`/repos/${repo}/commits/${sha}/pulls`, token)) as Array<{
    number?: number;
    title?: string;
  }>;
  const pr = Array.isArray(prs) ? prs[0] : undefined;
  if (pr?.title) return `${pr.title} (#${pr.number})`;

  const body = (await api(`/repos/${repo}/commits/${sha}`, token)) as {
    commit?: { message?: string };
  };
  return (body.commit?.message ?? '').split('\n')[0] ?? '';
}

/** Download an artifact zip. The one call that needs a credential. */
export async function downloadArtifact(opts: GithubOptions, artifactId: number): Promise<Response> {
  if (!opts.token) {
    throw new Error(
      'downloading an artifact needs a GitHub token: listing is public but ' +
        '/actions/artifacts/:id/zip answers 401 without one',
    );
  }
  const res = await fetch(`${API}/repos/${opts.repo}/actions/artifacts/${artifactId}/zip`, {
    headers: {
      accept: 'application/vnd.github+json',
      authorization: `Bearer ${opts.token}`,
    },
  });
  if (!res.ok) {
    throw new Error(`GitHub ${res.status} downloading artifact ${artifactId}`);
  }
  return res;
}
