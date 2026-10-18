// The body of a collect workflow's join step: compose `flight.json` once both offloads are done.
//
// The offload steps each write their own file under `collected/` and recompose the summary as
// they go, so this is not what makes the record correct -- it is what makes it FINAL. Two
// steps finishing at once can each compose from a view that is missing the other; one pass
// after both have stopped writing cannot.
//
// Runs even if an offload failed, because a flight that is short one campod still has the
// other's bundle on the share, and a summary naming it is how that is visible.

import { loadConfig } from './config.js';
import { composeFlight, validFlightName } from './offload.js';

const [flight] = process.argv.slice(2);

if (!flight) {
  console.error('usage: collect-join <flight>');
  process.exit(2);
}
if (!validFlightName(flight)) {
  console.error(`[join] ${flight} is not a usable flight name`);
  process.exit(2);
}

const cfg = loadConfig();
const record = await composeFlight(cfg.flightsDir, flight);

for (const c of record.collected) {
  console.log(
    `[join] ${c.node} ${c.file} ${(c.bytes / 1e6).toFixed(0)} MB ` +
      `source ${c.sourceDeleted ? 'deleted' : 'kept'}`,
  );
}
console.log(`[join] ${flight}: ${record.collected.length} contribution(s) in flight.json`);
