import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const exec = promisify(execFile);
const helper = fileURLToPath(
  new URL('../scripts/prepare_catalogue.py', import.meta.url),
);
const content = `<h1>Themeable UI directory</h1>
<div class=tabs><button data-set=light onclick="showMode('light')">Light</button><button data-set=dark onclick="showMode('dark')">Dark</button></div>
<div class='card missing'><h3>Firefox View <span class=badge>not present</span></h3><p>Captured information &amp; styles.</p><details><summary>Source rules</summary><p>No rules.</p></details></div>
<div data-mode=light hidden><img src='light/overview.png' alt='Captured Firefox'></div>`;
const generated = `<!doctype html><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>Themeable UI directory</title>
<style>
:root { --bg:#fff; --fg:#1c1c1e; --muted:#6b6b70; --card:#fafafa; --accent:#315bef; }
@media (prefers-color-scheme: dark) { :root { --bg:#1c1c1e; --fg:#f2f2f7; --muted:#9a9aa0; --card:#232326; --accent:#6f8dff; } }
.tabs button[aria-selected=true] { background: var(--accent); color:#fff; border-color:transparent; }
.missing { opacity:.55; }
</style><div class=wrap>
${content}
</div><script>
function showMode(m) {
  document.querySelectorAll('[data-mode]').forEach(function (el) { el.hidden = el.dataset.mode !== m; });
  document.querySelectorAll('.tabs button').forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.set === m)); });
}
document.addEventListener('DOMContentLoaded', function () { showMode('light'); });
</script>`;

async function catalogueFile(t, source) {
  const directory = await fs.mkdtemp(
    path.join(os.tmpdir(), 'fxcss-catalogue-'),
  );
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'index.html');
  await fs.writeFile(file, source);
  return file;
}

test('publishing an older captured catalogue adds semantics without rewriting its evidence', async (t) => {
  const file = await catalogueFile(t, generated);
  await exec('python3', [helper, file]);
  const published = await fs.readFile(file, 'utf8');
  assert.match(published, /<html lang="en">/);
  assert.match(
    published,
    /<head>[\s\S]*<title>Firefox themeable UI catalogue · fxcss<\/title>[\s\S]*<\/head>/,
  );
  assert.match(published, /<meta name="robots" content="noindex,follow">/);
  assert.match(
    published,
    /<link rel="canonical" href="https:\/\/fxcss\.com\/catalogue\/">/,
  );
  assert.equal(published.match(/<main\b/g)?.length, 1);
  assert.equal(
    published.match(/<main class="wrap">\n([\s\S]*?)\n<\/main>/)?.[1],
    content,
  );
  assert.doesNotMatch(published, /aria-selected|opacity:\s*\.55/);
  assert.match(published, /\.missing \{ border-style:dashed; \}/);
  assert.match(published, /aria-pressed=true\].*color:var\(--bg\)/);
  assert.match(
    published,
    /setAttribute\('aria-pressed', String\(b\.dataset\.set === m\)\)/,
  );
  await exec('python3', [helper, file]);
  assert.equal(
    await fs.readFile(file, 'utf8'),
    published,
    'publishing must be idempotent',
  );
});

test('the checked-in catalogue already matches the publishing helper', async (t) => {
  const published = await fs.readFile(
    new URL('../public/catalogue/index.html', import.meta.url),
    'utf8',
  );
  const file = await catalogueFile(t, published);
  await exec('python3', [helper, file]);
  assert.equal(await fs.readFile(file, 'utf8'), published);
});

test('unexpected generated catalogue structure fails before changing the file', async (t) => {
  for (const source of [
    generated.replace('class=wrap', 'class=changed-container'),
    generated.replace('.missing {', '.absent {'),
    generated.replace(
      "b.setAttribute('aria-selected',",
      "b.setAttribute('data-selected',",
    ),
    generated.replace('</div><script>', '</section><script>'),
  ]) {
    const file = await catalogueFile(t, source);
    await assert.rejects(
      exec('python3', [helper, file]),
      /Cannot prepare catalogue:/,
    );
    assert.equal(await fs.readFile(file, 'utf8'), source);
  }
});
