// The route surface. The UI is one client of it; anything the page can do, curl can do.

import { createReadStream, readFileSync } from 'node:fs';
import { Readable } from 'node:stream';
import Fastify, { type FastifyInstance } from 'fastify';
import type { Config } from './config.js';
import { findNode } from './inventory.js';
import { RunRegistry, sinkFor } from './runs.js';
import { converge, reimage } from './actions.js';
import { ImageCache } from './imagecache.js';
import { probeAll, probeNode } from './probe.js';
import { deleteSessions, listSessions, stopCapture } from './sessions.js';
import { offloadSession, validFlightName } from './offload.js';
import { enrich, LookupCache } from './status.js';
import { eventFiles, eventLines, isRunId, logChunks } from './runartifacts.js';
import { commitTitle, isHeadOfRef, listArtifacts, listBuilds, refHead, registryImage } from './github.js';

export function buildServer(cfg: Config, runs = new RunRegistry()): FastifyInstance {
  const app = Fastify({ logger: true });

  app.get('/healthz', async () => ({ ok: true }));

  const indexHtml = readFileSync(new URL('../ui/index.html', import.meta.url), 'utf8');
  app.get('/', async (_req, reply) => reply.type('text/html; charset=utf-8').send(indexHtml));

  app.get('/nodes', async () => cfg.inventory.nodes);

  /**
   * Converge a node. `?reflashed=true` clears the recorded host key first, which is the one
   * thing a reflashed card needs and nothing else does.
   */
  app.post<{ Params: { name: string }; Querystring: { reflashed?: string } }>(
    '/nodes/:name/converge',
    async (req, reply) => {
      const node = findNode(cfg.inventory, req.params.name);
      if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
      const reflashed = req.query.reflashed === 'true';
      try {
        const run = runs.start('converge', node.name, (emit, runId) =>
          converge(node, cfg.action, { runId, reflashed }, sinkFor(emit)),
        );
        return reply.code(202).send({ id: run.id, action: run.action, node: run.node, reflashed });
      } catch (err) {
        return reply.code(409).send({ error: (err as Error).message });
      }
    },
  );

  // ---- status -------------------------------------------------------------------------
  // On demand, never polled: nothing should touch the fleet while it is flying, and a probe
  // is cheap enough that a refresh button is the whole scheduling policy.
  //
  // One cache for the life of the process. A sha's PR title never changes so it is kept for
  // good; only head-of-ref is re-asked. That is what keeps a refresh inside GitHub's 60/hour
  // unauthenticated budget.

  const lookups = new LookupCache(cfg.images.token, 60_000, Date.now, {
    title: (repo, sha) => commitTitle(repo, sha, cfg.images.token),
    head: (repo, ref) => refHead(repo, ref, cfg.images.token),
    image: (ref) => registryImage(ref),
    // The disk image's currency is the newest successful BUILD of it, not the newest commit:
    // its workflow is path-filtered on pi-image/**, so branch head moves for changes that
    // cannot affect the card at all.
    buildSha: async () => (await listBuilds(cfg.images, 1))[0]?.sha,
  });

  /** Every machine, concurrently. One that cannot be reached is reported, not omitted. */
  app.get('/status', async () => enrich(await probeAll(cfg.inventory.nodes, cfg.action), lookups));

  app.get<{ Params: { name: string } }>('/nodes/:name/status', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    const [one] = await enrich([await probeNode(node, cfg.action)], lookups);
    return one;
  });

  // ---- post-flight ---------------------------------------------------------------------
  // The device owns what a session is (#344); this drives it, moves the bundle, verifies it
  // and puts it with the other nodes' contributions. Capture must already be stopped -- a
  // converge does that itself, and these do not, because stopping is a decision with a cost.

  /**
   * Stop capture, without converging. The flow's first step: a session that is still growing
   * is one whose size and span change while the operator is reading them.
   */
  app.post<{ Params: { name: string } }>('/nodes/:name/stop-capture', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    try {
      await stopCapture(node, cfg.action);
      return { node: node.name, stopped: true };
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

  /** Sessions on one node, for the selection list. */
  app.get<{ Params: { name: string } }>('/nodes/:name/sessions', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    try {
      return { node: node.name, sessions: await listSessions(node, cfg.action) };
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

  /**
   * Package, fetch, verify and delete one session into a named flight.
   *
   * One session per call rather than a batch: each is minutes of zstd plus a transfer, and a
   * failure should cost that session rather than the operator's whole selection. The caller
   * loops, and the flight directory accumulates.
   */
  app.post<{
    Params: { name: string };
    Querystring: { session?: string; flight?: string; keep?: string };
  }>('/nodes/:name/offload', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    const { session, flight } = req.query;
    if (!session) return reply.code(400).send({ error: 'session is required' });
    if (!flight || !validFlightName(flight)) {
      return reply.code(400).send({
        error: 'flight must be a single path segment of letters, digits, dot, dash, underscore',
      });
    }
    try {
      const run = runs.start('offload', node.name, (emit) =>
        offloadSession(node, cfg.action, session, flight, {
          flightsDir: cfg.flightsDir,
          // Kept by default is wrong for the flow this serves -- cards fill at ~6 GB/hour and
          // there is no way to stop capture yet -- but `keep=true` exists for a first run
          // where nobody wants the delete exercised at the same time as the transfer.
          deleteAfter: req.query.keep !== 'true',
          note: (l) => emit({ t: new Date().toISOString(), stream: 'stdout', line: l }),
        }).then(() => undefined),
      );
      return reply.code(202).send({ id: run.id, action: run.action, node: run.node, flight });
    } catch (err) {
      return reply.code(409).send({ error: (err as Error).message });
    }
  });

  /** Prune sessions that were not kept. Idempotent on the device; absent is not an error. */
  app.post<{ Params: { name: string }; Body: { sessions?: string[] } }>(
    '/nodes/:name/sessions/delete',
    async (req, reply) => {
      const node = findNode(cfg.inventory, req.params.name);
      if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
      const sessions = req.body?.sessions ?? [];
      if (sessions.length === 0) return reply.code(400).send({ error: 'sessions is required' });
      try {
        return await deleteSessions(node, cfg.action, sessions);
      } catch (err) {
        return reply.code(502).send({ error: (err as Error).message });
      }
    },
  );

  // ---- images -------------------------------------------------------------------------
  // Discovery is unauthenticated: the repos are public and listing runs and artifacts works
  // without a token. Only the download does, so everything here except `fetch` works with no
  // credential configured at all.

  const images = new ImageCache(cfg.images.cacheDir);

  /**
   * Recent builds on the tracked ref, newest first, each marked with whether we already hold
   * it. Shown, not filtered: a build whose artifact has expired is still listed, because
   * "the one I wanted is gone" is worth seeing rather than silently omitting.
   */
  app.get<{ Querystring: { role?: string; limit?: string } }>('/images/builds', async (req) => {
    const builds = await listBuilds(cfg.images, Number(req.query.limit ?? 10));
    const held = await images.list();
    const role = req.query.role;
    return Promise.all(
      builds.map(async (b) => ({
        ...b,
        // The run's own display_title is the commit subject, which for a merge commit is
        // `Merge pull request #64 from symmatree/feat/...` -- the branch, not the title.
        // This is the list you pick a build from, so it gets the real one. Cached per sha
        // and a sha's PR never changes, so a second listing costs nothing.
        title: await lookups.title(cfg.images.repo, b.sha).catch(() => b.title ?? ''),
        artifacts: role === undefined ? undefined : await listArtifacts(cfg.images, b.runId),
        cached: held.some((h) => h.sha === b.sha && (role === undefined || h.role === role)),
      })),
    );
  });

  /**
   * Resolve a build to a cached image, fetching it if absent. Returns a problem rather than
   * throwing, so callers can choose the status code.
   */
  async function ensureCached(
    role: string,
    sha?: string,
  ): Promise<import('./imagecache.js').CachedImage | { error: string; code: 404 | 410 }> {
    const builds = await listBuilds(cfg.images, 20);
    const build = sha ? builds.find((b) => b.sha.startsWith(sha)) : builds[0];
    if (!build) return { error: `no build matching ${sha ?? '(newest)'}`, code: 404 };
    const held = await images.get(role, build.sha);
    if (held && (await images.pathFor(role, build.sha))) return held;
    // The run's display_title is the commit subject, which for a merge commit is the branch
    // name. What gets written beside a cached image should be the title the builds list
    // shows, since both answer "which change is this".
    build.title = await lookups.title(cfg.images.repo, build.sha).catch(() => build.title);
    const arts = await listArtifacts(cfg.images, build.runId);
    const art = arts.find((a) => a.name.startsWith(`${role}-`));
    if (!art) return { error: `run ${build.runId} has no artifact for role ${role}`, code: 404 };
    if (art.expired) {
      return { error: `artifact for ${build.sha.slice(0, 10)} expired at ${art.expiresAt}`, code: 410 };
    }
    return images.ensure(cfg.images, build, role, art.id);
  }

  /** What the cache holds. Nothing evicts, so this only grows until the volume is wiped. */
  app.get('/images/cached', async () => images.list());

  /**
   * Put a build in the cache. The one route that needs `FLEET_GITHUB_TOKEN`; it says so
   * plainly rather than failing as a bare 500.
   */
  app.post<{ Params: { role: string }; Querystring: { sha?: string } }>(
    '/images/:role/fetch',
    async (req, reply) => {
      try {
        const got = await ensureCached(req.params.role, req.query.sha);
        return 'error' in got ? reply.code(got.code).send({ error: got.error }) : got;
      } catch (err) {
        return reply.code(502).send({ error: (err as Error).message });
      }
    },
  );

  /**
   * Serve a cached image. This is what a device fetches with `get_url`, so the bytes it
   * verifies are the bytes we hold.
   */
  app.get<{ Params: { role: string; sha: string } }>(
    '/images/:role/:sha/zip',
    async (req, reply) => {
      const path = await images.pathFor(req.params.role, req.params.sha);
      const meta = await images.get(req.params.role, req.params.sha);
      if (!path || !meta) {
        return reply.code(404).send({ error: `not cached: ${req.params.role} ${req.params.sha}` });
      }
      return reply
        .type('application/zip')
        .header('content-length', String(meta.sizeBytes))
        .header('x-fleet-image-sha256', meta.sha256)
        .send(createReadStream(path));
    },
  );

  /**
   * Whether a sha is current on the tracked ref, and what the commit was. A displayed fact:
   * nothing here refuses to act on a stale answer.
   */
  app.get<{ Params: { sha: string } }>('/images/:sha/current', async (req, reply) => {
    try {
      const [head, title] = await Promise.all([
        isHeadOfRef(cfg.images.repo, cfg.images.ref, req.params.sha, cfg.images.token),
        commitTitle(cfg.images.repo, req.params.sha, cfg.images.token),
      ]);
      return { ...head, ref: cfg.images.ref, title };
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

  /**
   * Reimage a node from a cached build, fetching it first if we do not hold it.
   *
   * `?sha=` picks a build; absent means the newest on the tracked ref. The play stages and
   * arms only -- whether it worked is answered by probing afterwards, not reported here.
   */
  app.post<{ Params: { name: string }; Querystring: { sha?: string } }>(
    '/nodes/:name/reimage',
    async (req, reply) => {
      const node = findNode(cfg.inventory, req.params.name);
      if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });

      let held;
      try {
        held = await ensureCached(node.role, req.query.sha);
      } catch (err) {
        return reply.code(502).send({ error: (err as Error).message });
      }
      if ('error' in held) return reply.code(held.code).send({ error: held.error });

      const image = {
        url: `${cfg.images.publicUrl}/images/${node.role}/${held.sha}/zip`,
        sha256: held.sha256,
        sha: held.sha,
      };
      try {
        const run = runs.start('reimage', node.name, (emit, runId) =>
          reimage(node, cfg.action, { runId, image }, sinkFor(emit)),
        );
        return reply.code(202).send({ id: run.id, action: run.action, node: run.node, image });
      } catch (err) {
        return reply.code(409).send({ error: (err as Error).message });
      }
    },
  );

  // ---- what ansible left behind -------------------------------------------------------
  // Kept only for a play that did not exit 0 (#353), and only `job_events/`: the runner also
  // writes a `command` file recording the WHOLE process environment it launched ansible with,
  // which in this pod includes FLEET_GITHUB_TOKEN. So there is no "download the directory"
  // route, and there should not be one.

  /** The run's event files, or a reply saying why there are none. */
  async function eventsFor(id: string): Promise<string[] | { error: string; code: 400 | 404 }> {
    if (!isRunId(id)) return { error: `not a run id: ${id}`, code: 400 };
    const files = await eventFiles(id);
    if (files.length > 0) return files;
    const run = runs.get(id);
    if (!run) return { error: `nothing kept for ${id}; the pod may have restarted`, code: 404 };
    if (run.status === 'succeeded') {
      return { error: `run ${id} succeeded, so its ansible detail was not kept`, code: 404 };
    }
    return { error: `run ${id} ('${run.action}') has no ansible detail`, code: 404 };
  }

  /**
   * Every ansible event for a run, one JSON object per line.
   *
   * This is the thing that says HOW a play ended -- each task's whole result object, an async
   * timeout distinguishable from an UNREACHABLE from a lost connection. `curl ... > x.ndjson`
   * and read it with `jq`.
   */
  app.get<{ Params: { id: string } }>('/runs/:id/events', async (req, reply) => {
    const found = await eventsFor(req.params.id);
    if ('error' in found) return reply.code(found.code).send({ error: found.error });
    return reply.type('application/x-ndjson').send(Readable.from(eventLines(found)));
  });

  /** The same run as ansible printed it: the console output, colour codes and all. */
  app.get<{ Params: { id: string } }>('/runs/:id/log', async (req, reply) => {
    const found = await eventsFor(req.params.id);
    if ('error' in found) return reply.code(found.code).send({ error: found.error });
    return reply.type('text/plain; charset=utf-8').send(Readable.from(logChunks(found)));
  });

  app.get('/runs', async () =>
    Promise.all(
      runs.list().map(async ({ lines, ...rest }) => ({
        ...rest,
        lineCount: lines.length,
        // Whether the runner's detail is still on disk. A successful play leaves none, and a
        // pod restart takes what was kept -- so this is asked, not remembered.
        events: (await eventFiles(rest.id)).length,
      })),
    ),
  );

  app.get<{ Params: { id: string } }>('/runs/:id', async (req, reply) => {
    const run = runs.get(req.params.id);
    return run ?? reply.code(404).send({ error: `no such run: ${req.params.id}` });
  });

  /** Server-sent events, so a run can be watched live rather than polled. */
  app.get<{ Params: { id: string } }>('/runs/:id/stream', (req, reply) => {
    const run = runs.get(req.params.id);
    if (!run) {
      void reply.code(404).send({ error: `no such run: ${req.params.id}` });
      return;
    }
    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    const send = (l: unknown) => reply.raw.write(`data: ${JSON.stringify(l)}\n\n`);
    /**
     * A named terminal event, then close.
     *
     * Named rather than just closing, because EventSource RECONNECTS when a server closes the
     * connection -- so an unannounced close would have the browser reattach, get the replayed
     * lines, and be closed again, forever. The client closes on this event instead.
     *
     * It also means a watcher does not have to recognise the text of the last line to know the
     * run is over, which is what the page was doing.
     */
    const finish = (status: string) => {
      reply.raw.write(`event: done\ndata: ${JSON.stringify({ id: run.id, status })}\n\n`);
      reply.raw.end();
    };

    for (const l of run.lines) send(l);
    if (run.status !== 'running') {
      finish(run.status);
      return;
    }
    const unsubscribe = runs.subscribe(run.id, send, () => finish(runs.get(run.id)?.status ?? 'unknown'));
    req.raw.on('close', unsubscribe);
  });

  return app;
}
