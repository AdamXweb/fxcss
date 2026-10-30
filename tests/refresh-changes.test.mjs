import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { refreshChangeKind } from '../scripts/refresh-changes.mjs';

function repo(t) {
  const root = mkdtempSync(path.join(tmpdir(), 'fxcss-refresh-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const git = (...args) => execFileSync('git', args, { cwd: root, stdio: 'pipe' });
  const write = (name, value = 'new') => {
    mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    writeFileSync(path.join(root, name), value);
  };
  git('init');
  write('content/source/version.txt', '0.25.0');
  write('public/search/old.json', '{}');
  write('.gitignore', '/node_modules/\n');
  git('add', '.');
  git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture');
  return { root, git, write };
}

test('new search output alone requires publication', (t) => {
  const { root, git, write } = repo(t);
  write('public/search/new-hash.json');
  git('diff', '--quiet'); // The old tracked-only gate misses this change.
  assert.equal(refreshChangeKind(root), 'content');
});

test('new source/version output and staged edits are recognized', (t) => {
  const { root, git, write } = repo(t);
  write('content/source/version.txt', '0.26.0');
  git('add', 'content/source/version.txt');
  assert.equal(refreshChangeKind(root), 'version');
});

test('tracked edits and deletions require publication', (t) => {
  const { root, write } = repo(t);
  write('public/search/old.json', '{"new":true}');
  assert.equal(refreshChangeKind(root), 'content');
  rmSync(path.join(root, 'public/search/old.json'));
  assert.equal(refreshChangeKind(root), 'content');
});

test('unexpected untracked paths fail, including paths with line breaks', (t) => {
  const { root, write } = repo(t);
  write('unexpected\nfile.txt');
  assert.throws(() => refreshChangeKind(root), /unexpected\\nfile\.txt/);
  const script = fileURLToPath(new URL('../scripts/refresh-changes.mjs', import.meta.url));
  const result = spawnSync(process.execPath, [script], { cwd: root, encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.equal(result.stdout, '');
});

test('a staged move outside permitted output is rejected', (t) => {
  const { root, git } = repo(t);
  git('mv', 'public/search/old.json', 'unexpected.json');
  assert.throws(() => refreshChangeKind(root), /unexpected\.json/);
});

test('auxiliary checkout and ignored dependencies do not trigger publication', (t) => {
  const { root, write } = repo(t);
  write('main-checkout/README.md');
  write('node_modules/example/index.js');
  assert.equal(refreshChangeKind(root), 'unchanged');
});

test('similar-looking directories do not bypass the output boundary', (t) => {
  const { root, write } = repo(t);
  write('main-checkout-elsewhere/file');
  write('public/search-unexpected/file');
  assert.throws(() => refreshChangeKind(root), /unexpected/);
});

test('refresh workflow uses the shared classifier for both publication decisions', () => {
  const workflow = readFileSync(new URL('../.github/workflows/refresh-docs.yml', import.meta.url), 'utf8');
  assert.ok(workflow.includes('changes=$(node scripts/refresh-changes.mjs)'));
  assert.ok(workflow.includes('if [ "$changes" = unchanged ]; then'));
  assert.ok(workflow.includes('if [ "$changes" = content ]; then'));
  assert.ok(!workflow.includes('git diff --quiet -- content'));
});
