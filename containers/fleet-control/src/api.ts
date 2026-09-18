// The route surface. The UI is one client of it; anything the page can do, curl can do.

import { createReadStream, readFileSync } from 'node:fs';
import Fastify, { type FastifyInstance } from 'fastify';
import type { Config } from './config.js';
import { findNode } from './inventory.js';
import { RunRegistry, sinkFor } from './runs.js';
import { converge } from './actions.js';
import { ImageCache } from './imagecache.js';
import { commitTitle, isHeadOfRef, listArtifacts, listBuilds } from './github.js';

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
        const run = runs.start('converge', node.name, (emit) =>
          converge(node, cfg.action, { reflashed }, sinkFor(emit)),
        );
        return reply.code(202).send({ id: run.id, action: run.action, node: run.node, reflashed });
      } catch (err) {
        return reply.code(409).send({ error: (err as Error).message });
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
        artifacts: role === undefined ? undefined : await listArtifacts(cfg.images, b.runId),
        cached: held.some((h) => h.sha === b.sha && (role === undefined || h.role === role)),
      })),
    );
  });

  /** What the cache holds. Nothing evicts, so this only grows until the volume is wiped. */
  app.get('/images/cached', async () => images.list());

  /**
   * Put a build in the cache. The one route that needs `FLEET_GITHUB_TOKEN`; it says so
   * plainly rather than failing as a bare 500.
   */
  app.post<{ Params: { role: string }; Querystring: { sha?: string } }>(
    '/images/:role/fetch',
    async (req, reply) => {
      const builds = await listBuilds(cfg.images, 20);
      const build = req.query.sha
        ? builds.find((b) => b.sha.startsWith(req.query.sha as string))
        : builds[0];
      if (!build) return reply.code(404).send({ error: `no build matching ${req.query.sha}` });
      const arts = await listArtifacts(cfg.images, build.runId);
      const art = arts.find((a) => a.name.startsWith(`${req.params.role}-`));
      if (!art) {
        return reply
          .code(404)
          .send({ error: `run ${build.runId} has no artifact for role ${req.params.role}` });
      }
      if (art.expired) {
        return reply
          .code(410)
          .send({ error: `artifact for ${build.sha.slice(0, 10)} expired at ${art.expiresAt}` });
      }
      try {
        return await images.ensure(cfg.images, build, req.params.role, art.id);
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
        .header('x-fleet-image-name', meta.imgName)
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

  app.get('/runs', async () =>
    runs.list().map(({ lines, ...rest }) => ({ ...rest, lineCount: lines.length })),
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
    for (const l of run.lines) send(l);
    if (run.status !== 'running') {
      reply.raw.end();
      return;
    }
    const unsubscribe = runs.subscribe(run.id, send);
    req.raw.on('close', unsubscribe);
  });

  return app;
}
