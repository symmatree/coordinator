import { test } from 'node:test';
import assert from 'node:assert/strict';
import { latestPlaybook } from '../src/playbookartifact.js';

const OPTS = {
  repo: 'symmatree/coordinator',
  workflow: 'ansible-payload.yaml',
  ref: 'main',
};

/** GitHub, as much of it as this needs: successful runs, then each run's artifacts. */
function githubWith(
  runs: Array<{ id: number; sha: string; created?: string }>,
  artifactsByRun: Record<number, Array<{ id: number; name: string; expired?: boolean }>>,
) {
  const asked: string[] = [];
  const real = globalThis.fetch;
  globalThis.fetch = (async (u: string) => {
    asked.push(String(u));
    const body = /\/runs\/(\d+)\/artifacts/.exec(String(u));
    if (body !== null) {
      const list = artifactsByRun[Number(body[1])] ?? [];
      return json({
        artifacts: list.map((a) => ({
          id: a.id,
          name: a.name,
          size_in_bytes: 10,
          expired: a.expired ?? false,
          expires_at: '2027-01-01T00:00:00Z',
        })),
      });
    }
    return json({
      workflow_runs: runs.map((r) => ({
        id: r.id,
        head_sha: r.sha,
        head_branch: 'main',
        created_at: r.created ?? '2026-10-17T00:00:00Z',
        display_title: 'a commit',
      })),
    });
  }) as typeof fetch;
  return { asked, restore: () => { globalThis.fetch = real; } };
}

function json(body: unknown): Response {
  return { ok: true, json: async () => body, text: async () => JSON.stringify(body) } as unknown as Response;
}

const SHA_A = 'a'.repeat(40);
const SHA_B = 'b'.repeat(40);

test('takes the newest build that actually has an archive', async () => {
  const g = githubWith(
    [{ id: 2, sha: SHA_B }, { id: 1, sha: SHA_A }],
    { 2: [{ id: 22, name: `ansible-${SHA_B}` }], 1: [{ id: 11, name: `ansible-${SHA_A}` }] },
  );
  try {
    const got = await latestPlaybook(OPTS);
    assert.equal(got.revision, SHA_B);
    assert.equal(got.url, 'https://api.github.com/repos/symmatree/coordinator/actions/artifacts/22/zip');
  } finally {
    g.restore();
  }
});

test('walks past a build whose artifact expired rather than failing', async () => {
  // Artifacts are deleted on a 90-day clock. The build still shows as successful, so the
  // newest success is not always a runnable one -- and the next one down is a better answer
  // than refusing to converge.
  const g = githubWith(
    [{ id: 2, sha: SHA_B }, { id: 1, sha: SHA_A }],
    {
      2: [{ id: 22, name: `ansible-${SHA_B}`, expired: true }],
      1: [{ id: 11, name: `ansible-${SHA_A}` }],
    },
  );
  try {
    const got = await latestPlaybook(OPTS);
    assert.equal(got.revision, SHA_A);
  } finally {
    g.restore();
  }
});

test('walks past a build that produced no archive at all', async () => {
  const g = githubWith(
    [{ id: 2, sha: SHA_B }, { id: 1, sha: SHA_A }],
    { 2: [{ id: 99, name: 'something-else' }], 1: [{ id: 11, name: `ansible-${SHA_A}` }] },
  );
  try {
    assert.equal((await latestPlaybook(OPTS)).revision, SHA_A);
  } finally {
    g.restore();
  }
});

test('the artifact must belong to the build whose sha we report', async () => {
  // The name carries the sha, so an artifact from another commit cannot be mistaken for
  // this build's. Reporting a revision we did not fetch is the failure the step's own
  // VERSION check exists to catch; this is the near end of the same guard.
  const g = githubWith(
    [{ id: 2, sha: SHA_B }],
    { 2: [{ id: 22, name: `ansible-${SHA_A}` }] },
  );
  try {
    await assert.rejects(() => latestPlaybook(OPTS), /no usable artifact/);
  } finally {
    g.restore();
  }
});

test('no successful builds says so, rather than returning nothing useful', async () => {
  const g = githubWith([], {});
  try {
    await assert.rejects(() => latestPlaybook(OPTS), /no successful ansible-payload\.yaml runs/);
  } finally {
    g.restore();
  }
});

test('every exhausted build is named, so the reason is in the error', async () => {
  const g = githubWith(
    [{ id: 2, sha: SHA_B }, { id: 1, sha: SHA_A }],
    {
      2: [{ id: 22, name: `ansible-${SHA_B}`, expired: true }],
      1: [{ id: 11, name: 'nope' }],
    },
  );
  try {
    await assert.rejects(
      () => latestPlaybook(OPTS),
      (e: Error) => e.message.includes(SHA_B.slice(0, 10)) && e.message.includes(SHA_A.slice(0, 10)),
    );
  } finally {
    g.restore();
  }
});
