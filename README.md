# fxcss website

The Showcase homepage and Field guide documentation for https://fxcss.com. The application is built with vinext (the Next.js App Router on Vite) and runs as a Cloudflare Worker.

This is the `website` branch of [AdamXweb/fxcss](https://github.com/AdamXweb/fxcss). fxcss itself, and the README the documentation is generated from, live on `main`. The two share no files: website changes go to pull requests against `website`, toolkit changes against `main`.

## Commands

`just` is the front door; `just --list` shows every recipe. Each one runs an npm script, a wrangler command or a file under `scripts/`, and checks what it needs first: run them out of order and it names the recipe to run instead.

| Command | What it does |
| --- | --- |
| `just setup` | Install the locked dependencies (Node.js 22.13 or newer). |
| `just dev` | Development server on http://127.0.0.1:4317. |
| `just check` | What Website CI checks before building: docs, types, lint, tests, the installer, and the dependency audit. |
| `just build` | Build the Worker into `dist/`. |
| `just preview` | Serve the built Worker on http://127.0.0.1:4318, under the production runtime. |
| `just verify [url]` | Check every page, asset, redirect and security header of a running site; `just verify https://fxcss.com` checks the live one. |
| `just dry-run` | Check the deploy without uploading anything or signing in. |
| `just login` | Sign in to Cloudflare with wrangler. |
| `just deploy` | Upload the Worker and attach `fxcss.com` and `www.fxcss.com`. |
| `just first-deploy` | `check`, `build`, `dry-run` and `deploy` in order, then the two zone settings to turn on. |
| `just tail` | Stream the deployed Worker's logs. |
| `just content ../fxcss` | Refresh the documentation from a checkout of `main`. |
| `just refresh` | Run the Refresh docs workflow on GitHub: regenerate from `main`, check, push here. |
| `just evidence ../fxcss` | Recapture the comparison screenshots with fxcss (opens Firefox windows). |
| `just clean` | Remove build output and caches. |

From a fresh clone:

```sh
git clone --branch website https://github.com/AdamXweb/fxcss.git fxcss-website
cd fxcss-website
just setup
just dev
```

Open http://localhost:4317. `/docs` contains getting-started paths, search, and the full command reference. Every documentation page has its own URL. The old sample URLs redirect to the selected design.

## Production build and checks

```sh
just check
just build
just preview
```

In another terminal:

```sh
just verify
```

`npm run check:audit` runs `npm audit` and fails on any advisory at moderate or above, except those listed with a reason in `scripts/audit.mjs`. The one accepted today, GHSA-vfj7-8cjw-p6xm in `braces`, has no patched release and only reaches build tools, not the deployed Worker; the script notes when it can be removed.

The Website GitHub Actions workflow (`.github/workflows/website.yml`) runs the same checks, plus a deploy dry run, on every pull request and push to `website`, without deploying. A second job warns when the recorded documentation is behind `main`. The production check visits every documentation page, validates asset responses and the security headers on pages, redirects and static files, checks that every page carries the share card and that each labelled control's name contains its visible text, and verifies not-found responses and redirects. Browser checks cover navigation, search, code copying, keyboard-operated comparison controls, and narrow layouts.

## Documentation source

`README.md` on `main` is the reference source. This branch keeps a recorded copy of it, and of the fxcss version, in `content/source/`. `npm run content` generates and sanitises `content/docs.json` from that copy; it runs automatically before development and production builds. Source hashes and tests detect stale generated content and missing command pages.

The copy refreshes itself. The Refresh docs workflow (`.github/workflows/refresh-docs.yml`) regenerates it from `main` and pushes it to this branch, and Workers Builds deploys the result. `main`'s Website docs workflow starts it after each release reaches PyPI and after each README change. Before pushing, it:
- skips a version PyPI doesn't serve yet, so the site never advertises an install that fails;
- runs the type check, tests, build and production checks itself, because its push starts no other workflow.

The homepage's release pill reads the same recorded version, so it moves with the docs.

To refresh by hand, run `just refresh`. Or regenerate locally from a checkout of `main` and commit `content/`:

```sh
just content ../fxcss
```

That sets `FXCSS_SOURCE` for `scripts/build-content.mjs`, which also copies `docs/icon.png`. Website CI compares the recorded copy with `main` on every run and warns when it is behind. The website's getting-started overview remains deliberately short and links to the full generated guides.

`app/components/` contains the shared navigation, documentation controls, comparison, and copy controls. `app/docs/[slug]/page.tsx` renders the generated pages. `proxy.ts` supplies a fresh Content Security Policy nonce and security headers for each production application response. `public/_headers` gives static files the same headers, and the static catalogue its own policy. `lib/share-card.ts` gives every page the share image, `public/assets/og-card.png`. There are no accounts, forms, remote fonts, or site databases.

The root layout loads Simple Analytics for page views. A delegated click listener records outbound links as `outbound_<hostname>` events, including the destination URL without its query string or fragment. It covers links added by client-side navigation and respects Simple Analytics' Do Not Track behavior. Same-tab links wait for the event request for at most 800 ms before navigating. The script and collection endpoint are allowed by the production Content Security Policy; no inline click handlers are needed. Add `fxcss.com` to the Simple Analytics dashboard before launch and make the dashboard public, because `/open` links to it.

`/open` (`app/open/page.tsx`) is the website data notice: every outside service, cookie, and browser storage key the site uses. Update it with any new service or storage. `npm test` fails when the production policy allows an outside host the notice does not name, or when site code uses cookies or browser storage. The production check compares the session storage keys in the client build with the notice.

## Screenshot evidence

All current theme screenshots come from fxcss running the bundled starter in disposable profiles. `public/evidence/manifest.json` records Firefox and fxcss versions, the exact CSS change, image dimensions, measured differences, and SHA-256 checksums. `public/catalogue/` is the actual catalogue generated by fxcss. The original PNG captures are unchanged; the homepage displays matching regions using CSS. The highlighted image is generated by fxcss's own comparison renderer.

To refresh the evidence, with Firefox and fxcss's Pillow image dependency installed, pass a checkout of `main` (the capture runs that copy of fxcss and its starter theme):

```sh
just evidence ../fxcss
```

This launches disposable Firefox sessions, captures 20 states for the unchanged starter and for two light-mode changes (the active tab accent, the homepage's small example, and the toolbar colour, its obvious example), checks the measured differences, and regenerates the catalogue. It preserves the original captures and capture-coverage reports. `/docs/screenshot-evidence` explains the measurements and links to the source files.

## Homepage film

The homepage plays a 57-second silent film between the introduction and the three starting points. `public/media/fxcss-film.mp4` is H.264 at 1920x1080 (CRF 27, BT.709, `+faststart`, about 5 MB); `public/media/fxcss-film-poster.jpg` is its frame at 2.5 seconds with a "Get started on your first theme today." play card added on the right; that card appears only on the poster, not in the film. The video uses `preload="none"`, so visitors download only the poster until they press play. The film ends on the logo, without a URL or install command, because the page carries both.

The film is rendered from an HTML source outside this repository. Its on-screen commands and output match fxcss's real CLI, and the theme colours come from WhiteSur's `custom/theme-*.css`. Replace both files together when the film changes.

## Publishing

The site is prepared for **https://fxcss.com**. `/install.sh` serves the interactive installer directly as plain text. Canonical URLs, the sitemap, and setup examples use that domain. Publish the matching package metadata when the domain is live. See [DEPLOYMENT.md](DEPLOYMENT.md) for the launch, the Cloudflare zone settings and deploying on every push.

Nothing has been published yet. The site runs as the Cloudflare Worker `fxcss-website`, configured in `vite.config.ts`; the `deploy` script attaches the `fxcss.com` and `www.fxcss.com` custom domains. `npm run build` writes `dist/server/index.js`, `dist/client/` and the generated `dist/server/wrangler.json`, and `npm run deploy` publishes them. Production scripts require the generated Worker configuration. The public installer must work without a browser session or a sign-in redirect. Do not serve the development server publicly.
