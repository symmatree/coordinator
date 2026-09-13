// The route surface. The UI is one client of this, not the only way in -- the test that
// matters is whether `curl` can run pre-flight (coordinator#223: three named trigger surfaces
// already exist -- the phone UI, hardwired buttons, and the pocketterm -- so the actions are
// the thing and the buttons are a skin).

import { readFileSync } from 'node:fs';
import Fastify, { type FastifyInstance } from 'fastify';
import type { Config } from './config.js';
import { enabledNodes, findNode } from './inventory.js';
import { probeAll, probeNode } from './probe.js';
import { RunRegistry, sinkFor } from './runs.js';
import { bootstrap, update } from './actions.js';

export function buildServer(cfg: Config, runs = new RunRegistry()): FastifyInstance {
  const app = Fastify({ logger: true });

  app.get('/healthz', async () => ({ ok: true }));

  // The UI is served from here, but it is just another client of the routes below: no
  // server-rendered state, no private endpoints. Anything the page can do, curl can do.
  const indexHtml = readFileSync(new URL('../ui/index.html', import.meta.url), 'utf8');
  app.get('/', async (_req, reply) => reply.type('text/html; charset=utf-8').send(indexHtml));

  /** Service status, including whether host-key trust actually survives a restart. */
  app.get('/status', async () => ({
    ok: true,
    inventoryPath: cfg.inventoryPath,
    nodes: cfg.inventory.nodes.map((n) => ({ name: n.name, role: n.role, enabled: n.enabled })),
    hostKeys: {
      known: cfg.ssh.hostKeys.all(),
      // Surfaced rather than left to be discovered: if the store is ephemeral, every restart
      // is a fresh first-contact and host-key verification protects nothing.
      ephemeral: cfg.ssh.hostKeys.ephemeral,
    },
  }));

  app.get('/nodes', async () => cfg.inventory.nodes);

  app.get('/probe', async () => probeAll(enabledNodes(cfg.inventory), cfg.ssh));

  app.get<{ Params: { name: string } }>('/nodes/:name/probe', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    return probeNode(node, cfg.ssh);
  });

  /** Forget a node's recorded host key. The operation to run when you reflash that card. */
  app.delete<{ Params: { name: string } }>('/nodes/:name/hostkey', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    return { forgotten: cfg.ssh.hostKeys.forget(node.name) };
  });

  const ACTIONS = { update, bootstrap } as const;

  app.post<{ Params: { name: string; action: keyof typeof ACTIONS } }>(
    '/nodes/:name/:action',
    async (req, reply) => {
      const fn = ACTIONS[req.params.action];
      if (!fn) return reply.code(404).send({ error: `no such action: ${req.params.action}` });
      const node = findNode(cfg.inventory, req.params.name);
      if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
      if (!node.enabled) {
        return reply.code(409).send({ error: `${node.name} is not enabled in the inventory` });
      }
      try {
        const run = runs.start(req.params.action, node.name, (emit) =>
          fn(node, cfg.ssh, sinkFor(emit)),
        );
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
