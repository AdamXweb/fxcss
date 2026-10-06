import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const base = process.argv[2] || 'http://127.0.0.1:4318';
const data = JSON.parse(
  await fs.readFile(new URL('../content/docs.json', import.meta.url)),
);
const routes = [
  '/',
  '/docs',
  '/docs/screenshot-evidence',
  '/open',
  ...data.pages.map((p) => '/docs/' + p.slug),
];
let assetCount = 0;
let namedControls = 0;
const checkedAssets = new Set();
// Sent with every page, redirect and static file.
const securityHeaders = {
  'strict-transport-security': 'max-age=31536000; includeSubDomains',
  'x-content-type-options': 'nosniff',
  'x-frame-options': 'DENY',
  'permissions-policy': 'camera=(), microphone=(), geolocation=()',
  'referrer-policy': 'strict-origin-when-cross-origin',
};
function assertSecurityHeaders(response, label) {
  for (const [name, value] of Object.entries(securityHeaders))
    assert.equal(response.headers.get(name), value, `${label}: ${name}`);
}
const text = (html) =>
  html
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&')
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, ' ')
    .trim();
const words = (value) =>
  value
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
// Every page carries the share card, including not-found responses.
function assertShareCard(html, label) {
  assert.match(
    html,
    /<meta property="og:image" content="https:\/\/fxcss\.com\/assets\/og-card\.png"\/?>/,
    `${label}: og:image`,
  );
  assert.match(
    html,
    /<meta name="twitter:card" content="summary_large_image"\/?>/,
    `${label}: twitter:card`,
  );
}
// Browser storage keys that client code passes to sessionStorage. /open must name each one.
const sessionKeys = new Set();
for (const route of routes) {
  const response = await fetch(new URL(route, base));
  assert.equal(response.status, 200, route);
  assertSecurityHeaders(response, route);
  const html = await response.text();
  assertShareCard(html, route);
  // A control's accessible name contains its visible words (WCAG 2.5.3).
  for (const [, attributes, inner] of html.matchAll(
    /<(?:a|button)\b([^>]*\baria-label="[^"]*"[^>]*)>([\s\S]*?)<\/(?:a|button)>/g,
  )) {
    const visible = words(text(inner));
    if (!visible) continue;
    const name = words(text(attributes.match(/aria-label="([^"]*)"/)[1]));
    assert.ok(
      name.includes(visible),
      `${route}: "${name}" does not contain its visible text "${visible}"`,
    );
    namedControls++;
  }
  assert.match(html, /<title>[^<]+<\/title>/, route);
  assert.match(html, /<main\b/, route);
  const canonical = [...html.matchAll(/<link\b[^>]*>/g)]
    .find(([tag]) => tag.includes('rel="canonical"'))?.[0]
    .match(/href="([^"]+)"/)?.[1];
  assert.equal(
    new URL(canonical).href,
    `https://fxcss.com${route}`,
    `${route}: canonical URL`,
  );
  assert.doesNotMatch(
    html,
    /DESIGN STUDIES|Local preview|Your site is taking shape/,
  );
  const csp = response.headers.get('content-security-policy');
  assert.ok(csp?.includes("object-src 'none'"), `${route}: production CSP`);
  assert.ok(csp.includes("frame-ancestors 'none'"), `${route}: no framing`);
  assert.ok(
    csp.includes('https://queue.simpleanalyticscdn.com'),
    `${route}: analytics collection allowed by CSP`,
  );
  const nonce = csp.match(/'nonce-([^']+)'/)?.[1];
  assert.ok(nonce, `${route}: script nonce`);
  assert.match(
    html,
    /<script\b[^>]*src="https:\/\/scripts\.simpleanalyticscdn\.com\/latest\.js"[^>]*>/,
    `${route}: Simple Analytics script`,
  );
  for (const match of html.matchAll(/<script\b([^>]*)>/g)) {
    assert.ok(
      match[1].includes(`nonce="${nonce}"`),
      `${route}: script without matching nonce`,
    );
  }
  assert.doesNotMatch(html, /\beval\(/, `${route}: eval in production markup`);
  assert.match(
    html,
    /<a\b[^>]*href="\/open"[^>]*>Website data<\/a>/,
    `${route}: footer link to the website data page`,
  );
  for (const match of html.matchAll(
    /(?:href|src)="(\/(?:assets|evidence|catalogue|_vinext|_next)\/[^"?#]+)(?:[?#][^"]*)?"/g,
  )) {
    const asset = match[1];
    if (checkedAssets.has(asset)) continue;
    checkedAssets.add(asset);
    const result = await fetch(new URL(asset, base));
    assert.equal(result.status, 200, asset);
    assertSecurityHeaders(result, asset);
    if (/\.(png|gif)$/.test(asset))
      assert.ok(
        result.headers.get('content-type')?.startsWith('image/'),
        asset,
      );
    if (asset.endsWith('.js')) {
      const code = await result.text();
      assert.doesNotMatch(
        code,
        /document\.cookie|cookieStore|localStorage|indexedDB/,
        `${asset}: cookies or lasting browser storage that /open does not list`,
      );
      for (const [, key] of code.matchAll(
        /sessionStorage\.setItem\(\s*([\w$]+|`[^`]*`|"[^"]*"|'[^']*')/g,
      )) {
        // Minified code passes a constant; find the string it was assigned.
        const name = key.replaceAll('$', '\\$');
        const literal = /^[`"']/.test(key)
          ? key.slice(1, -1)
          : code.match(
              new RegExp(`(?<![\\w$.])${name}\\s*=\\s*([\`"'])(.*?)\\1`),
            )?.[2];
        assert.ok(literal, `${asset}: unidentified sessionStorage key ${key}`);
        sessionKeys.add(literal);
      }
    }
    assetCount++;
  }
}
// The docs search is named by a visible label and reports results in a status
// region that is always present.
const docsPage = await (await fetch(new URL('/docs', base))).text();
const searchLabel = docsPage.match(
  /<label for="([^"]+)">Search documentation<\/label>/,
);
assert.ok(searchLabel, '/docs: visible search label');
const searchInput = docsPage.match(
  new RegExp(`<input\\b[^>]*\\bid="${searchLabel[1]}"[^>]*>`),
)?.[0];
assert.ok(searchInput, '/docs: labelled search field');
assert.doesNotMatch(
  searchInput,
  /aria-label=/,
  '/docs: search named by its label',
);
assert.match(docsPage, /<output class="search-count"><\/output>/);
const card = await fetch(new URL('/assets/og-card.png', base));
assert.equal(card.status, 200, 'share card');
assert.equal(card.headers.get('content-type'), 'image/png', 'share card type');
const cardBytes = Buffer.from(await card.arrayBuffer());
assert.deepEqual(
  [cardBytes.readUInt32BE(16), cardBytes.readUInt32BE(20)],
  [1200, 630],
  'share card size',
);
// The static catalogue page gets its own policy from public/_headers.
const catalogue = await fetch(new URL('/catalogue/', base));
assert.equal(catalogue.status, 200, '/catalogue/');
assertSecurityHeaders(catalogue, '/catalogue/');
assert.match(
  catalogue.headers.get('content-security-policy') || '',
  /^default-src 'none';.*frame-ancestors 'none'$/,
  '/catalogue/: policy',
);
const notice = await (await fetch(new URL('/open', base))).text();
for (const key of sessionKeys)
  assert.ok(
    notice.includes(`<code>${key}</code>`),
    `/open must list the ${key} browser storage key`,
  );
