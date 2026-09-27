import { test } from 'node:test';
import assert from 'node:assert/strict';
import { armedWindow, settingsValue } from '../src/cluster.js';

// Shaped like `kubectl logs --timestamps` on mavproxy, with the lines observed on 2026-09-23.
const CONSOLE = [
  '2026-09-23T11:58:47.101Z Detected vehicle 1:1 on link 0',
  '2026-09-23T11:59:00.220Z NTRIP started',
  '2026-09-23T12:03:17.880Z link 1 down',
  '2026-09-23T12:08:48.010Z link 1 OK',
  '2026-09-23T12:09:11.700Z ARMED',
  '2026-09-23T12:10:02.400Z Mode ALT_HOLD',
  '2026-09-23T12:11:50.800Z DISARMED',
  '2026-09-23T12:12:09.300Z link 1 down',
].join('\n');

test('the armed window comes from the console, which is the one clock that does not lie', () => {
  // Neither device has an RTC, so nothing on the vehicle can establish this -- which is why
  // selecting sessions and FC logs is a ground-side act (#385).
  const w = armedWindow(CONSOLE);
  assert.equal(w.armed, '2026-09-23T12:09:11.700Z');
  assert.equal(w.disarmed, '2026-09-23T12:11:50.800Z');
});

test('DISARMED is not read as ARMED', () => {
  // `\bARMED\b` matches inside DISARMED, so the disarm line would otherwise set both.
  const w = armedWindow('2026-09-23T12:11:50.800Z DISARMED');
  assert.equal(w.armed, undefined);
  assert.equal(w.disarmed, '2026-09-23T12:11:50.800Z');
});

test('the FIRST arm wins and the LAST disarm wins', () => {
  // A pod's console can span several flights. The window has to bracket the whole thing rather
  // than describe whichever event was seen last.
  const w = armedWindow([
    '2026-09-23T10:00:00Z ARMED',
    '2026-09-23T10:05:00Z DISARMED',
    '2026-09-23T12:09:11Z ARMED',
    '2026-09-23T12:11:50Z DISARMED',
  ].join('\n'));
  assert.equal(w.armed, '2026-09-23T10:00:00Z');
  assert.equal(w.disarmed, '2026-09-23T12:11:50Z');
});

test('a console with no arm reports none rather than guessing', () => {
  const w = armedWindow(CONSOLE.split('\n').filter((l) => !/ARMED/.test(l)).join('\n'));
  assert.equal(w.armed, undefined);
  assert.equal(w.disarmed, undefined);
});

test('settings.conf values survive quoting', () => {
  // rtkbase writes these quoted; `position` is why any of this matters -- PPK is not possible
  // without the base coordinates -- and `datadir` is on a separate mount, so it is read rather
  // than guessed at.
  const conf = [
    '# rtkbase',
    "position='45.1234567 -122.7654321 123.456'",
    'datadir="/home/rtkbase/data"',
    'local_ntripc_msg=1005,1077,1087',
    'rtcm_msg_a = 1004,1005',
  ].join('\n');
  assert.equal(settingsValue(conf, 'position'), '45.1234567 -122.7654321 123.456');
  assert.equal(settingsValue(conf, 'datadir'), '/home/rtkbase/data');
  assert.equal(settingsValue(conf, 'rtcm_msg_a'), '1004,1005');
  assert.equal(settingsValue(conf, 'nope'), undefined);
});

test('a settings.conf with no position says so rather than returning empty', () => {
  assert.equal(settingsValue('datadir=/x', 'position'), undefined);
});
