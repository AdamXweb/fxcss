import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { marked, Renderer } from 'marked';
import sanitizeHtml from 'sanitize-html';
import { describeMarkdown, plainText } from './doc-text.mjs';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// The site lives on the `website` branch; the README it documents lives on
// `main`. FXCSS_SOURCE points at a checkout of main (`just content ../fxcss`)
// to refresh the recorded copy in content/source. Without it, the build uses
// that recorded copy, so the branch builds on its own.
const root = process.env.FXCSS_SOURCE
  ? path.resolve(process.env.FXCSS_SOURCE)
  : null;
const source = path.join(site, 'content/source');
let readme;
let version;
if (root) {
  const packageSource = await fs.readFile(
    path.join(root, 'fxcss/__init__.py'),
    'utf8',
  );
  readme = await fs.readFile(path.join(root, 'README.md'), 'utf8');
  version = packageSource.match(/__version__ = "([^"]+)"/)[1];
  await fs.mkdir(source, { recursive: true });
  await fs.writeFile(path.join(source, 'README.md'), readme);
  await fs.writeFile(path.join(source, 'version.txt'), version + '\n');
} else {
  readme = await fs.readFile(path.join(source, 'README.md'), 'utf8');
  version = (
    await fs.readFile(path.join(source, 'version.txt'), 'utf8')
  ).trim();
}
const esc = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[
        c
      ],
  );
