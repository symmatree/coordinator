import { strict as assert } from 'node:assert';
import test from 'node:test';
import { QUIESCE, quiesced } from '../src/quiesce.js';

test('signals dumb-init by exact comm', () => {
  assert.match(QUIESCE, /pkill -x -TERM dumb-init/);
});

test('waits for the processes to actually be gone, not just for the signal', () => {
  // The bug this exists to prevent: pkill returns when the signal is sent. Measured on
  // campod-se, the camera exited 19.0s AFTER the command had already returned.
  assert.match(QUIESCE, /while pgrep -x dumb-init/);
});

test('the wait is bounded, so a stuck container cannot hang the caller', () => {
  assert.match(QUIESCE, /\[ \$i -lt \d+ \]/);
});

test('quiesced() keeps the real command’s exit status, not pkill’s', () => {
  // `;` not `&&`: pkill exits 1 when nothing matched, which is the normal idle case.
  const c = quiesced('coord version');
  assert.ok(c.endsWith('; coord version'));
  assert.ok(!c.includes('&& coord version'));
});

test('quiesced() writes nothing to stdout, so it can prefix a binary stream', () => {
  // offload pipes `cat <bundle>` through this.
  assert.ok(!/echo|printf/.test(QUIESCE));
  assert.match(QUIESCE, /pgrep[^;]*>\/dev\/null/);
});
