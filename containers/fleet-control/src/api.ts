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
import { offloadFcLog, offloadSession, readNotes, validFlightName, writeNotes } from './offload.js';
import { enrich, LookupCache, repoFromUrl } from './status.js';
import { eventFiles, eventLines, isRunId, logChunks } from './runartifacts.js';
import { collectGround, listTlogs } from './cluster.js';
import { configured, powerState, setPower } from './power.js';
import { build } from './build.js';
import { notify, runEnded } from './notify.js';
import { commitTitle, isHeadOfRef, listArtifacts, listBuilds, refHead, registryImage } from './github.js';

export function buildServer(
  cfg: Config,
  // A run's ending is announced from HERE, not from the page: the point of a notification is that
  // it reaches you when no browser is attached.
  runs = new RunRegistry(echoToProcess, (run) => {
    const [title, body] = runEnded(run.action, run.node, run.status, run.lines.at(-1)?.line);
    void notify(cfg.notify, title, body);
  }),
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
      const run = runs.start('stop', node.name, (emit) => stop(node, cfg.action, sinkFor(emit)));
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
      const run = runs.start('reboot', node.name, (emit) => {
        const say = sinkFor(emit);
        // Said in the reboot's own log, because that is the run someone reads afterwards when
        // wondering where the converge went.
        for (const r of ended) say('stderr', `abandoned ${r.action} (run ${r.id.slice(0, 8)}), which this reboot ends`);
        return reboot(node, cfg.action, say);
      });
      return reply.code(202).send({
        id: run.id, action: run.action, node: run.node,
        abandoned: ended.map((r) => ({ id: r.id, action: r.action })),
      });
    } catch (err) {
      return reply.code(409).send({ error: (err as Error).message });
    }
  });

  // ---- the bench's power ----------------------------------------------------------------
  //
  // Not a run: one HTTP call to Home Assistant, done by the time the request returns. And not
  // per node -- one plug feeds the whole bench (#422).

  /**
   * What the switch is, read from HA rather than remembered. Anything can operate that plug --
   * the HA app, a wall button, an automation -- so a cached answer would be a guess.
   */
  app.get('/power', async (_req, reply) => {
    if (!configured(cfg.power)) return { configured: false };
    try {
      return { configured: true, ...await powerState(cfg.power) };
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

  /**
   * Turn the bench on or off.
   *
   * OFF IS NEVER REFUSED. It is confirmed on the screen, and the confirmation names any run in
   * flight, but it is not gated on one: cutting power is a way out of a stuck box, and the same
   * mistake as #424 would be to take the control away for the case it is wanted in. The devices
   * are on btrfs for this (#41); what a cut costs is a transfer in progress, which is what the
   * confirmation is for.
   */
  app.post<{ Params: { how: string } }>('/power/:how', async (req, reply) => {
    const how = req.params.how;
    if (how !== 'on' && how !== 'off') {
      return reply.code(400).send({ error: `power is on or off, not ${JSON.stringify(how)}` });
    }
    if (!configured(cfg.power)) {
      return reply.code(501).send({ error: 'no switch configured -- set FLEET_HA_SWITCH and FLEET_HA_TOKEN' });
    }
    try {
      return await setPower(cfg.power, how === 'on');
    } catch (err) {
      return reply.code(502).send({ error: (err as Error).message });
    }
  });

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
      const run = runs.start('ground', flight, (emit) =>
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

  /**
   * Pull one log into a flight directory. Its own run, because it holds the serial port with
   * the stack down for as long as it takes -- ~30 min for 148 MB at the measured 84 KiB/s --
   * which is a different operational state from the minutes everything else takes.
   */
  app.post<{ Params: { name: string }; Querystring: { id?: string; flight?: string } }>(
    '/nodes/:name/fc-log',
    async (req, reply) => {
      const node = findNode(cfg.inventory, req.params.name);
      if (!node) return reply.code(404).send({ error: `no such node: ${req.params.name}` });
      const id = Number(req.query.id);
      if (!Number.isInteger(id) || id < 0) return reply.code(400).send({ error: 'id must be a log id' });
      const flight = req.query.flight ?? '';
      if (!validFlightName(flight)) {
        return reply.code(400).send({ error: `not a usable flight name: ${JSON.stringify(flight)}` });
      }
      try {
        const run = runs.start('fc-log', node.name, (emit) =>
          offloadFcLog(node, cfg.action, id, flight, {
            flightsDir: cfg.flightsDir,
            note: (line) => sinkFor(emit)('stdout', line),
          }).then(() => undefined),
        );
        return reply.code(202).send({ id: run.id, action: run.action, node: run.node, log: id, flight });
      } catch (err) {
        return reply.code(409).send({ error: (err as Error).message });
      }
    },
  );

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
