# Hosting fxcss.com

## Current state

The source and production build are prepared for `https://fxcss.com`. They include the interactive shell installer at `/install.sh`, canonical page URLs, `/sitemap.xml`, and `/robots.txt`. The installer is a public static asset with a plain-text content type, revalidation on every request, and no sign-in requirement in the application.

The domain's public nameservers were `dorthy.ns.cloudflare.com` and `scott.ns.cloudflare.com` when checked on 2026-09-05. No apex A record was returned. Hosting and DNS have not been changed by this preparation. `.openai/hosting.json` is still unregistered; there is no deployed Site ID to reuse yet.

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

2. Use the Sites hosting workflow for this existing application. Register it once, retain its Site ID in `.openai/hosting.json`, and save the exact validated source and Worker build. The output is `dist/server/index.js` and `dist/client/`. Keep the enclosing fxcss repository intact; use a dedicated staging checkout if the hosting workflow requires a repository rooted at the website. `content/docs.json` and `content/evidence.json` are already generated. Their recorded inputs in `content/source/` make the website independently buildable; working in the full repository refreshes those inputs from the root README and package version.

3. Publish with **public access** when launch is authorized. The shell installer must be accessible to an anonymous HTTP client; an owner-only preview or sign-in-gated deployment cannot serve the advertised setup command.

4. Attach `fxcss.com` as a custom domain through Sites. Use the exact apex A targets and validation records returned by the domain operation in Cloudflare DNS. Do not invent an IP address, use the source repository endpoint, or point DNS at localhost. Leave unrelated records intact. Wait until both the domain and its TLS certificate report active.

5. Enable an HTTPS redirect at the hosting edge so it also covers static assets. If `www.fxcss.com` is desired, register that hostname as well and use the returned CNAME and validation records. Application page requests for `www.fxcss.com` redirect to the canonical apex, preserving the path and query. Configure the same host redirect at the edge for static asset requests.

6. Validate the public domain without authentication:

   ```sh
   npm run check:production -- https://fxcss.com
   curl -fsSLo /tmp/fxcss-install-check.sh https://fxcss.com/install.sh
   bash /tmp/fxcss-install-check.sh --help
   ```

   The check requires the exact script bytes, HTTP 200 without a sign-in redirect, `text/plain`, the expected cache policy, all canonical URLs, and the complete sitemap. Also verify that `http://fxcss.com/install.sh` redirects to HTTPS. The `--help` command does not install anything.

7. Publish the matching repository documentation and package metadata with the launch. These changes advertise `fxcss.com`; the domain should be active before they reach users.

## Installer behavior

The installer asks for a starting point, confirms its installation plan, bootstraps pipx when necessary, installs `fxcss[images]`, verifies the executable, and runs `pipx ensurepath`. It prints the chosen next steps without launching Firefox or creating theme files. Re-running it preserves an existing pipx-managed fxcss version and adds Pillow only if missing. Update existing fxcss explicitly with `pipx upgrade fxcss`.

On macOS, missing pipx is installed through an existing Homebrew installation. Otherwise setup uses Python 3.10+ and venv to bootstrap a persistent pipx-managed pipx installation. It cleans up only its own temporary bootstrap directory. Missing Python or venv produces recovery instructions; the installer does not install an operating-system package manager, run as root, or bypass Python's system-package protections.

`npm run test:installer` tests those paths with isolated command stubs, including a real terminal-backed `cat install.sh | bash` flow. No tests install packages or modify the host's shell configuration. Windows users receive the documented PowerShell/pipx instructions.
