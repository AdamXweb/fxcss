# Hosting fxcss.com

## Current state

The source and production build are prepared for `https://fxcss.com`. They include the interactive shell installer at `/install.sh`, canonical page URLs, `/sitemap.xml`, and `/robots.txt`. The installer is a public static asset with a plain-text content type, revalidation on every request, and no sign-in requirement in the application.

The site runs as a Cloudflare Worker named `fxcss-website`. `vite.config.ts` holds its configuration (the name, the compatibility date and Workers Logs), and the `deploy` script in `package.json` attaches the `fxcss.com` and `www.fxcss.com` custom domains. It has no database, storage or secrets. `npm run build` writes the Worker (`dist/server/index.js`), its static files (`dist/client/`) and the generated `dist/server/wrangler.json`, and `npm run deploy` publishes them. Don't edit the generated file; change `vite.config.ts` and rebuild.

The domain's public nameservers were `dorthy.ns.cloudflare.com` and `scott.ns.cloudflare.com` when checked on 2026-09-05, so the zone is on Cloudflare. No apex A record was returned. Nothing has been deployed yet.

## Launch sequence

1. Run the Website checks and build from `website/`:

   ```sh
   npm ci
   npm run content
   npm run typecheck
   npm run lint
   npm test
   npm run test:installer
   bash -n public/install.sh
   shellcheck public/install.sh
   npm run build
   ```

   `content/docs.json` and `content/evidence.json` are already generated. Their recorded inputs in `content/source/` make the website independently buildable; working in the full repository refreshes those inputs from the root README and package version.

2. Check the deploy without publishing anything. This needs no Cloudflare login:

   ```sh
   npm run deploy -- --dry-run
   ```

3. Sign in to the Cloudflare account that holds the `fxcss.com` zone, with `npx wrangler login` (browser sign-in) or an API token made from Cloudflare's "Edit Cloudflare Workers" template. Then publish:

   ```sh
   npm run deploy
   ```

   Wrangler creates the `fxcss-website` Worker and attaches both custom domains. Cloudflare adds their DNS records and certificates itself, so don't create A or CNAME records for `fxcss.com` or `www.fxcss.com` by hand, and remove any existing records for those two names first. Leave unrelated records intact. Wait until both custom domains show as active in the dashboard.

   To deploy on every push instead, connect the repository with Cloudflare Workers Builds:
   - root directory `website`;
   - build command `npm ci && npm run build`;
   - deploy command `npm run deploy`.

4. Turn on two zone settings. The Worker's `proxy.ts` sends plain HTTP and `www` page requests to `https://fxcss.com`, but static files such as `/install.sh` are served before the Worker runs, so the zone has to cover them:
   - **SSL/TLS → Edge Certificates → Always Use HTTPS.**
   - A redirect rule from `www.fxcss.com/*` to `https://fxcss.com/${1}`, keeping the query string. In the dashboard: Rules → Redirect Rules, with the "Redirect from WWW to root" template.

5. Validate the public domain without authentication:

   ```sh
   npm run check:production -- https://fxcss.com
   curl -fsSLo /tmp/fxcss-install-check.sh https://fxcss.com/install.sh
   bash /tmp/fxcss-install-check.sh --help
   ```

   The check requires the exact script bytes, HTTP 200 without a sign-in redirect, `text/plain`, the expected cache policy, all canonical URLs, and the complete sitemap. Also verify that `http://fxcss.com/install.sh` and `https://www.fxcss.com/install.sh` redirect to `https://fxcss.com/install.sh`. The `--help` command does not install anything.

   Add `fxcss.com` to Simple Analytics. In a browser, verify that `latest.js` loads, a page view reaches `queue.simpleanalyticscdn.com`, and an external link click appears as an `outbound_<hostname>` event. Analytics should remain absent when Do Not Track is enabled. The site uses Simple Analytics directly; the separate `a.adamxweb.com` proxy is not a dependency.

   Make the Simple Analytics dashboard public, because `/open` links to `https://dashboard.simpleanalytics.com/fxcss.com`.

6. Publish the matching repository documentation and package metadata with the launch. These changes advertise `fxcss.com`; the domain should be active before they reach users.

## Installer behavior

The installer asks for a starting point, confirms its installation plan, bootstraps pipx when necessary, installs `fxcss[images]`, verifies the executable, and runs `pipx ensurepath`. It prints the chosen next steps without launching Firefox or creating theme files. Re-running it preserves an existing pipx-managed fxcss version and adds Pillow only if missing. Update existing fxcss explicitly with `pipx upgrade fxcss`.

On macOS, missing pipx is installed through an existing Homebrew installation. Otherwise setup uses Python 3.10+ and venv to bootstrap a persistent pipx-managed pipx installation. It cleans up only its own temporary bootstrap directory. Missing Python or venv produces recovery instructions; the installer does not install an operating-system package manager, run as root, or bypass Python's system-package protections.

`npm run test:installer` tests those paths with isolated command stubs, including a real terminal-backed `cat install.sh | bash` flow. No tests install packages or modify the host's shell configuration. Windows users receive the documented PowerShell/pipx instructions.
