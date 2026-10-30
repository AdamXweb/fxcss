import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
import { searchDocs } from '../lib/docs-search.ts';
import { describeMarkdown } from '../scripts/doc-text.mjs';
const read = (p) => fs.readFileSync(new URL(p, import.meta.url));
const docs = JSON.parse(read('../content/docs.json'));
const manifest = JSON.parse(read('../public/evidence/manifest.json'));
const hash = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');

test('documentation matches the README and includes every command', () => {
  const readme = read('../content/source/README.md');
  const original = new URL('../../fxcss/__init__.py', import.meta.url);
  if (fs.existsSync(original))
    assert.equal(hash(read('../../README.md')), hash(readme));
  assert.equal(docs.sourceSha256, hash(readme));
  const names = [...readme.toString().matchAll(/^### fxcss (\w+)$/gm)].map(
    (m) => m[1],
  );
  const pages = docs.pages
    .filter((p) => p.group === 'command')
    .map((p) => p.slug);
  assert.deepEqual(
    pages.sort((a, b) => a.localeCompare(b)),
    names.sort((a, b) => a.localeCompare(b)),
  );
  assert.equal(new Set(docs.pages.map((p) => p.slug)).size, docs.pages.length);
});
test('generated documentation has valid local links, assets, and code controls', () => {
  const valid = new Set([
    '/',
    '/docs',
    '/docs/screenshot-evidence',
    '/open',
    ...docs.pages.map((p) => '/docs/' + p.slug),
  ]);
  for (const page of docs.pages) {
    assert.ok(page.html.trim(), page.slug);
    assert.doesNotMatch(page.html, /<script\b|\son\w+=|href="javascript:/i);
    // Every link has a name and none nests in another; header cells have text.
    assert.doesNotMatch(
      page.html,
      /<a\b[^>]*>\s*<\/a>/,
      `${page.slug}: empty link`,
    );
    assert.doesNotMatch(
      page.html,
      /<a\b(?:(?!<\/a>)[\s\S])*<a\b/,
      `${page.slug}: nested link`,
    );
    assert.doesNotMatch(
      page.html,
      /<th\b[^>]*>\s*<\/th>/,
      `${page.slug}: empty header`,
    );
    assert.equal(
      new Set(page.search.map((passage) => passage.id)).size,
      page.search.length,
    );
    for (const passage of page.search) {
      assert.ok(passage.text.trim(), `${page.slug}: empty search passage`);
      assert.ok(
        page.html.includes(`id="${passage.id}"`),
        `${page.slug}: ${passage.id}`,
      );
    }
    const indices = [...page.html.matchAll(/data-copy-index="(\d+)"/g)].map(
      (m) => Number(m[1]),
    );
    assert.deepEqual(
      indices,
      page.codes.map((_, i) => i),
      page.slug,
    );
    for (const match of page.html.matchAll(
      /(?:href|src)="(\/[^"#]*)(?:#[^"]*)?"/g,
    )) {
      const href = match[1];
      if (!valid.has(href))
        assert.ok(
          fs.existsSync(new URL('../public' + href, import.meta.url)),
          `${page.slug}: ${href}`,
        );
    }
  }
});
test('full-text search finds command options and links to their passage', () => {
  const results = searchDocs(docs.pages, '--profile');
  assert.ok(results.length >= 2);
  assert.equal(results[0].page.slug, 'install');
  assert.match(results[0].excerpt, /--profile/i);
  for (const { page, href } of results) {
    const anchor = href.split('#')[1];
    assert.ok(anchor, href);
    assert.ok(page.search.some((passage) => passage.id === anchor));
    assert.ok(page.html.includes(`id="${anchor}"`));
  }
  assert.ok(
    searchDocs(docs.pages, 'Firefox profile').some(
      ({ page }) => page.slug === 'install',
    ),
  );
  assert.equal(searchDocs(docs.pages, 'no unfindable-command-option').length, 0);
  assert.equal(searchDocs(docs.pages, 'unfindable-command-option').length, 0);
});
test('search accepts reordered terms and ranks exact phrases first', () => {
  const page = { title: 'Guide', description: '', chapter: 'Guide', group: 'guide' };
  const candidates = [
    { ...page, slug: 'fallback', search: [{ id: 'one', text: 'Screenshots show the result.' }, { id: 'two', text: 'Compare each release.' }] },
    { ...page, slug: 'exact', search: [{ id: 'three', text: 'Compare screenshots with fxcss.' }] },
    { ...page, slug: 'missing', search: [{ id: 'four', text: 'Compare colours.' }] },
  ];
  assert.deepEqual(searchDocs(candidates, 'compare screenshots').map(({ page }) => page.slug), ['exact', 'fallback']);
  assert.deepEqual(new Set(searchDocs(candidates, 'screenshots compare').map(({ page }) => page.slug)), new Set(['exact', 'fallback']));
  assert.equal(searchDocs(candidates, 'screenshots impossible').length, 0);
  assert.equal(searchDocs(candidates, '  ').length, 0);
  for (const query of ['screenshots compare', 'mode dark', 'install --profile']) {
    assert.ok(searchDocs(docs.pages, query).length, query);
  }
});

test('the separately cached search index matches the generated pages and its hash', () => {
  const { url } = JSON.parse(read('../content/search-index.json'));
  const bytes = read('../public' + url);
  assert.equal(url, `/search/index-${hash(bytes).slice(0, 16)}.json`);
  assert.deepEqual(JSON.parse(bytes), docs.pages.map(({ slug, title, description, chapter, group, search }) =>
    ({ slug, title, description, chapter, group, search }),
  ));
});

test('descriptions prefer prose, use readable list/table fallbacks, and keep whole words', () => {
  assert.equal(describeMarkdown('# Heading\n\n| Name | Value |\n| --- | --- |\n| theme | blue |\n\nUse **fxcss** & save [your theme](https://example.com).', 'Fallback'), 'Use fxcss & save your theme.');
  assert.equal(describeMarkdown('- **Python** 3.9+\n- Firefox', 'Fallback'), 'Python 3.9+; Firefox');
  assert.equal(describeMarkdown('| Command | Purpose |\n| --- | --- |\n| `watch` | Edit CSS live |', 'Fallback'), 'watch: Edit CSS live');
  assert.equal(describeMarkdown('![Preview](preview.png)', 'Fallback'), 'Fallback');
  assert.equal(describeMarkdown('The steps are:\n\n```sh\nfxcss watch\n```\n\nEdit CSS live in Firefox.', 'Fallback'), 'Edit CSS live in Firefox.');
  assert.equal(describeMarkdown('Check two things:\n\n- **Selectors:** audit your theme.\n- Screenshots.', 'Fallback'), 'Check two things: Selectors: audit your theme.');
  const introduction = 'Install fxcss with pipx, which gives it its own environment and puts the command on your PATH';
  assert.equal(describeMarkdown(`${introduction}:\n\n\`\`\`sh\npipx install fxcss\n\`\`\``, 'Fallback'), `${introduction}.`);
  const prose = 'A useful description with complete words. '.repeat(8).trim();
  const result = describeMarkdown(prose, 'Fallback');
  assert.ok(result.length <= 160);
  assert.ok(result.endsWith('…'));
  assert.ok(prose.startsWith(result.slice(0, -1)));
  assert.match(prose.slice(result.length - 1), /^[ .]/);
});

test('reference and example-led pages have useful generated descriptions', () => {
  const description = (slug) => docs.pages.find((page) => page.slug === slug).description;
  assert.match(description('requirements'), /Python 3\.9\+.*Firefox.*Pillow/);
  assert.match(description('commands'), /fxcss commands.*themes.*compatibility checks/);
  assert.match(description('rollback'), /chrome\.backup-\*.*installation record/);
  assert.match(description('pull-request-previews'), /Render: capture the base and proposed theme revisions/);
  assert.match(description('watching-firefox-for-breakage'), /Theme selectors: run an audit/);
  assert.match(description('using-it-in-ci'), /theme revisions.*Firefox.*display/);
  assert.match(description('installation'), /Install it with pipx/);
  assert.match(description('completions'), /Tab-completes subcommands/);
  for (const page of docs.pages) {
    assert.notEqual(page.description, page.title, page.slug);
    assert.ok(page.description.length <= 160, `${page.slug}: description length`);
    assert.doesNotMatch(page.description, /:$|\||<[^>]+>|\]\(|```/, `${page.slug}: unfinished or marked-up description`);
  }
});

test('screenshot assets match the recorded evidence without pixel edits', () => {
  assert.deepEqual(JSON.parse(read('../content/evidence.json')), manifest);
  for (const [file, expected] of Object.entries(manifest.assets))
    assert.equal(hash(read('../public/evidence/' + file)), expected, file);
  const before = read('../public/evidence/before.css').toString();
  const after = read('../public/evidence/after.css').toString();
  assert.equal(
    after,
    before.replace('--demo-accent: #4f6ef2;', '--demo-accent: #ff7139;'),
  );
  for (const file of ['before.png', 'after.png', 'dark.png']) {
    const bytes = read('../public/evidence/' + file);
    assert.equal(bytes.readUInt32BE(16), manifest.dimensions.width);
    assert.equal(bytes.readUInt32BE(20), manifest.dimensions.height);
  }
});
test('comparison measurements and coverage agree', () => {
  const summary = JSON.parse(
    read('../public/evidence/comparison-summary.json'),
  );
  const view = summary.views.find((v) => v.view === 'light-01-window');
  assert.deepEqual(view, manifest.comparison);
  assert.equal(
    Number(((100 * view.changed_pixels) / view.total_pixels).toFixed(4)),
    view.percent,
  );
  assert.ok(view.changed_pixels > 0);
  for (const record of summary.views.filter((v) => v.view.startsWith('dark-')))
    assert.equal(record.changed_pixels, 0, record.view);
  for (const file of ['before-coverage.json', 'after-coverage.json']) {
    const coverage = JSON.parse(read('../public/evidence/' + file));
    assert.equal(Object.keys(coverage.views).length, 20);
    assert.ok(
      Object.values(coverage.views).every((v) => v.status === 'captured'),
    );
  }
});
test('the obvious toolbar example comes from the same baseline', () => {
  const before = read('../public/evidence/before.css').toString();
  const toolbar = read('../public/evidence/toolbar-after.css').toString();
  assert.equal(
    toolbar,
    before.replace('--demo-toolbar: #eaeefb;', '--demo-toolbar: #ffe1cf;'),
  );
  const bytes = read('../public/evidence/toolbar-after.png');
  assert.equal(bytes.readUInt32BE(16), manifest.dimensions.width);
  assert.equal(bytes.readUInt32BE(20), manifest.dimensions.height);
  const summary = JSON.parse(
    read('../public/evidence/toolbar-comparison-summary.json'),
  );
  const view = summary.views.find((v) => v.view === 'light-01-window');
  assert.deepEqual(view, manifest.toolbar.comparison);
  assert.equal(
    Number(((100 * view.changed_pixels) / view.total_pixels).toFixed(4)),
    view.percent,
  );
  // The point of the second example: it is far more visible than the accent change.
  assert.ok(view.percent > 10 * manifest.comparison.percent);
  for (const record of summary.views.filter((v) => v.view.startsWith('dark-')))
    assert.equal(record.changed_pixels, 0, record.view);
  const coverage = JSON.parse(
    read('../public/evidence/toolbar-after-coverage.json'),
  );
  assert.equal(Object.keys(coverage.views).length, 20);
  assert.ok(
    Object.values(coverage.views).every((v) => v.status === 'captured'),
  );
});
test('catalogue images are backed by measured Firefox elements', () => {
  const catalogue = JSON.parse(read('../public/catalogue/catalogue.json'));
  assert.equal(catalogue.info.version, manifest.browser.version);
  assert.equal(catalogue.entries.length, 25);
  const urlbar = catalogue.entries.find((e) => e.selector === '#urlbar');
  assert.ok(urlbar);
  for (const mode of ['light', 'dark']) {
    assert.equal(urlbar.modes[mode].found, true);
    assert.equal(urlbar.modes[mode].visible, true);
    for (const item of catalogue.entries) {
      const shot = item.modes[mode].shot;
      if (shot)
        assert.ok(
          fs.existsSync(
            new URL('../public/catalogue/' + shot, import.meta.url),
          ),
          shot,
        );
    }
  }
});
test('the website data page names every outside service and storage use', () => {
  const page = read('../app/open/page.tsx').toString().replace(/\s+/g, ' ');
  assert.deepEqual(
    [...page.matchAll(/<h2 id="[^"]+">([^<]+)<\/h2>/g)].map((m) => m[1]),
    [
      'Services this website uses',
      'How your data is stored',
      'Open statistics',
      'Cookies and browser storage',
      'What is not on this site',
      'The fxcss tool',
      'More detail',
    ],
  );
  // Every outside host the pages load or send to must be a service /open names.
  const providers = {
    'scripts.simpleanalyticscdn.com': 'Simple Analytics',
    'queue.simpleanalyticscdn.com': 'Simple Analytics',
  };
  const hosts = new Set(
    [
      ...`${read('../proxy.ts')}\n${read('../app/layout.tsx')}`.matchAll(
        /https:\/\/([a-z0-9.-]+)/g,
      ),
    ].map((m) => m[1]),
  );
  for (const host of hosts) {
    assert.ok(providers[host], `${host}: add its service to /open`);
    assert.ok(page.includes(providers[host]), host);
  }
  const loaded = [...new Set(Object.values(providers))].join(' and ');
  assert.ok(page.includes(`the site loads only ${loaded}.`));
  for (const [, href] of page.matchAll(/href="([^"]+)"/g))
    assert.match(href, /^https:\/\//, href);
  // No cookies or lasting storage. The one tab-only preference is explicitly
  // listed on /open; the built-site check also verifies every emitted key.
  const code = ['app', 'components', 'hooks', 'lib'].flatMap((dir) =>
    fs
      .readdirSync(new URL(`../${dir}/`, import.meta.url), { recursive: true })
      .filter((file) => /\.(tsx?|m?js)$/.test(file))
      .map((file) => `../${dir}/${file}`),
  );
  for (const file of [...code, '../proxy.ts']) {
    const source = read(file).toString();
    assert.doesNotMatch(
      source,
      /document\.cookie|cookieStore|localStorage|indexedDB|set-cookie/i,
      `${file}: list this storage on /open`,
    );
    if (/sessionStorage/.test(source)) {
      assert.equal(file, '../lib/time-machine-storage.ts', 'Only the disclosed preference helper may use tab storage');
      const accesses = [...source.matchAll(/sessionStorage\.(?:getItem|setItem|removeItem)\('([^']+)'/g)];
      assert.equal(accesses.length, 3);
      assert.equal([...source.matchAll(/\bsessionStorage\b/g)].length, accesses.length);
      for (const [, key] of accesses) assert.equal(key, 'fxcss-time-machine');
      assert.ok(page.includes('<code>fxcss-time-machine</code>'));
    }
  }
  assert.match(
    read('../app/components/site-chrome.tsx').toString(),
    /<Link href="\/open">Website data<\/Link>/,
  );
  assert.match(read('../app/sitemap.xml/route.ts').toString(), /"\/open"/);
});

test('pages and static files send the same security headers', () => {
  const expected = {
    'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
  };
  // Static files are served before the Worker, from public/_headers.
  const rules = read('../public/_headers').toString();
  const all = rules.match(/^\/\*\n((?:[ \t]+\S.*\n?)+)/m)?.[1] ?? '';
  for (const [name, value] of Object.entries(expected))
    assert.ok(all.includes(`${name}: ${value}`), `public/_headers /*: ${name}`);
  const proxy = read('../proxy.ts').toString();
  for (const [name, value] of Object.entries(expected))
    assert.ok(proxy.includes(`'${name}': '${value}'`), `proxy.ts: ${name}`);
  assert.ok(proxy.includes(`"frame-ancestors 'none'"`), 'proxy.ts: framing');
  // The docs sidebar cannot collapse, so Ctrl/Cmd+B stays with the browser.
  assert.doesNotMatch(
    read('../components/ui/sidebar.tsx').toString(),
    /addEventListener\(\s*['"]keydown/,
  );
});
test('every page shares the 1200 x 630 card', () => {
  const card = read('../public/assets/og-card.png');
  assert.equal(card.readUInt32BE(16), 1200);
  assert.equal(card.readUInt32BE(20), 630);
  assert.match(read('../lib/share-card.ts').toString(), /summary_large_image/);
  // A page's own openGraph or twitter object replaces the layout's, image
  // included, so pages set them only through shareCard.
  const files = fs
    .readdirSync(new URL('../app/', import.meta.url), { recursive: true })
    .filter((file) => /(?:page|layout)\.tsx$/.test(file))
    .map((file) => [file, read(`../app/${file}`).toString()])
    .filter(([, source]) =>
      /export (?:const metadata|async function generateMetadata)/.test(source),
    );
  assert.ok(files.length >= 6);
  for (const [file, source] of files) {
    assert.match(source, /shareCard\(/, `${file}: share card`);
    assert.doesNotMatch(
      source,
      /\b(?:openGraph|twitter):/,
      `${file}: use shareCard`,
    );
  }
});
test('the docs search has a visible label and an always-present status', () => {
  const nav = read('../app/components/docs-navigation.tsx').toString();
  assert.match(
    nav,
    /<label htmlFor=\{searchId\}>Search documentation<\/label>/,
  );
  assert.match(nav, /<Input\s+id=\{searchId\}/);
  assert.doesNotMatch(nav, /aria-label="Search/);
  // Rendered unconditionally, so screen readers hear each new result count.
  assert.match(nav, /\n\s*<output className="search-count">/);
  assert.doesNotMatch(nav, /&&\s*\(\s*<output/);
});
test('links in running text are underlined', () => {
  const css = read('../app/globals.css').toString();
  for (const selector of [
    '.doc-body a',
    '.footer-credit a',
    '.guide-article .breadcrumbs a',
    '.not-found a:not(.button)',
  ]) {
    const start =
      css.indexOf(`${selector} {`) >= 0
        ? css.indexOf(`${selector} {`)
        : css.indexOf(`${selector},`);
    assert.ok(start >= 0, selector);
    const block = css.slice(start, css.indexOf('}', start));
    assert.match(block, /text-decoration: underline/, selector);
  }
});
test('the Worker answers only on fxcss.com and uploads no repository metadata', () => {
  // A dashboard-only change is undone by the next deploy, so both flags live
  // in the config the build emits. The custom domains carry the zone's WAF,
  // HSTS and Access; a workers.dev or preview URL does not.
  const config = read('../vite.config.ts').toString();
  for (const line of ['workers_dev: false,', 'preview_urls: false,'])
    assert.ok(config.includes(line), `vite.config.ts: ${line}`);
  // Wrangler uploads everything in dist/client except what .assetsignore
  // lists, and the Cloudflare Vite plugin prepends public/.assetsignore to it.
  const patterns = read('../public/.assetsignore')
    .toString()
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'));
  for (const pattern of [
    '.git/',
    '.wrangler/',
    '.DS_Store',
    '.env*',
    'wrangler.json*',
    'node_modules/',
    '.github/',
    '.gh-workflows/',
    'justfile',
    'README.md',
  ])
    assert.ok(patterns.includes(pattern), `public/.assetsignore: ${pattern}`);
  // security.txt must stay public.
  assert.ok(
    !patterns.some((pattern) => pattern.includes('.well-known')),
    'public/.assetsignore: .well-known/ must stay public',
  );
  // After `just build`, the emitted config and assets directory carry both.
  const built = new URL('../dist/server/wrangler.json', import.meta.url);
  if (fs.existsSync(built)) {
    const emitted = JSON.parse(fs.readFileSync(built));
    assert.equal(emitted.workers_dev, false, 'dist/server/wrangler.json');
    assert.equal(emitted.preview_urls, false, 'dist/server/wrangler.json');
    const ignore = fs
      .readFileSync(new URL(`${emitted.assets.directory}/.assetsignore`, built))
      .toString()
      .split('\n');
    for (const pattern of patterns)
      assert.ok(
        ignore.includes(pattern),
        `dist/client/.assetsignore: ${pattern}`,
      );
  }
});
