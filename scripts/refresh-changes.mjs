import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

// Include staged, unstaged and untracked output. NUL delimiters preserve paths
// with whitespace and Git's --no-renames exposes both sides of a move.
export function refreshChangeKind(cwd = process.cwd()) {
  const paths = new Set([
    ...gitPaths(['diff', '--name-only', '--no-renames', '-z', 'HEAD', '--'], cwd),
    ...gitPaths(['ls-files', '--others', '--exclude-standard', '-z'], cwd),
  ].filter((file) => !file.startsWith('main-checkout/')));
  const outside = [...paths].filter((file) => !(
    file.startsWith('content/') || file === 'public/assets/icon.png' ||
    file.startsWith('public/search/')
  ));
  if (outside.length) {
    throw new Error(`Documentation generation changed unexpected files: ${JSON.stringify(outside)}`);
  }
  if (paths.size === 0) return 'unchanged';
  return paths.has('content/source/version.txt') ? 'version' : 'content';
}

function gitPaths(args, cwd) {
  return execFileSync('git', args, { cwd, encoding: 'utf8' }).split('\0').filter(Boolean);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    console.log(refreshChangeKind());
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
