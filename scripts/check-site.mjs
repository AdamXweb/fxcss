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
  ...data.pages.map((p) => '/docs/' + p.slug),
];
let assetCount = 0;
const checkedAssets = new Set();
for (const route of routes) {
  const response = await fetch(new URL(route, base));
  assert.equal(response.status, 200, route);
  assert.equal(
    response.headers.get('x-content-type-options'),
    'nosniff',
    `${route}: security header`,
  );
  const html = await response.text();
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
  for (const match of html.matchAll(
    /(?:href|src)="(\/(?:assets|evidence|catalogue|_vinext|_next)\/[^"?#]+)(?:[?#][^"]*)?"/g,
  )) {
    const asset = match[1];
    if (checkedAssets.has(asset)) continue;
    checkedAssets.add(asset);
    const result = await fetch(new URL(asset, base));
    assert.equal(result.status, 200, asset);
    if (/\.(png|gif)$/.test(asset))
      assert.ok(
        result.headers.get('content-type')?.startsWith('image/'),
        asset,
      );
    assetCount++;
  }
}
for (const route of [
  '/this-page-does-not-exist',
  '/docs/this-command-does-not-exist',
]) {
  const response = await fetch(new URL(route, base));
  assert.equal(response.status, 404, route);
}
for (const [from, to] of [
  ['/field-guide', '/docs'],
  ['/showcase', '/'],
]) {
  const response = await fetch(new URL(from, base), { redirect: 'manual' });
  assert.equal(response.status, 308, from);
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
assert.equal(installer.headers.get('x-content-type-options'), 'nosniff');
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
  `Passed: ${routes.length} pages, ${assetCount} assets, installer, canonical URLs, sitemap, redirects, and production security headers.`,
);
