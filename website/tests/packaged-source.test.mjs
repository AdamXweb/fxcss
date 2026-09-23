import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const exec = promisify(execFile);
const site = fileURLToPath(new URL('..', import.meta.url));

test('documentation rebuilds from a standalone hosting checkout', async (t) => {
  const checkout = await fs.mkdtemp(
    path.join(os.tmpdir(), 'fxcss-site-source-'),
  );
  t.after(() => fs.rm(checkout, { recursive: true, force: true }));
  for (const relative of [
    'scripts/build-content.mjs',
    'content/source',
    'public/evidence/manifest.json',
    'public/assets/icon.png',
  ]) {
    const target = path.join(checkout, relative);
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.cp(path.join(site, relative), target, { recursive: true });
  }
  await fs.symlink(
    path.join(site, 'node_modules'),
    path.join(checkout, 'node_modules'),
    'dir',
  );
  await exec(process.execPath, ['scripts/build-content.mjs'], {
    cwd: checkout,
  });
  for (const relative of ['content/docs.json', 'content/evidence.json']) {
    assert.deepEqual(
      JSON.parse(await fs.readFile(path.join(checkout, relative))),
      JSON.parse(await fs.readFile(path.join(site, relative))),
    );
  }
});
