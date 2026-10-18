// Which playbook a run should use, and where the step fetches it from.
//
// The playbooks are published per commit (#513), so which one to run is a choice.
//
// NEWEST ON THE TRACKED REF, not this service's own revision: a playbook merge does not move
// our revision, so pinning to it would run an ever-staler playbook while reporting a current
// service.
//
// What keeps that honest is the other end: the step reads the archive's own VERSION and
// refuses a mismatch, and prints what it actually unpacked. So this picks, and the run
// records.

import { listArtifacts, listBuilds, type GithubOptions } from './github.js';

/** The artifact a step should fetch, and the commit it is expected to carry. */
export interface PlaybookArtifact {
  url: string;
  revision: string;
  /** For saying which build it came from when something is wrong. */
  runId: number;
  createdAt: string;
}

/**
 * The newest published playbook archive on the tracked ref.
 *
 * Walks back through recent builds rather than taking only the newest: a build can succeed
 * and still have no artifact -- expired past GitHub's retention, or a run that produced
 * nothing -- and the next one down is a better answer than a failure.
 */
export async function latestPlaybook(
  opts: GithubOptions,
  look = 5,
): Promise<PlaybookArtifact> {
  const builds = await listBuilds(opts, look);
  if (builds.length === 0) {
    throw new Error(
      `no successful ${opts.workflow} runs on ${opts.ref} in ${opts.repo}, so there is no ` +
        'playbook to run',
    );
  }
  const skipped: string[] = [];
  for (const build of builds) {
    const artifacts = await listArtifacts(opts, build.runId);
    const wanted = artifacts.find((a) => a.name === `ansible-${build.sha}`);
    if (wanted === undefined) {
      skipped.push(`${build.sha.slice(0, 10)}: no ansible- artifact`);
      continue;
    }
    if (wanted.expired) {
      skipped.push(`${build.sha.slice(0, 10)}: expired ${wanted.expiresAt}`);
      continue;
    }
    return {
      // The same endpoint `downloadArtifact` uses. The step has the token to call it.
      url: `https://api.github.com/repos/${opts.repo}/actions/artifacts/${wanted.id}/zip`,
      revision: build.sha,
      runId: build.runId,
      createdAt: build.createdAt,
    };
  }
  throw new Error(
    `the last ${builds.length} ${opts.workflow} builds on ${opts.ref} have no usable ` +
      `artifact -- ${skipped.join('; ')}`,
  );
}
