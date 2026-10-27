// The route surface. The UI is one client of it; anything the page can do, curl can do.

import { readFileSync } from 'node:fs';
import { Readable } from 'node:stream';
import Fastify, { type FastifyInstance } from 'fastify';
import type { Config } from './config.js';
import { findNode } from './inventory.js';
import { converge, reboot, stop, type ConvergeKind } from './actions.js';
import { probeAll, probeNode } from './probe.js';
import { deleteSessions, listSessions, stopCapture } from './sessions.js';
import { listLogs } from './fclog.js';
import { readNotes, validFlightName, writeNotes } from './offload.js';
import { enrich, LookupCache, repoFromUrl } from './status.js';
import { collectGround, listTlogs } from './cluster.js';
import { configured as busConfigured, readPods, setDesired } from './bus.js';
import {
  follow, listWorkflows, nodeBusyWith, readWorkflow, submitCollect, workflowUrl,
  type CollectTarget,
} from './workflows.js';
import { build } from './build.js';
import { commitTitle, refHead, registryImage } from './github.js';

export function buildServer(cfg: Config): FastifyInstance {
  const app = Fastify({ logger: true });

  app.get('/healthz', async () => ({ ok: true }));

  // So `curl -X PUT --data-binary @notes.md -H 'content-type: text/markdown'` works. Fastify
  // parses application/json and text/plain out of the box and 415s anything else; a description
  // is markdown and writing it from a file should not require wrapping it in JSON.
  app.addContentTypeParser('text/markdown', { parseAs: 'string' }, (_req, body, done) => {
    done(null, body);
  });

  const indexHtml = readFileSync(new URL('../ui/index.html', import.meta.url), 'utf8');
  app.get('/', async (_req, reply) => reply.type('text/html; charset=utf-8').send(indexHtml));

  app.get('/nodes', async () => cfg.inventory.nodes);

  /**
   * What build this is and how long it has been up.
   *
   * Asked on demand rather than pushed, because the answer only matters at two moments: when a
   * long job is about to be started (has the roller just replaced me?) and when one has
   * vanished (did it?). The PR title comes from the same cached lookup the status screen uses,
   * so a refresh costs nothing after the first.
   */
  app.get('/build', async () => {
    const b = build();
    const repo = repoFromUrl(b.source);
    if (repo === undefined || b.revision === undefined) return b;
    const [title, head] = await Promise.all([
      lookups.title(repo, b.revision).catch(() => undefined),
      b.refName === undefined ? Promise.resolve(undefined) : lookups.head(repo, b.refName).catch(() => undefined),
    ]);
    return { ...b, repo, title, head, current: head === undefined ? undefined : head === b.revision };
  });

  // ---- converge, and full provision --------------------------------------------------------
  //
  // Two routes rather than one with a size flag, because they are not the same operation at
  // two sizes: one installs nothing and does not reboot, the other does both. Which playbook
  // runs is the whole difference between them, and after #453 split the playbook this service
  // ran `site.yaml` for both -- so a routine converge paid the fresh-card path.

  /**
   * Submit a playbook workflow against a node, unless something is already driving it.
   *
   * THE LOCK IS THE CLUSTER'S. `nodeBusyWith` asks for a live workflow carrying this node's
   * label; there is no copy here to go stale across a restart. Two converges against one
   * device fight over the docker daemon, so a busy node is a 409 -- and the answer names
   * what is holding it, which is where to go and look.
   *
   * 202 with the workflow, because that is the handle for everything afterwards: the
   * phase at `/workflows/:name`, the play's output at `/workflows/:name/log`, and the link.
   */
  async function submitFor(name: string, kind: ConvergeKind, reply: import('fastify').FastifyReply) {
    const node = findNode(cfg.inventory, name);
    if (!node) return reply.code(404).send({ error: `no such node: ${name}` });
    const busy = await nodeBusyWith(cfg.action.workflows, node.name);
    if (busy !== undefined) {
      return reply.code(409).send({ error: `${node.name} is already being driven by ${busy}. Wait for it, or look at why it is stuck.` });
    }
    try {
      const sub = await converge(node, cfg.action, kind);
      return reply.code(202).send({ ...sub, node: node.name, action: kind });
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  }

  /** Config, stack definitions and image pulls. The routine one, and the default. */
  app.post<{ Params: { name: string } }>('/nodes/:name/converge', (req, reply) =>
    submitFor(req.params.name, 'converge', reply));

  /**
   * A fresh card, end to end -- `site.yaml`, provision then deploy, because a bare card needs
   * both. Reboots partway, at the hand-off between them.
   */
  app.post<{ Params: { name: string } }>('/nodes/:name/provision', (req, reply) =>
    submitFor(req.params.name, 'provision', reply));

  /**
   * Stop the container set, and nothing else. Synchronous: it returns when the device
   * reports its units are out, which is seconds, and there is nothing to follow.
   *
   * Its own route rather than a flag on converge, because the operator wants it on its own:
   * a converge is twenty minutes of apt to achieve a stop.
   */
  app.post<{ Params: { name: string } }>('/nodes/:name/stop', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    try {
      await stop(node, cfg.action);
      return { node: node.name, stopped: true };
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

  /**
   * Reboot. Returns once the device has taken the request; nothing waits for it to come back.
   *
   * A REBOOT IS NEVER REFUSED FOR BEING BUSY, because busy is the case it exists for. A
   * converge that wedged held this node's slot, and the one action that would have got the box
   * back to a known state was the one the lock blocked -- both here and on the screen, which
   * disabled the button (#424). A workflow still driving the node is reported in the answer,
   * not acted on: rebooting the box ends whatever it was doing, and the workflow records that
   * itself when its step loses the device.
   */
  app.post<{ Params: { name: string } }>('/nodes/:name/reboot', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    const wasRunning = await nodeBusyWith(cfg.action.workflows, node.name).catch(() => undefined);
    try {
      const done = await reboot(node, cfg.action);
      return { node: node.name, ...done, ...(wasRunning === undefined ? {} : { wasRunning }) };
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

  // ---- the pod bus -----------------------------------------------------------------------
  //
  // What the devices say about themselves, read off MQTT. It costs them nothing: the status
  // documents are retained and already being published, so this is a subscribe rather than a
  // question. `GET /status` below is the other thing -- it asks what is INSTALLED and pays a
  // quiesce to do it, which is why readiness moved here (#434, docs/pod-bus.md).

  app.get('/pods', async (_req, reply) => {
    if (!busConfigured(cfg.bus)) return { configured: false, pods: [] };
    const read = await readPods(cfg.bus);
    // A bus that could not be read is a 502; a bus that was read and holds nothing is 200 with
    // an empty list. "No pods are publishing" and "there is no broker" are different answers,
    // and an empty result must not be able to mean either.
    if (read.error !== undefined) return reply.code(502).send({ error: read.error });
    return { configured: true, pods: read.pods };
  });

  /**
   * Stop or start the container set on one device, over the bus.
   *
   * The same units a quiesce stops, held as a desire rather than sent as an action: this is
   * the button, `quiesceNode` is what every other command does on its way past.
   *
   * `stack: running` is NOT reconciled device-side (docs/pod-bus.md): it clears the desire to be
   * stopped, and a power cycle is what actually brings the stack back. So this is the honest verb
   * for "stop collecting" and deliberately not a start button pretending to be one.
   */
  app.post<{ Params: { name: string }; Querystring: { desired?: string } }>(
    '/nodes/:name/stack',
    async (req, reply) => {
      const node = findNode(cfg.inventory, req.params.name);
      if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
      const desired = req.query.desired;
      if (desired !== 'running' && desired !== 'stopped') {
        return reply.code(400).send({ error: `desired is running or stopped, not ${JSON.stringify(desired)}` });
      }
      if (!busConfigured(cfg.bus)) {
        return reply.code(501).send({ error: 'no pod bus configured -- set FLEET_MQTT_URL' });
      }
      try {
        await setDesired(cfg.bus, node.name, 'stack', desired);
        return { node: node.name, desired };
      } catch (err) {
        return reply.code(502).send({ error: (err as Error).message });
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

  const token = cfg.action.github.token;
  const lookups = new LookupCache(token, 60_000, Date.now, {
    title: (repo, sha) => commitTitle(repo, sha, token),
    head: (repo, ref) => refHead(repo, ref, token),
    image: (ref) => registryImage(ref),
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
   * Collect a flight: everything named for it fetched and verified, all at once.
   *
   * SUBMITS, IT DOES NOT TRANSFER. The bytes move in the collect workflow's own step pods,
   * so the retrievals go at the same time instead of one after another and nothing is
   * bounded by this pod's lifetime. What comes back is the workflow, which is the thing
   * that will still be true after a restart.
   *
   * SESSIONS AND FC LOGS IN ONE CALL. They used to be separate routes run in sequence,
   * which put a 30-minute dataflash pull behind the campods for no reason -- they are
   * different devices. One list of targets means the slowest retrieval sets the cost of a
   * collect rather than the sum of them.
   *
   * What to fetch is the caller's judgement, not this route's: which capture was the
   * flight is read off `GET /nodes/:name/sessions` and `GET /nodes/:name/fc-logs`.
   */
  app.post<{
    Params: { flight: string };
    Body: { targets?: CollectTarget[] };
  }>('/flights/:flight/collect', async (req, reply) => {
    const { flight } = req.params;
    if (!validFlightName(flight)) {
      return reply.code(400).send({
        error: 'flight must be a single path segment of letters, digits, dot, dash, underscore',
      });
    }
    const targets = req.body?.targets ?? [];
    if (targets.length === 0) {
      return reply.code(400).send({
        error: 'targets is required: [{node, kind, id}, ...] with kind session or fc-log',
      });
    }
    for (const t of targets) {
      if (!t?.node || !t?.id || (t.kind !== 'session' && t.kind !== 'fc-log')) {
        return reply.code(400).send({
          error: 'every target needs a node, an id, and kind of session or fc-log',
        });
      }
      if (!findNode(cfg.inventory, t.node)) {
        return reply.code(404).send({ error: `no such node: ${t.node}` });
      }
    }

    // Refused up front for every node, not per step: a collect that takes one campod and
    // fails the other has already deleted nothing but has half a flight, and the operator
    // would rather be told before it starts than find out from a step's log.
    for (const node of new Set(targets.map((t) => t.node))) {
      const busy = await nodeBusyWith(cfg.action.workflows, node);
      if (busy !== undefined) {
        return reply.code(409).send({
          error: `${node} is already being driven by ${busy}. Wait for it, then collect.`,
        });
      }
    }

    try {
      const name = await submitCollect(cfg.action.workflows, flight, targets);
      return reply.code(202).send({
        workflow: name,
        url: workflowUrl(cfg.action.workflows, name),
        flight,
        targets,
      });
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

  /**
   * One workflow's state, for a caller following something it submitted.
   *
   * Read from the cluster every time. This service holds no copy, which is the point: the
   * workflow outlives the process, so asking is the only answer that stays true across a
   * restart.
   */
  app.get<{ Params: { name: string } }>('/workflows/:name', async (req, reply) => {
    const state = await readWorkflow(cfg.action.workflows, req.params.name);
    if (state === undefined) {
      return reply.code(404).send({ error: `no such workflow: ${req.params.name}` });
    }
    return { ...state, url: workflowUrl(cfg.action.workflows, state.name) };
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

  /**
   * What the ground-tlog share holds, so a caller can name what it wants.
   *
   * Not per node and not per flight: it is one directory tlog-split writes into. A listing, never
   * a parse -- it stats the files and reads the stamps out of their names, and reports every file
   * it finds rather than only the ones shaped as expected (#414).
   */
  app.get('/ground/tlogs', async (_req, reply) => {
    try {
      return { dir: cfg.cluster.groundTlogs, tlogs: await listTlogs(cfg.cluster.groundTlogs) };
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

  /**
   * Collect the ground station's own record of a flight: the tlogs named, the base station's raw
   * observations for the days they touch, and the backpack link metrics.
   *
   * IT DOES NOT WORK OUT WHICH FLIGHT YOU MEANT. `tlog` names what to collect, repeatable; `start`
   * and `end` state the interval for the observations and the metrics, and override what the tlogs
   * imply. It used to infer an armed window from mavproxy's console log and rebuild a tlog
   * filename from it, which filed the wrong flight's tlog once a pod had seen more than one -- and
   * armed begins after the pre-arm window where the RTK problems are (#414).
   *
   * Not per node: none of it is on a vehicle.
   */
  app.post<{
    Params: { flight: string };
    Querystring: { tlog?: string | string[]; start?: string; end?: string };
  }>('/flights/:flight/ground', async (req, reply) => {
    const flight = req.params.flight;
    if (!validFlightName(flight)) {
      return reply.code(400).send({ error: `not a usable flight name: ${JSON.stringify(flight)}` });
    }
    const { start, end } = req.query;
    // Both or neither. One alone is a half-stated interval, and guessing the other end is the
    // habit this route is being cured of.
    if ((start === undefined) !== (end === undefined)) {
      return reply.code(400).send({ error: 'give both start and end, or neither' });
    }
    for (const [k, v] of Object.entries({ start, end })) {
      if (v !== undefined && !Number.isFinite(Date.parse(v))) {
        return reply.code(400).send({ error: `${k} is not a timestamp: ${JSON.stringify(v)}` });
      }
    }
    const tlogs = req.query.tlog === undefined
      ? []
      : Array.isArray(req.query.tlog) ? req.query.tlog : [req.query.tlog];
    // SYNCHRONOUS. A copy between two mounted shares plus one Mimir query: seconds, holding
    // no device, so there is nothing to submit and nothing to follow. The answer is the
    // result, with what was said along the way.
    const notes: string[] = [];
    try {
      const { collected, failed, range } = await collectGround(cfg, flight, {
        flightsDir: cfg.flightsDir,
        tlogs,
        start,
        end,
        note: (line) => notes.push(line),
      });
      for (const l of notes) process.stdout.write(`[fleet-control] ground ${flight}: ${l}\n`);
      return { flight, collected, failed, range, notes };
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message, notes });
    }
  });

  /**
   * The flight's description.
   *
   * A first-class route rather than a UI feature: the usual author is an agent that was told what
   * the flight was for and is writing that down, and the text box is the same call. `NOTES.md` is
   * what docs/flight-data-layout.md already names for it.
   *
   * PUT semantics -- it replaces. A description is the current answer, not a log.
   */
  app.put<{ Params: { flight: string }; Body: { text?: string } | string }>(
    '/flights/:flight/notes',
    async (req, reply) => {
      const flight = req.params.flight;
      if (!validFlightName(flight)) {
        return reply.code(400).send({ error: `not a usable flight name: ${JSON.stringify(flight)}` });
      }
      // Either `{"text": "..."}` or a bare text/markdown body, so `curl --data-binary @notes.md`
      // works without wrapping it in JSON.
      const text = typeof req.body === 'string' ? req.body : req.body?.text;
      if (typeof text !== 'string' || text.trim() === '') {
        return reply.code(400).send({ error: 'text is required' });
      }
      try {
        const bytes = await writeNotes(cfg.flightsDir, flight, text);
        return { flight, file: 'NOTES.md', bytes };
      } catch (err) {
        return reply.code(500).send({ error: (err as Error).message });
      }
    },
  );

  app.get<{ Params: { flight: string } }>('/flights/:flight/notes', async (req, reply) => {
    if (!validFlightName(req.params.flight)) {
      return reply.code(400).send({ error: `not a usable flight name: ${JSON.stringify(req.params.flight)}` });
    }
    const text = await readNotes(cfg.flightsDir, req.params.flight);
    return text === undefined
      ? reply.code(404).send({ error: `no NOTES.md for ${req.params.flight}` })
      : reply.type('text/markdown; charset=utf-8').send(text);
  });

  // ---- the FC's dataflash logs ---------------------------------------------------------
  // The third thing a flight is assembled from, alongside a campod session and the
  // coordinator's. Device side is #384/#395; only the coordinator has an FC.

  /**
   * What the FC holds. Quiesced like everything else, because coordinator-mavlink holds
   * /dev/ttyAMA0 while the stack is up and two readers on one UART get half a stream each.
   *
   * `time_utc` is the FC's LAST-MODIFIED, not creation -- see `fclog.ts`. Anything rendering it
   * says "last written", or the operator picks the wrong log.
   */
  app.get<{ Params: { name: string } }>('/nodes/:name/fc-logs', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    try {
      return { node: node.name, logs: await listLogs(node, cfg.action) };
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

  // ---- what ran, and what is running -----------------------------------------------------
  //
  // Read from the cluster on every call. This service holds no record of a run
  // (coordinator#566): the Workflow is the record, it outlives this pod, and a copy here
  // would be the thing that went stale across a restart. `ttlStrategy` on the templates is
  // how far back this can answer.

  /**
   * Every workflow in the namespace, newest first -- this service's and the bench cycle's
   * alike, because a screen that showed only ours would hide the thing holding the campods.
   */
  app.get('/workflows', async (_req, reply) => {
    try {
      const all = await listWorkflows(cfg.action.workflows);
      return { workflows: all.map((w) => ({ ...w, url: workflowUrl(cfg.action.workflows, w.name) })) };
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

  /**
   * One workflow's steps' output, live, as server-sent events; then `done` with its phase.
   *
   * The same `follow` a converge used to run inside a registry entry, now on demand for
   * whoever asks and holding nothing when they stop. The log is the pods' own, which Alloy
   * also ships to Loki -- so this is the live view and nothing is lost if the stream drops.
   *
   * Named terminal event rather than a bare close, because EventSource RECONNECTS when a
   * server closes the connection and `--tail=-1` would replay the whole log on each attach.
   */
  app.get<{ Params: { name: string } }>('/workflows/:name/log', async (req, reply) => {
    const name = req.params.name;
    const state = await readWorkflow(cfg.action.workflows, name);
    if (state === undefined) {
      return reply.code(404).send({ error: `no such workflow: ${name}` });
    }
    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    const send = (stream: string, line: string) =>
      reply.raw.write(`data: ${JSON.stringify({ t: new Date().toISOString(), stream, line })}\n\n`);
    const stop = new AbortController();
    req.raw.on('close', () => stop.abort());
    // A finished workflow still streams its log: `follow` reads the pods' logs once and the
    // phase poll returns on the first read, so this is the same path with no waiting.
    const end = await follow(cfg.action.workflows, name, send, { signal: stop.signal });
    if (!stop.signal.aborted) {
      reply.raw.write(`event: done\ndata: ${JSON.stringify({ name, phase: end.phase, message: end.message })}\n\n`);
      reply.raw.end();
    }
    return reply;
  });

  return app;
}
