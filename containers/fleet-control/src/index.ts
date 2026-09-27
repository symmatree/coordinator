import { loadConfig } from './config.js';
import { buildServer } from './api.js';
import { build } from './build.js';
import { notify, serviceStarted } from './notify.js';

const cfg = loadConfig();
const app = buildServer(cfg);
await app.listen({ port: cfg.port, host: cfg.host });

// Announce the restart, because a restart is not a hiccup: it ends any run that was in flight.
// After listening, so the notification means "reachable" rather than "starting".
const [title, body] = serviceStarted(build());
void notify(cfg.notify, title, body);

for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.on(sig, () => {
    void app.close().then(() => process.exit(0));
  });
}
