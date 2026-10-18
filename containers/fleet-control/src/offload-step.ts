// The body of a collect workflow's offload step: one node's session, moved in its own pod.
//
// An entry point rather than a route because the bytes are the point. A session is hundreds
// of megabytes and minutes of transfer, and doing it inside the service meant the two campods
// went one after the other -- 18:23 and 18:30 on 2026-10-18, for 336 and 361 MB -- bounded by
// that pod's lifetime and recorded only in its memory. In a step pod they move at once, the
// log is the pod's own and outlives it, and a service restart does not touch a transfer.

import { loadConfig } from './config.js';
import { findNode } from './inventory.js';
import { offloadSession, validFlightName } from './offload.js';

const [nodeName, session, flight] = process.argv.slice(2);

if (!nodeName || !session || !flight) {
  console.error('usage: offload-step <node> <session> <flight>');
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

console.log(`[step] offloading ${session} from ${nodeName} into ${flight}`);

const collected = await offloadSession(node, cfg.action, session, flight, {
  flightsDir: cfg.flightsDir,
  // The same default the route had: cards fill at about 6 GB/hour and nothing else frees
  // them. Deletion still happens only after the device's own digest matches.
  deleteAfter: process.env.OFFLOAD_KEEP !== 'true',
  note: (l) => console.log(`[step] ${l}`),
});

console.log(
  `[step] ${nodeName}: ${collected.file} (${(collected.bytes / 1e6).toFixed(0)} MB, ` +
    `source ${collected.sourceDeleted ? 'deleted' : 'kept'})`,
);
