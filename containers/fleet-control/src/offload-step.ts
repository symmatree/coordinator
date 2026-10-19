// The body of a collect step: one retrieval, in a pod of its own.
//
// An entry point rather than a route because the bytes are the point. Done inside the
// service these ran one after another, bounded by that pod's lifetime and recorded only in
// its memory -- two campod sessions at 18:23 and 18:30 on 2026-10-18 for 336 and 361 MB,
// and an FC log that is worse: 148 MB took about 30 minutes on 2026-09-23, holding
// /dev/ttyAMA0 with the stack down the whole time. `fc-log-50.bin.part`, zero bytes, has
// been on the share since 2026-09-26 -- a pull that went away with whatever was running it.
//
// In a step pod each goes at the same time as the others, the log is the pod's own and
// outlives it, and a service restart does not reach into a transfer.
//
// ONE STEP BODY FOR BOTH KINDS. A session and an FC log differ in which function moves the
// bytes and in nothing else: same node, same key, same flight, same record written to
// `collected/`. Two templates would have been two of everything to keep in step.

import { loadConfig } from './config.js';
import { findNode } from './inventory.js';
import { offloadFcLog, offloadSession, validFlightName } from './offload.js';

const [nodeName, kind, id, flight] = process.argv.slice(2);

if (!nodeName || !kind || !id || !flight) {
  console.error('usage: offload-step <node> <session|fc-log> <id> <flight>');
  process.exit(2);
}
if (!validFlightName(flight)) {
  console.error(`[step] ${flight} is not a usable flight name`);
  process.exit(2);
}

const cfg = loadConfig();
const node = findNode(cfg.inventory, nodeName);
if (!node) {
  console.error(`[step] no such node in the roster: ${nodeName}`);
  process.exit(2);
}

const opts = {
  flightsDir: cfg.flightsDir,
  // The same default the route had: cards fill at about 6 GB/hour and nothing else frees
  // them. Deletion still happens only after the device's own digest matches. An FC log is
  // never deleted either way -- `offloadFcLog` ignores this, and says why.
  deleteAfter: process.env.OFFLOAD_KEEP !== 'true',
  note: (l: string) => console.log(`[step] ${l}`),
};

console.log(`[step] ${kind} ${id} from ${nodeName} into ${flight}`);

let collected;
if (kind === 'session') {
  collected = await offloadSession(node, cfg.action, id, flight, opts);
} else if (kind === 'fc-log') {
  const logId = Number(id);
  if (!Number.isInteger(logId)) {
    console.error(`[step] an FC log id is a number, not ${id}`);
    process.exit(2);
  }
  collected = await offloadFcLog(node, cfg.action, logId, flight, opts);
} else {
  console.error(`[step] ${kind} is not a kind of retrieval; expected session or fc-log`);
  process.exit(2);
}

console.log(
  `[step] ${nodeName}: ${collected.file} (${(collected.bytes / 1e6).toFixed(0)} MB, ` +
    `source ${collected.sourceDeleted ? 'deleted' : 'kept'})`,
);
