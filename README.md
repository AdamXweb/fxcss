# fxcss website

The Showcase homepage and Field guide documentation for https://fxcss.com. The application is built with vinext (the Next.js App Router on Vite) and runs as a Cloudflare Worker.

This is the `website` branch of [AdamXweb/fxcss](https://github.com/AdamXweb/fxcss). fxcss itself, and the README the documentation is generated from, live on `main`. The two share no files: website changes go to pull requests against `website`, toolkit changes against `main`.

## Commands

`just` is the front door; `just --list` shows every recipe. Each one runs an npm script, a wrangler command or a file under `scripts/`, and checks what it needs first: run them out of order and it names the recipe to run instead.

| Command | What it does |
| --- | --- |
| `just setup` | Install the locked dependencies (Node.js 22.18 or newer). |
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

`npm run check:audit` runs `npm audit` and fails on any advisory at moderate or above. There are no accepted advisories in `scripts/audit.mjs`. The `miniflare` override in `package.json` pins the patched `undici` 7.30.1 while the Cloudflare development tools still pin an affected release; remove the override when their dependency is patched.

vinext 1.0.1 includes `@vercel/og` 1.0.3 with the Satori SVG-escaping fix and the framework's HarfBuzz runtime support. The `satori` override pins `fflate` 0.7.5 to fix its ZIP64 parsing advisory while Satori pins 0.7.3; remove that override once Satori uses a patched release. The `@ts-morph/common` override moves `minimatch` to 11.0.3 for its globparts denial-of-service advisory; it is reached only through the `shadcn` CLI (via `ts-morph`), whose newest release still asks for `minimatch` 10, which has no patched version. Remove it once `@ts-morph/common` depends on `minimatch` 11. The lockfile also keeps `@hono/node-server` and `systeminformation` on patched versions within their existing dependency ranges.

The Website GitHub Actions workflow (`.github/workflows/website.yml`) runs the same checks, plus a deploy dry run, on every pull request and push to `website`, without deploying. Pull requests stacked on `fix/website-*` branches also run these checks. A second job warns when the recorded documentation is behind `main`. The production check visits every documentation page, validates asset responses and the security headers on pages, redirects and static files, checks raw-head metadata and share cards, checks that each labelled control's name contains its visible text, and verifies not-found responses and redirects. On Node 24, CI also runs the Playwright Chromium and Firefox suite (`npm run test:browser`) against the built Worker. It covers journey selection, game completion, search loading and recovery, route announcements, code copying, keyboard-operated comparison controls, catalogue toggles, mobile Escape handling, logo picking and its report card, sidebar search sizing and scrolling, narrow layouts, and the reported catalogue and retro contrast regressions. The main checks also run on the declared minimum Node 22.18.0. For local browser checks, run `npx playwright install chromium firefox`, `npm run build`, then `npm run test:browser`; Playwright starts and stops its own local Worker and blocks outside requests. These checks do not replace testing with screen readers or native browser zoom.

To run only Firefox, use `npm run test:browser -- --project=firefox`. If the bundled Firefox fails to start on macOS 27 with “Could not find profile folder”, give the test browser separate application data directories ([upstream issue](https://github.com/microsoft/playwright/issues/42768)):

```sh
mkdir -p work/firefox-app-data work/firefox-local-app-data
MOZ_APP_DATA="$PWD/work/firefox-app-data" MOZ_LOCAL_APP_DATA="$PWD/work/firefox-local-app-data" npm run test:browser
```

## Documentation source

`README.md` on `main` is the reference source. This branch keeps a recorded copy of it, and of the fxcss version, in `content/source/`. `npm run content` generates and sanitises `content/docs.json` from that copy; it runs automatically before development and production builds. Source hashes and tests detect stale generated content and missing command pages. The build also writes a content-hashed search index under `public/search/` and records its URL in `content/search-index.json`. Documentation navigation receives only page labels; search downloads the index on first focus or input, with a retry control and the normal page navigation if it fails. Retrying returns keyboard focus to the search field. Exact phrases rank first, followed by pages containing all query words in any order. Descriptions select useful prose or list content, with explicit summaries for reference pages, and end at a word boundary. Metadata resolves before HTML streams so all clients receive it in the document head.

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

The transparent vector logo is `public/assets/fxcss-logo.svg`. `app/components/brand-mark.tsx` reuses its named `fxcss-flames` and `fxcss-braces` groups, so the downloadable artwork and the website share one source. `app/components/brand-link.tsx` starts one flame spin on pointer entry or keyboard focus and lets it finish even when the pointer or focus leaves. Re-entry during a spin does not restart it. CSS in `app/globals.css` keeps the cyan braces glowing through the spin, then fades the glow if the link is no longer hovered or focused; the braces and wordmark stay upright. The spin is disabled when reduced motion is requested. Serve the SVG from the same origin when reusing the interactive component; a regular `<img>` displays the static artwork.

The root layout loads Simple Analytics for page views. A delegated click listener records outbound links as `outbound_<hostname>` events, including the destination URL without its query string or fragment. It covers links added by client-side navigation and respects Simple Analytics' Do Not Track behavior. Same-tab links wait for the event request for at most 800 ms before navigating. The script and collection endpoint are allowed by the production Content Security Policy; no inline click handlers are needed. Add `fxcss.com` to the Simple Analytics dashboard before launch and make the dashboard public, because `/open` links to it.

`/open` (`app/open/page.tsx`) is the website data notice: every outside service, cookie, and browser storage key the site uses. Update it with any new service or storage. `npm test` fails when the production policy allows an outside host the notice does not name, or when site code uses cookies or browser storage. The production check compares the session storage keys in the client build with the notice.

## Easter eggs

- Search documentation for `1999`, `2003` / `firebird`, or `2002` / `phoenix` to visit Windows, early Mac OS X, or classic Linux. Exact keywords are case-insensitive. The time machine has a desktop selector, history notes linked to Mozilla's archives, and a return button; Escape also returns to the present outside the search field. Search keeps its existing Escape-to-clear behaviour. The selected desktop survives navigation and reloads in the current tab when tab storage is available. Its single `fxcss-time-machine` preference is disclosed on `/open`. Screenshot evidence and the homepage film are unchanged.
- The themed window buttons are interactive: minimise tucks away the time-machine picker while keeping the theme and site navigation available; restore (the green Mac button or square Windows/Linux button) brings it back; close returns to the present. Hover or keyboard focus shows each action. A visible “Theme controls” shortcut also restores a minimised picker. The collapsed state follows navigation in the current page session; reloading opens the picker again without adding a stored preference.
- `/important` is a seven-round specificity boss battle with hints, explanations, actual isolated CSS previews, scoring and replay. A quiet `{ !important }` footer link and an exact `!important` search lead to it. The examples cover matching, unlayered author declarations; the page links to MDN for the wider cascade and stays out of the sitemap.
- Search `c0ffee` or `#c0ffee` for a short, replayable coffee party. The non-modal message can be dismissed, and reduced-motion visitors get a still celebration. Ordinary search results remain available.
- The favicon sleeps when the tab is hidden and restores the original icons on return, including icons replaced during navigation. It uses a local SVG.
- Drag the fxcss logo in the header or footer out past its edge and it becomes `fxcss pick` for the site. Until the pointer leaves the logo the press stays a click, however much it wobbles, so a shaky click still goes home. Once lifted, whatever it is over gets the picker's pink outline, with the element's classes labelled underneath (above, near the bottom of the window). It labels the nearest element that has a class. When some of an element's classes come from the site's own stylesheet rather than Tailwind's utility layer, only those are shown; utility-only lists are shortened. Release it over an element to pick it, as a click does in `fxcss pick`: a terminal-style card beside the element prints the CLI's report for it, with its tag and selector, classes (the site's own first), box, notable computed styles and the rules on the site that match it, with rules naming one of its classes listed first and Tailwind's layered rules marked by layer. The card copies the selector and links to the `fxcss pick` documentation. It looks like the terminal of the current time-machine stop: an MS-DOS Prompt in 1999, Terminal on Mac OS X in 2003, and an xterm in 2002. The report itself follows the page, so it lists the time machine's `html[data-retro]` rules when they apply. It is a non-modal dialog that takes focus; Escape or its close button returns focus to the logo, and a press elsewhere or leaving the page closes it. Carry the logo back onto itself, release it outside the page, or press Escape during the drag, and nothing is picked or followed. Either way the logo returns; reduced-motion visitors skip the flight and the card's fade. Dropping it activates nothing underneath, and a click without a drag still goes home. Mouse and pen only, so touch scrolling and taps behave as before.

## Screenshot evidence

All current theme screenshots come from fxcss running the bundled starter in disposable profiles. `public/evidence/manifest.json` records Firefox and fxcss versions, the exact CSS change, image dimensions, measured differences, and SHA-256 checksums. `public/catalogue/` is the actual catalogue generated by fxcss. The original PNG captures are unchanged; the homepage displays matching regions using CSS. The highlighted image is generated by fxcss's own comparison renderer.

To refresh the evidence, with Firefox and fxcss's Pillow image dependency installed, pass a checkout of `main` (the capture runs that copy of fxcss and its starter theme):

```sh
just evidence ../fxcss
```

This launches disposable Firefox sessions, captures 20 states for the unchanged starter and for two light-mode changes (the active tab accent, the homepage's small example, and the toolbar colour, its obvious example), checks the measured differences, and regenerates the catalogue. It preserves the original captures and capture-coverage reports. `/docs/screenshot-evidence` explains the measurements and links to the source files.

## Homepage film

The homepage plays a 57-second silent film between the introduction and the three starting points. `public/media/fxcss-film.mp4` is H.264 at 1920x1080 (CRF 27, BT.709, `+faststart`, about 5 MB); `public/media/fxcss-film-poster.jpg` is its frame at 2.5 seconds with a "Get started on your first theme today." play card added on the right; that card appears only on the poster, not in the film. An expandable text description below the film provides its steps without requiring video playback. The video uses `preload="none"`, so visitors download only the poster until they press play. The film ends on the logo, without a URL or install command, because the page carries both.

The film is rendered from an HTML source outside this repository. Its on-screen commands and output match fxcss's real CLI, and the theme colours come from WhiteSur's `custom/theme-*.css`. Replace both files and update the adjacent text description in `app/page.tsx` when the film changes.

## Publishing

The site is prepared for **https://fxcss.com**. `/install.sh` serves the interactive installer directly as plain text. Canonical URLs, the sitemap, and setup examples use that domain. Publish the matching package metadata when the domain is live. See [DEPLOYMENT.md](DEPLOYMENT.md) for the launch, the Cloudflare zone settings and deploying on every push.

The site runs as the Cloudflare Worker `fxcss-website`, configured in `vite.config.ts`; the `deploy` script attaches the `fxcss.com` and `www.fxcss.com` custom domains. `npm run build` writes `dist/server/index.js`, `dist/client/` and the generated `dist/server/wrangler.json`, and `npm run deploy` publishes them. Production scripts require the generated Worker configuration. The public installer must work without a browser session or a sign-in redirect. Do not serve the development server publicly.

The tab favicon uses the existing lightweight SVG, independently of the recorded source icon. After each catalogue recapture, `scripts/prepare_catalogue.py` applies the website's document structure, language, landmarks, toggle semantics and contrast fixes without changing captured images. It fails if the toolkit's generated structure has changed unexpectedly. The captured directory is an auxiliary reference marked `noindex,follow`; `/docs/catalogue` remains its indexable documentation page.
