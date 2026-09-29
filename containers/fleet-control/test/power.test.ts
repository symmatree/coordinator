import { test } from 'node:test';
import assert from 'node:assert/strict';
import { configured, powerState, setPower, type PowerConfig } from '../src/power.js';

const CFG: PowerConfig = {
  url: 'https://homeassistant.local.symmatree.com:8123',
  token: 'tok',
  entity: 'switch.christmas_tree',
};

/** Stand in for Home Assistant. Records what was asked, answers what it is told to. */
function fakeHa(reply: (url: URL, init: RequestInit) => { status?: number; body: unknown }) {
  const seen: { url: string; method: string; auth?: string; body?: string }[] = [];
  const real = globalThis.fetch;
  globalThis.fetch = (async (u: URL, init: RequestInit = {}) => {
    const h = (init.headers ?? {}) as Record<string, string>;
    seen.push({ url: u.toString(), method: init.method ?? 'GET', auth: h.authorization, body: init.body as string });
    const r = reply(u, init);
    return {
      ok: (r.status ?? 200) < 400,
      status: r.status ?? 200,
      json: async () => r.body,
      text: async () => JSON.stringify(r.body),
    } as unknown as Response;
  }) as typeof fetch;
  return { seen, restore: () => { globalThis.fetch = real; } };
}

test('an unset switch or url disables the feature rather than guessing one', () => {
  // Which plug feeds the bench is a fact about the house. A default here would switch something
  // else, so absent means off -- the same rule an empty notify url follows.
  assert.equal(configured(CFG), true);
  assert.equal(configured({ ...CFG, entity: '' }), false);
  assert.equal(configured({ ...CFG, url: '' }), false);
});

test('the state is read from HA, with the bearer token', async () => {
  const ha = fakeHa(() => ({ body: {
    state: 'off', last_changed: '2026-09-29T18:00:00+00:00',
    attributes: { friendly_name: 'Christmas tree' },
  } }));
  try {
    const s = await powerState(CFG);
    assert.equal(s.state, 'off');
    assert.equal(s.name, 'Christmas tree');
    assert.equal(s.entity, 'switch.christmas_tree');
    assert.equal(ha.seen[0]?.method, 'GET');
    assert.match(ha.seen[0]?.url ?? '', /\/api\/states\/switch\.christmas_tree$/);
    assert.equal(ha.seen[0]?.auth, 'Bearer tok');
  } finally { ha.restore(); }
});

test('turning it on calls the service and then RE-READS, rather than assuming', async () => {
  // "I pressed on and it is still off" has to be visible. Asserting the call worked would hide it.
  const ha = fakeHa((u) => u.pathname.startsWith('/api/services')
    ? { body: [] }
    : { body: { state: 'on', attributes: { friendly_name: 'Christmas tree' } } });
  try {
    const s = await setPower(CFG, true);
    assert.equal(s.state, 'on');
    assert.equal(ha.seen.length, 2);
    assert.match(ha.seen[0]?.url ?? '', /\/api\/services\/switch\/turn_on$/);
    assert.equal(ha.seen[0]?.method, 'POST');
    assert.equal(ha.seen[0]?.body, JSON.stringify({ entity_id: 'switch.christmas_tree' }));
    assert.match(ha.seen[1]?.url ?? '', /\/api\/states\//);
  } finally { ha.restore(); }
});

test('off calls turn_off, not turn_on', async () => {
  const ha = fakeHa((u) => u.pathname.startsWith('/api/services') ? { body: [] } : { body: { state: 'off' } });
  try {
    await setPower(CFG, false);
    assert.match(ha.seen[0]?.url ?? '', /turn_off$/);
  } finally { ha.restore(); }
});

test('a rejected token and an unknown entity say which, because the fixes differ', async () => {
  for (const [status, re] of [[401, /rejected the token/], [404, /no switch\.christmas_tree/]] as const) {
    const ha = fakeHa(() => ({ status, body: {} }));
    try {
      await assert.rejects(() => powerState(CFG), re);
    } finally { ha.restore(); }
  }
});

test('a state HA does not call on or off is reported as it is, not coerced', async () => {
  // `unavailable` is a real answer -- the plug is unreachable, which is different from being off
  // and must not be shown as off.
  const ha = fakeHa(() => ({ body: { state: 'unavailable' } }));
  try {
    assert.equal((await powerState(CFG)).state, 'unavailable');
  } finally { ha.restore(); }
});