for (const route of [
  '/this-page-does-not-exist',
  '/docs/this-command-does-not-exist',
]) {
  const response = await fetch(new URL(route, base));
  assert.equal(response.status, 404, route);
  assertSecurityHeaders(response, route);
  assertShareCard(await response.text(), route);
}
for (const [from, to] of [
  ['/field-guide', '/docs'],
  ['/showcase', '/'],
]) {
  const response = await fetch(new URL(from, base), { redirect: 'manual' });
  assert.equal(response.status, 308, from);
  assertSecurityHeaders(response, from);
  assert.equal(response.headers.get('location'), to, from);
}
const installer = await fetch(new URL('/install.sh', base), {
  redirect: 'manual',
});
assert.equal(
  installer.status,
  200,
  'installer must be available without a sign-in or redirect',
);
assert.match(installer.headers.get('content-type') || '', /^text\/plain/);
assert.match(installer.headers.get('cache-control') || '', /no-cache/);
assertSecurityHeaders(installer, '/install.sh');
assert.equal(installer.headers.get('x-robots-tag'), 'noindex');
assert.equal(
  await installer.text(),
  await fs.readFile(new URL('../public/install.sh', import.meta.url), 'utf8'),
);
const robots = await fetch(new URL('/robots.txt', base));
assert.equal(robots.status, 200);
assert.match(
  await robots.text(),
  /Sitemap: https:\/\/fxcss\.com\/sitemap\.xml/,
);
const sitemap = await fetch(new URL('/sitemap.xml', base));
assert.equal(sitemap.status, 200);
assert.match(sitemap.headers.get('content-type') || '', /xml/);
const xml = await sitemap.text();
assert.equal([...xml.matchAll(/<loc>/g)].length, routes.length);
for (const route of routes)
  assert.ok(xml.includes(`<loc>https://fxcss.com${route}</loc>`), route);
console.log(
  `Passed: ${routes.length} pages, ${assetCount} assets, installer, canonical URLs, sitemap, redirects, security headers on pages and static files, the share card, ${namedControls} labelled controls named by their visible text, the search label and status, and ${sessionKeys.size} browser storage keys listed on /open.`,
);
