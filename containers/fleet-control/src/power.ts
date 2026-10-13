// The bench's power, through Home Assistant.
//
// #223 asks for "ask for an update, it powers things up, waits for connections, runs the update,
// and powers back down". This is the first half: the switch. Everything after it already exists --
// the status probe says when the devices answer, and it quiesces them on its way past.
//
// Home Assistant is an appliance on the house LAN, not a workload here, so this is plain HTTP to
// it. A cluster pod reaches it: `homeassistant.local.symmatree.com` resolves to 10.0.99.9 and the
// API answers, checked from a pod on 2026-09-28. No Kubernetes identity is involved.
//
// WHAT IS SWITCHED IS NOT OUR BUSINESS, AND NEITHER IS ITS DOMAIN. Calls go through
// `homeassistant.turn_on`/`turn_off`, documented as acting "across any domain in a single
// action", so the entity is a name this service passes through. An earlier version called
// `switch/turn_on` and would not have driven a plug that is not a `switch.`.

export interface PowerConfig {
  /** HA's base URL. Empty disables the whole feature, the way an empty notify url does. */
  url: string;
  /** Long-lived access token. */
  token: string;
  /** The entity to operate, whatever its domain -- `switch.x`, `light.x`. Empty disables. */
  entity: string;
}

export interface PowerState {
  entity: string;
  /** `on`, `off`, or whatever HA says -- `unavailable` is a real answer and is reported as one. */
  state: string;
  /** HA's friendly name, so the screen can say what it is switching. */
  name?: string;
  changedAt?: string;
}

export const configured = (cfg: PowerConfig): boolean => cfg.url !== '' && cfg.entity !== '';

/** One call to HA. Errors carry what it said, because 401 and 404 need different fixes. */
async function ha(cfg: PowerConfig, path: string, body?: unknown): Promise<unknown> {
  const res = await fetch(new URL(path, cfg.url), {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      authorization: `Bearer ${cfg.token}`,
      ...(body === undefined ? {} : { 'content-type': 'application/json' }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(15_000),
  });
  if (res.status === 401) throw new Error('home assistant rejected the token (401)');
  // 404 on a state read means the entity does not exist. The service calls do not 404 on a bad
  // entity -- HA accepts the call and does nothing -- which is why `setPower` re-reads rather
  // than trusting the call, and why a wrong entity id shows up as "pressed on, still off".
  if (res.status === 404) throw new Error(`home assistant has no ${cfg.entity} (404)`);
  if (!res.ok) throw new Error(`home assistant ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

/**
 * What the switch says it is.
 *
 * READ, NOT REMEMBERED. Anything can operate this plug -- the HA app, a wall button, an
 * automation -- so a state this service cached would be a guess dressed as a fact.
 */
export async function powerState(cfg: PowerConfig): Promise<PowerState> {
  const s = await ha(cfg, `/api/states/${encodeURIComponent(cfg.entity)}`) as {
    state?: string; last_changed?: string; attributes?: { friendly_name?: string };
  };
  return {
    entity: cfg.entity,
    state: s.state ?? 'unknown',
    name: s.attributes?.friendly_name,
    changedAt: s.last_changed,
  };
}

/**
 * Turn it on or off, and report what it is afterwards.
 *
 * The state comes from a fresh read rather than from assuming the call worked, so "I pressed on
 * and it is still off" is visible instead of being asserted away.
 */
export async function setPower(cfg: PowerConfig, on: boolean): Promise<PowerState> {
  await ha(cfg, `/api/services/homeassistant/turn_${on ? 'on' : 'off'}`, { entity_id: cfg.entity });
  return powerState(cfg);
}
