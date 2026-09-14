import { loadConfig } from './config.js';
import { buildServer } from './api.js';

const cfg = loadConfig();
const app = buildServer(cfg);
await app.listen({ port: cfg.port, host: cfg.host });

for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.on(sig, () => {
    void app.close().then(() => process.exit(0));
  });
}
