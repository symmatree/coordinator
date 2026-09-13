import { loadConfig } from './config.js';
import { buildServer } from './api.js';

const cfg = loadConfig();
const app = buildServer(cfg);

if (cfg.ssh.hostKeys.ephemeral) {
  app.log.warn(
    'host-key store is not persistable: every restart is a fresh first-contact and host-key ' +
      'verification protects nothing. Mount a writable volume at the FLEET_HOSTKEYS path.',
  );
}

await app.listen({ port: cfg.port, host: cfg.host });

for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.on(sig, () => {
    void app.close().then(() => process.exit(0));
  });
}
