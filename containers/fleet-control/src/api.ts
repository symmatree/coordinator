// The route surface. The UI is one client of it; anything the page can do, curl can do.

import { readFileSync } from 'node:fs';
import Fastify, { type FastifyInstance } from 'fastify';
import type { Config } from './config.js';
import { findNode } from './inventory.js';
import { RunRegistry, sinkFor } from './runs.js';
import { bootstrap, update, type ActionContext } from './actions.js';

export function buildServer(cfg: Config, runs = new RunRegistry()): FastifyInstance {
  const app = Fastify({ logger: true });
  const ctx: ActionContext = {
    inventory: cfg.inventory,
    ssh: cfg.ssh,
    repoUrl: cfg.repoUrl,
    checkoutPath: cfg.checkoutPath,
  };

  app.get('/healthz', async () => ({ ok: true }));

  const indexHtml = readFileSync(new URL('../ui/index.html', import.meta.url), 'utf8');
  app.get('/', async (_req, reply) => reply.type('text/html; charset=utf-8').send(indexHtml));

  app.get('/nodes', async () => cfg.inventory.nodes);

  const ACTIONS = { update, bootstrap } as const;

  app.post<{ Params: { name: string; action: keyof typeof ACTIONS } }>(
    '/nodes/:name/:action',
    async (req, reply) => {
      const fn = ACTIONS[req.params.action];
      if (!fn) return reply.code(404).send({ error: `no such action: ${req.params.action}` });
      const node = findNode(cfg.inventory, req.params.name);
      if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
      try {
        const run = runs.start(req.params.action, node.name, (emit) => fn(node, ctx, sinkFor(emit)));
        return reply.code(202).send({ id: run.id, action: run.action, node: run.node });
      } catch (err) {
        return reply.code(409).send({ error: (err as Error).message });
      }
    },
  );

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
