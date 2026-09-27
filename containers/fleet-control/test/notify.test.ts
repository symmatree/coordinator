import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { RunRegistry } from '../src/runs.js';
import { notify, runEnded, serviceStarted } from '../src/notify.js';

const settle = () => new Promise((r) => setTimeout(r, 30));

/** A one-shot apprise stand-in that records what it was sent. */
function listener(status = 200) {
  const got: unknown[] = [];
  const srv = createServer((req, res) => {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      got.push(JSON.parse(body));
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end('{}');
    });
  });
  return { got, srv, url: () => `http://127.0.0.1:${(srv.address() as { port: number }).port}/notify` };
}

test('a notification carries the tag, because an untagged apprise notify reaches nobody', async () => {
  const l = listener();
  await new Promise<void>((r) => l.srv.listen(0, '127.0.0.1', r));
  const [title, body] = runEnded('converge', 'campod-sw', 'succeeded', 'PLAY RECAP ok=58');
  await notify({ url: l.url(), tag: 'tiles' }, title, body);
  l.srv.close();
  assert.equal(l.got.length, 1);
  const sent = l.got[0] as { title: string; body: string; tag: string };
  assert.equal(sent.tag, 'tiles');
  assert.match(sent.title, /campod-sw: converge succeeded/);
  assert.match(sent.body, /PLAY RECAP ok=58/);
});

test('notify NEVER throws -- a converge that worked and could not be announced still worked', async () => {
  // Nothing listening at all.
  await notify({ url: 'http://127.0.0.1:1/notify', tag: 'tiles' }, 'x', 'y');
  // Listening and refusing.
  const l = listener(503);
  await new Promise<void>((r) => l.srv.listen(0, '127.0.0.1', r));
  await notify({ url: l.url(), tag: 'tiles' }, 'x', 'y');
  l.srv.close();
});

test('an empty url disables notification without pretending to send', async () => {
  await notify({ url: '', tag: 'tiles' }, 'x', 'y');   // resolves, sends nothing, no throw
});

test('the registry announces a run ending, server-side', async () => {
  const seen: string[] = [];
  const runs = new RunRegistry(() => {}, (run) => seen.push(`${run.node}:${run.action}:${run.status}`));
  runs.start('converge', 'campod-sw', async () => {});
  runs.start('reboot', 'coordinator', async () => { throw new Error('nope'); });
  await settle();
  assert.deepEqual(seen.sort(), ['campod-sw:converge:succeeded', 'coordinator:reboot:failed']);
});

test('a throwing notifier does not break the run it was reporting on', async () => {
  const runs = new RunRegistry(() => {}, () => { throw new Error('apprise exploded'); });
  const run = runs.start('converge', 'campod-sw', async () => {});
  await settle();
  // The run still reached its terminal state and is still queryable.
  assert.equal(runs.get(run.id)?.status, 'succeeded');
  assert.ok(runs.get(run.id)?.endedAt);
});

test('a restart is announced as the build, since that is the reason to care', () => {
  const [title, body] = serviceStarted({
    unit: 'fleet-control', source: 'https://github.com/symmatree/coordinator',
    revision: '4ee3e86c1dabcdef', refName: 'refs/heads/main',
    startedAt: new Date().toISOString(), uptimeSec: 0,
  });
  assert.match(title, /started/);
  assert.match(body, /build 4ee3e86c1d \(refs\/heads\/main\)/);
  // The consequence is the point: a restart ends whatever was running.
  assert.match(body, /in flight was ended by this restart/);
});

test('a local build with no manifest is announced as such rather than as a blank revision', () => {
  const [, body] = serviceStarted({ startedAt: new Date().toISOString(), uptimeSec: 0 });
  assert.match(body, /no build manifest/);
});
