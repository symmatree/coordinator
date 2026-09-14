import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { parseInventory, findNode, hostOf } from '../src/inventory.js';

const ok = {
  nodes: [
    { name: 'coordinator', role: 'coordinator' },
    { name: 'campod-se', role: 'campod', host: '10.0.5.237' },
  ],
};

describe('inventory', () => {
  it('defaults the user to pi', () => {
    assert.equal(parseInventory(ok).user, 'pi');
  });

  it('takes a host when given, and falls back to the name', () => {
    const inv = parseInventory(ok);
    assert.equal(hostOf(findNode(inv, 'campod-se')!), '10.0.5.237');
    assert.equal(hostOf(findNode(inv, 'coordinator')!), 'coordinator');
  });

  it('rejects an unknown role rather than connecting with the wrong one', () => {
    assert.throws(() => parseInventory({ nodes: [{ name: 'x', role: 'router' }] }));
  });

  it('rejects an empty roster', () => {
    assert.throws(() => parseInventory({ nodes: [] }));
  });

  it('rejects a node with no name', () => {
    assert.throws(() => parseInventory({ nodes: [{ role: 'campod' }] }));
  });
});
