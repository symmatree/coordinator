import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { converge } from '../src/ansible.js';

// converge() spawns `ansible-runner`. These tests exercise what this module is actually
// responsible for -- turning the event stream into operator-readable lines, and reporting the
// exit code honestly -- without requiring ansible or a node.

describe('converge -- when ansible-runner is not installed', () => {
  it('says so rather than failing as an unexplained nonzero', async () => {
    await assert.rejects(
      () =>
        converge({
          host: '10.0.0.1',
          user: 'pi',
          privateKeyPath: '/dev/null',
          extraVars: { device_role: 'campod' },
        }),
      /could not run ansible-runner/,
    );
  });
});
