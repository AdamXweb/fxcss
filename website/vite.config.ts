import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { defineConfig } from 'vite';

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === 'seatbelt';

// The Cloudflare Worker that serves fxcss.com. The Cloudflare plugin writes
// this into dist/server/wrangler.json, which `npm run deploy` publishes. The
// site has no database or storage, so the Worker has no bindings. Workers Logs
// stay on because /open says requests and errors are logged for up to 7 days;
// change that page if you turn them off.
//
// The fxcss.com and www.fxcss.com custom domains are passed by the deploy
// script (package.json), not listed here: with routes in this config,
// `wrangler dev` treats every local request as http://fxcss.com, which
// proxy.ts then redirects to https, so `npm start` and the production check
// could not run locally.
const workerConfig = {
  name: 'fxcss-website',
  main: 'vinext/server/fetch-handler',
  compatibility_date: '2026-09-30',
  compatibility_flags: ['nodejs_compat'],
  observability: { enabled: true },
};

export default defineConfig(async () => {
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= 'false';
  process.env.WRANGLER_LOG_PATH ??= '.wrangler/logs';
  process.env.MINIFLARE_REGISTRY_PATH ??= '.wrangler/registry';

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import('@cloudflare/vite-plugin');

  return {
    css: { postcss: { plugins: [tailwindcss()] } },
    server: isCodexSeatbeltSandbox
      ? { watch: { useFsEvents: false, usePolling: true } }
      : undefined,
    plugins: [
      vinext(),
      cloudflare({
        viteEnvironment: { name: 'rsc', childEnvironments: ['ssr'] },
        config: workerConfig,
      }),
    ],
  };
});
