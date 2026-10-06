# fxcss.com — one front door for build and deploy.
#
# Same contract as the Trainie Talkie and RelicPack website justfiles: every
# recipe DELEGATES to an npm script, a wrangler command or a file under
# scripts/, and `just --list` is the index, so nothing here becomes the only
# place a command is written down. What this adds over the npm scripts is
# ORDER and PRECONDITIONS: each recipe checks what it needs and names the
# recipe that provides it, instead of failing inside wrangler with an error
# that blames something else.
#
# This file exists only on the `website` branch. `main` holds fxcss itself and
# the README the documentation is generated from; `just content` and
# `just evidence` take a checkout of main for that. The site has no database
# and no secrets, so a deploy is build, dry-run, deploy.

set shell := ["bash", "-uc"]

wrangler_config := "dist/server/wrangler.json"
local_url := "http://127.0.0.1:4318"
public_url := "https://fxcss.com"

# Show the available commands.
default:
    @just --list --unsorted

# Needs Node 22.13 or newer (package.json engines); CI uses 24.
[doc("Fresh checkout: install the locked dependencies")]
setup:
    npm ci

# Without node_modules, `npm run deploy` says "sh: wrangler: command not
# found", and a bare `npx wrangler` would fetch whatever is newest instead of
# the pinned version.
_installed:
    @test -x node_modules/.bin/wrangler || { echo "▸ node_modules missing — run 'just setup' first"; exit 1; }

# `wrangler whoami` exits 0 even when logged out; it only SAYS so.
_logged-in: _installed
    @npx wrangler whoami 2>&1 | grep -q "not authenticated" && { echo "▸ Not signed in to wrangler — run 'just login' first"; exit 1; } || true

# wrangler's own error for a missing config is an ENOENT that reads as a broken
# build rather than a skipped one.
_built: _installed
    @test -f {{wrangler_config}} || { echo "▸ {{wrangler_config}} missing — run 'just build' first"; exit 1; }

# The Website CI checks before the build: documentation, types, lint, tests,
# the installer, and the dependency audit (scripts/audit.mjs).
[doc("What Website CI checks before building: docs, types, lint, tests, installer, audit")]
check: _installed
    npm run content
    npm run typecheck
    npm run lint
    npm test
    @command -v shellcheck >/dev/null || { echo "▸ shellcheck missing — brew install shellcheck"; exit 1; }
    bash -n public/install.sh && shellcheck public/install.sh
    npm run test:installer
    npm run check:audit

[doc("Local development server on http://127.0.0.1:4317")]
dev: _installed
    npm run dev -- --host 127.0.0.1 --port 4317

[doc("Build the Worker into dist/")]
build: _installed
    npm run build
    @echo "▸ Built. Config at {{wrangler_config}}"

# The built Worker under wrangler (workerd), the same runtime as production.
[doc("Serve the built Worker on http://127.0.0.1:4318")]
preview: _built
    npm start -- --ip 127.0.0.1 --port 4318

# Every page, asset, redirect and security header. Against `just preview` by
# default; `just verify https://fxcss.com` checks the live site, and also
# whether the homepage film can be streamed (Safari needs a 206 reply; the
# local server never sends one, so that part is skipped locally).
[doc("Check a running site: just verify, or just verify https://fxcss.com")]
verify url=local_url: _installed
    npm run check:production -- {{url}}
    @case "{{url}}" in http://127.0.0.1*|http://localhost*) exit 0;; esac; \
      code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Range: bytes=0-1023' {{url}}/media/fxcss-film.mp4); \
      if [ "$code" = 206 ]; then echo "▸ Film supports range requests (206)"; \
      else echo "▸ Film answered $code to a range request, not 206: Safari will not play it (serve it from R2 or Stream)"; fi

# Proves the config, the custom domains and the assets are coherent without
# uploading anything, and needs no login. CI runs it on every change.
[doc("wrangler deploy --dry-run against the built config")]
dry-run: _built
    npm run deploy -- --dry-run

[doc("Sign in to wrangler (browser OAuth)")]
login: _installed
    npx wrangler login

# Creates the fxcss-website Worker the first time and attaches fxcss.com and
# www.fxcss.com (the deploy script passes both domains). Cloudflare adds their
# DNS records and certificates; do not create them by hand.
[doc("Upload the built Worker and attach fxcss.com")]
deploy: _built _logged-in
    npm run deploy

# The whole sequence, in the only order that works.
[doc("First deploy: check → build → dry-run → deploy")]
first-deploy: check build dry-run deploy
    @echo "▸ Deployed. Two zone settings cover static files such as /install.sh, which are served before the Worker runs:"
    @echo "    SSL/TLS → Edge Certificates → Always Use HTTPS"
    @echo "    Rules → Redirect Rules → 'Redirect from WWW to root'"
    @echo "▸ Then: just verify {{public_url}}"

# Live logs from the deployed Worker. Ctrl-C to stop.
[doc("Stream the deployed Worker's logs")]
tail: _built _logged-in
    npx wrangler tail --config {{wrangler_config}} --format pretty

# The documentation pages are generated from main's README. This refreshes
# the recorded copy in content/source from a checkout of main; commit the
# result. Website CI warns when that copy is behind main.
[doc("Refresh the docs from a checkout of main: just content ../fxcss")]
content main_checkout: _installed
    @test -f "{{main_checkout}}/fxcss/__init__.py" || { echo "▸ {{main_checkout}} is not a checkout of fxcss main"; exit 1; }
    FXCSS_SOURCE="{{main_checkout}}" npm run content

# The same refresh, run on GitHub by the Refresh docs workflow: regenerate from
# main, check, push here, and Workers Builds deploys. Releases and README
# changes on main start it on their own; this is for a manual rerun.
[doc("Regenerate the docs from main on GitHub and publish them")]
refresh:
    gh workflow run refresh-docs.yml --repo AdamXweb/fxcss --ref website
    @echo "▸ Started. Follow it with: gh run watch --repo AdamXweb/fxcss"

# Recaptures the homepage comparison and the parts catalogue with fxcss itself.
# Needs Firefox and Pillow, and opens Firefox windows for about ten minutes.
[doc("Recapture the comparison screenshots with fxcss: just evidence ../fxcss")]
evidence main_checkout: _installed
    @test -f "{{main_checkout}}/fxcss/__init__.py" || { echo "▸ {{main_checkout}} is not a checkout of fxcss main"; exit 1; }
    python3 scripts/capture-evidence.py "{{main_checkout}}"
    npm run content

[doc("Remove build output and caches; keeps node_modules")]
clean:
    rm -rf dist .next .vinext .wrangler next-env.d.ts tsconfig.tsbuildinfo work