export const slugify = (text) =>
  text
    .toLowerCase()
    .replace(/[`']/g, '')
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, '-');
const sections = [];
let current = null;
let chapter = '';
let fenced = false;
for (const line of readme.split('\n')) {
  if (line.startsWith('```')) fenced = !fenced;
  const heading = !fenced && line.match(/^(#{2,3}) (.+)$/);
  if (heading) {
    if (heading[1].length === 2) chapter = heading[2];
    current = {
      title: heading[2],
      chapter,
      level: heading[1].length,
      lines: [],
    };
    sections.push(current);
  } else if (current) current.lines.push(line);
}
const excluded = new Set([
  'Getting started',
  'Explore the toolkit',
  'How this was built',
  'Credits',
  'License',
]);
const pages = sections.filter(
  (s) => !excluded.has(s.title) && s.lines.join('\n').trim(),
);
for (const page of pages) {
  page.slug = page.title.startsWith('fxcss ')
    ? page.title.slice(6)
    : slugify(page.title);
  page.anchor = slugify(page.title);
  page.group = page.title.startsWith('fxcss ') ? 'command' : 'guide';
}
const byAnchor = new Map(pages.map((p) => [p.anchor, p.slug]));
byAnchor.set('getting-started', '');
byAnchor.set('explore-the-toolkit', '');
const images = new Map(
  ['compare', 'watch', 'catalogue', 'pick', 'icon'].map((name) => [
    `https://raw.githubusercontent.com/AdamXweb/fxcss/main/docs/${name}.png`,
    `/assets/${name}.png`,
  ]),
);
images.set(
  'https://raw.githubusercontent.com/AdamXweb/fxcss/main/docs/watch-loop.gif',
  '/assets/watch-loop.gif',
);
function linkHref(href) {
  if (href.startsWith('https://fxcss.com/'))
    return href.slice('https://fxcss.com'.length);
  if (href.startsWith('#')) {
    const target = byAnchor.get(href.slice(1));
    return target !== undefined
      ? `/docs${target ? '/' + target : ''}`
      : `https://github.com/AdamXweb/fxcss#${href.slice(1)}`;
  }
  if (href === 'LICENSE')
    return 'https://github.com/AdamXweb/fxcss/blob/main/LICENSE';
  if (href === 'examples/README.md') return '/docs/github-actions-workflows';
  return href;
}
const docs = [];
// These reference pages start with a list/table rather than introductory
// prose. Keep their summaries specific to the recorded source content.
const descriptionOverrides = {
  requirements: 'Run fxcss with Python 3.9+ and Firefox on macOS, Windows or Linux. Add Pillow for image commands with the fxcss[images] extra.',
  commands: 'Find fxcss commands for trying and installing Firefox themes, live CSS editing, screenshots, compatibility checks, and troubleshooting.',
};
for (const page of pages) {
  const codes = [];
  const headings = [];
  const search = [];
  const addPassage = (html, id) => {
    const text = plainText(html);
    if (!text) return null;
    const passageId = id || `passage-${search.length + 1}`;
    search.push({ id: passageId, text });
    return passageId;
  };
  const renderer = new Renderer();
  renderer.code = ({ text, lang }) => {
    const index = codes.push(text) - 1;
    const id = addPassage(esc(text));
    return `<div class="doc-code" id="${id}"><div class="doc-code-header"><span>${esc(lang || 'example')}</span><button type="button" data-copy-index="${index}" aria-label="Copy code example ${index + 1}">Copy</button></div><pre><code>${esc(text)}</code></pre></div>`;
  };
  const seen = new Map();
  renderer.heading = function (token) {
    const text = token.text.replace(/[`*_]/g, '');
    const base = slugify(text);
    const n = seen.get(base) || 0;
    seen.set(base, n + 1);
    const id = n ? `${base}-${n}` : base;
    const level = Math.max(2, token.depth - 2);
    headings.push({ id, text });
    const inline = this.parser.parseInline(token.tokens);
    addPassage(inline, id);
    return `<h${level} id="${id}">${inline}</h${level}>`;
  };
  renderer.paragraph = function (token) {
    const inline = this.parser.parseInline(token.tokens);
    const id = addPassage(inline);
    return `<p${id ? ` id="${id}"` : ''}>${inline}</p>`;
  };
  renderer.listitem = function (token) {
    const item = this.parser.parse(token.tokens);
    const id = addPassage(item);
    return `<li${id ? ` id="${id}"` : ''}>${item}</li>`;
  };
  renderer.table = function (token) {
    // An empty corner cell labels nothing, so it is a data cell, not a header.
    const table = Renderer.prototype.table
      .call(this, token)
      .replace(/<th( align="\w+")?>\s*<\/th>/g, '<td$1></td>');
    const id = addPassage(table);
    return table.replace('<table>', `<table id="${id}">`);
  };
  renderer.link = function (token) {
    const href = linkHref(token.href);
    // A linked badge (an image the site does not host) becomes its alt text,
    // so the link keeps a name and no second link nests inside it.
    const [only] = token.tokens;
    const inner =
      token.tokens.length === 1 &&
      only.type === 'image' &&
      !images.has(only.href)
        ? esc(only.text)
        : this.parser.parseInline(token.tokens);
    return `<a href="${esc(href)}"${href.startsWith('https://') ? ' target="_blank" rel="noreferrer noopener"' : ''}>${inner}</a>`;
  };
  renderer.image = ({ href, text }) => {
    const src = images.get(href);
    if (!src) return `<a href="${esc(href)}">${esc(text || 'View image')}</a>`;
    // Detailed captures remain available without autoplaying the old README recording.
    if (src.endsWith('.gif'))
      return '<p><a href="/docs/watch#captured-browser">See light and dark captures of the starter theme</a>.</p>';
    if (src === '/assets/compare.png')
      return '<p><a href="/#demo">Explore the verified before/after comparison</a>.</p>';
    const current =
      src === '/assets/catalogue.png'
        ? '/catalogue/light/overview.png'
        : src === '/assets/pick.png'
          ? '/catalogue/light/urlbar.png'
          : '/evidence/before.png';
    const alt =
      src === '/assets/catalogue.png'
        ? 'Annotated Firefox interface from the generated fxcss catalogue'
        : src === '/assets/pick.png'
          ? 'Address bar captured by the fxcss catalogue, corresponding to the #urlbar selector'
          : 'Actual Firefox starter-theme capture';
    return `<figure class="doc-figure"><a href="${current}" target="_blank" rel="noreferrer"><img src="${current}" alt="${alt}" loading="lazy" decoding="async" /></a><figcaption>Fresh capture from the bundled starter theme. <a href="/docs/screenshot-evidence">Capture details</a>.</figcaption></figure>`;
  };
  let markdown = page.lines.join('\n').trim();
  // A README caption belongs to its original comparison, not the refreshed website evidence.
  if (page.slug === 'compare')
    markdown = markdown.replace(
      /<p align="center"><sub>[\s\S]*?<\/sub><\/p>/,
      '',
    );
  const html = sanitizeHtml(marked.parse(markdown, { renderer, gfm: true }), {
    allowedTags: [
      ...sanitizeHtml.defaults.allowedTags,
      'img',
      'details',
      'summary',
      'button',
      'figure',
      'figcaption',
      'output',
    ],
    allowedAttributes: {
      ...sanitizeHtml.defaults.allowedAttributes,
      '*': ['id', 'class'],
      a: ['href', 'title', 'target', 'rel'],
      img: ['src', 'alt', 'width', 'height', 'loading', 'decoding'],
      button: ['type', 'data-copy-index', 'aria-label'],
    },
    allowedSchemes: ['http', 'https', 'mailto'],
    transformTags: {
      table: (_tagName, attribs) => ({
        tagName: 'table',
        attribs: { class: 'doc-table', id: attribs.id },
      }),
    },
  });
  const description = descriptionOverrides[page.slug] ?? describeMarkdown(markdown, page.title);
  docs.push({
    slug: page.slug,
    title: page.title,
    chapter: page.chapter,
    group: page.group,
    description,
    html,
    codes,
    headings,
    search,
    sourceAnchor: page.anchor,
  });
}
const output = {
  version,
  sourceSha256: crypto.createHash('sha256').update(readme).digest('hex'),
  pages: docs,
};
await fs.mkdir(path.join(site, 'content'), { recursive: true });
await fs.writeFile(
  path.join(site, 'content/docs.json'),
  JSON.stringify(output, null, 2) + '\n',
);
// A content-hashed asset can be cached independently of the nonce-bearing HTML.
const searchIndex = JSON.stringify(docs.map(({ slug, title, description, chapter, group, search }) =>
  ({ slug, title, description, chapter, group, search }),
));
const searchHash = crypto.createHash('sha256').update(searchIndex).digest('hex').slice(0, 16);
const searchName = `index-${searchHash}.json`;
const searchDirectory = path.join(site, 'public/search');
await fs.mkdir(searchDirectory, { recursive: true });
for (const file of await fs.readdir(searchDirectory)) {
  if (/^index-[a-f0-9]+\.json$/.test(file) && file !== searchName)
    await fs.unlink(path.join(searchDirectory, file));
}
await fs.writeFile(path.join(searchDirectory, searchName), searchIndex);
await fs.writeFile(path.join(site, 'content/search-index.json'), JSON.stringify({ url: `/search/${searchName}` }, null, 2) + '\n');
// Keep public downloads outside the module graph; share the same recorded data.
await fs.copyFile(
  path.join(site, 'public/evidence/manifest.json'),
  path.join(site, 'content/evidence.json'),
);
await fs.mkdir(path.join(site, 'public/assets'), { recursive: true });
if (root)
  await fs.copyFile(
    path.join(root, 'docs/icon.png'),
    path.join(site, 'public/assets/icon.png'),
  );
await fs.access(path.join(site, 'public/assets/icon.png'));
console.log(
  `Generated ${docs.length} documentation pages for fxcss ${version}.`,
);
