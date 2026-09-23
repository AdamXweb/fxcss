import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
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
    ...docs.pages.map((p) => '/docs/' + p.slug),
  ]);
  for (const page of docs.pages) {
    assert.ok(page.html.trim(), page.slug);
    assert.doesNotMatch(page.html, /<script\b|\son\w+=|href="javascript:/i);
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
