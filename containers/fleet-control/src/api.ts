// The route surface. The UI is one client of it; anything the page can do, curl can do.

import { createReadStream, readFileSync } from 'node:fs';
import { Readable } from 'node:stream';
import Fastify, { type FastifyInstance } from 'fastify';
import type { Config } from './config.js';
import { findNode } from './inventory.js';
import { RunRegistry, echoToProcess, sinkFor } from './runs.js';
import { converge, reboot, reimage, stop } from './actions.js';
import { ImageCache } from './imagecache.js';
import { probeAll, probeNode } from './probe.js';
import { deleteSessions, listSessions, stopCapture } from './sessions.js';
import { listLogs } from './fclog.js';
import { readNotes, validFlightName, writeNotes } from './offload.js';
import { enrich, LookupCache, repoFromUrl } from './status.js';
import { collectGround, listTlogs } from './cluster.js';
import { configured as busConfigured, readPods, setDesired } from './bus.js';
import { findByRun, nodeBusyWith, readWorkflow, submitCollect, workflowUrl, type CollectTarget } from './workflows.js';
import { build } from './build.js';
import { notify, runEnded } from './notify.js';
import { commitTitle, isHeadOfRef, listArtifacts, listBuilds, refHead, registryImage } from './github.js';

export function buildServer(
  cfg: Config,
  // A run's ending is announced from HERE, not from the page: the point of a notification is that
  // it reaches you when no browser is attached.
  runs = new RunRegistry(
    echoToProcess,
    (run) => {
      const [title, body] = runEnded(run.action, run.node, run.status, run.lines.at(-1)?.line);
      void notify(cfg.notify, title, body);
    },
    (node) => nodeBusyWith(cfg.action.workflows, node),
  ),
): FastifyInstance {
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
   * Config, stack definitions and image pulls. The routine one, and the default.
   */
  app.post<{ Params: { name: string } }>('/nodes/:name/converge', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    try {
      const run = await runs.start('converge', node.name, (emit, runId) =>
        converge(node, cfg.action, { runId, kind: 'converge' }, sinkFor(emit)));
      return reply.code(202).send({ id: run.id, action: run.action, node: run.node });
    } catch (err) {
      return reply.code(409).send({ error: (err as Error).message });
    }
  });

  /**
   * A fresh card, end to end -- `site.yaml`, provision then deploy, because a bare card needs
   * both. Reboots partway, at the hand-off between them.
   */
  app.post<{ Params: { name: string } }>(
    '/nodes/:name/provision',
    async (req, reply) => {
      const node = findNode(cfg.inventory, req.params.name);
      if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
      try {
        const run = await runs.start('provision', node.name, (emit, runId) =>
          converge(node, cfg.action, { runId, kind: 'provision' }, sinkFor(emit)));
        return reply.code(202).send({ id: run.id, action: run.action, node: run.node });
      } catch (err) {
        return reply.code(409).send({ error: (err as Error).message });
      }
    },
  );

  /**
   * Stop the container set, and nothing else.
   *
   * Its own route rather than a flag on converge, because the operator wants it on its own:
   * a converge is twenty minutes of apt to achieve a stop, and a card that has never been
   * converged can still be stopped -- a signal needs nothing installed.
   */
  app.post<{ Params: { name: string } }>('/nodes/:name/stop', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    try {
      const run = await runs.start('stop', node.name, (emit) => stop(node, cfg.action, sinkFor(emit)));
      return reply.code(202).send({ id: run.id, action: run.action, node: run.node });
    } catch (err) {
      return reply.code(409).send({ error: (err as Error).message });
    }
  });

  /**
   * Stop the stack and reboot. Returns once the device has taken the request.
   *
   * Nothing waits for it to come back -- the status screen is the check, as for everything
   * else here. A stop that fails does not stop the reboot: being unable to quiesce is one of
   * the states this gets pressed in.
   */
  app.post<{ Params: { name: string } }>('/nodes/:name/reboot', async (req, reply) => {
    const node = findNode(cfg.inventory, req.params.name);
    if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
    // A REBOOT IS NEVER REFUSED FOR BEING BUSY, because busy is the case it exists for. A
    // converge that wedged held this node's slot, and the one action that would have got the box
    // back to a known state was the one the lock blocked -- both here and on the screen, which
    // disabled the button (#424).
    //
    // Abandoning first is what frees the slot, so there is no second path around the lock to
    // keep working: the runs are ended because the reboot is about to end them, which is true.
    const ended = runs.abandonFor(node.name, `[fleet-control] abandoned: rebooting ${node.name}`);
    try {
      const run = await runs.start('reboot', node.name, (emit) => {
        const say = sinkFor(emit);
        // Said in the reboot's own log, because that is the run someone reads afterwards when
        // wondering where the converge went.
        for (const r of ended) say('stderr', `abandoned ${r.action} (run ${r.id.slice(0, 8)}), which this reboot ends`);
        return reboot(node, cfg.action, say);
      }, { whateverIsRunning: true });
      return reply.code(202).send({
        id: run.id, action: run.action, node: run.node,
        abandoned: ended.map((r) => ({ id: r.id, action: r.action })),
      });
    } catch (err) {
      return reply.code(409).send({ error: (err as Error).message });
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
        // Counts against the playbook cap for the same reason converge does -- reimage.yaml
        // is a play, so it is an ansible interpreter in this pod.
        const run = await runs.start('reimage', node.name, (emit, runId) =>
          reimage(node, cfg.action, { runId, image }, sinkFor(emit)));
        return reply.code(202).send({ id: run.id, action: run.action, node: run.node, image });
      } catch (err) {
        return reply.code(409).send({ error: (err as Error).message });
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
    try {
      const run = await runs.start('ground', flight, (emit) =>
        collectGround(cfg, flight, {
          flightsDir: cfg.flightsDir,
          tlogs,
          start,
          end,
          note: (line) => sinkFor(emit)('stdout', line),
        }).then(({ collected, failed, range }) => {
          const say = sinkFor(emit);
          say('stdout', `collected ${collected.length}: ${collected.map((c) => c.file).join(', ')}`);
          for (const f of failed) say('stderr', `not collected -- ${f}`);
          say('stdout', range === undefined
            ? 'no range, so no backpack series'
            : `range ${range.start} -> ${range.end} (${range.from})`);
        }),
      );
      return reply.code(202).send({ id: run.id, action: run.action, flight, tlogs });
    } catch (err) {
      return reply.code(409).send({ error: (err as Error).message });
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

  app.get('/runs', async () =>
    runs.list().map(({ lines, ...rest }) => ({ ...rest, lineCount: lines.length })),
  );

  /**
   * One run -- and when this process has never heard of it, where the work actually is.
   *
   * The registry is in memory, so a pod roll loses every run in it while the Workflow each
   * one submitted carries on in a pod of its own. The run id is then the only handle a
   * caller has left, because the link was in the lines that died with the registry.
   *
   * Still a 404: this service does not have that run. It does not invent one either --
   * reporting a Workflow's state in the registry's vocabulary would be a second, worse
   * account of something the workflow already describes. What it adds is the link, so the
   * answer is "not here, and here is where it is" rather than "no".
   */
  app.get<{ Params: { id: string } }>('/runs/:id', async (req, reply) => {
    const run = runs.get(req.params.id);
    if (run) return run;

    const found = await findByRun(cfg.action.workflows, req.params.id).catch(() => undefined);
    return reply.code(404).send({
      error: `no such run: ${req.params.id}`,
      ...(found === undefined
        ? {}
        : { workflow: workflowUrl(cfg.action.workflows, found.name) }),
    });
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
