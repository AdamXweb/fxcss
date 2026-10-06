// `npm audit --audit-level=moderate`, except for advisories listed in ACCEPTED.
// Each accepted advisory must have no patched release and must not reach the
// deployed site; the reason says why. Any other advisory at moderate or above
// fails, and an accepted one that npm no longer reports is flagged for removal.
import { spawnSync } from 'node:child_process';

const ACCEPTED = {
  'GHSA-vfj7-8cjw-p6xm':
    'braces <= 3.0.3 has no patched release. It is reached only through build ' +
    'tools (vinext > vite-plugin-commonjs > vite-plugin-dynamic-import > ' +
    'fast-glob, and the shadcn CLI), whose glob patterns come from this ' +
    'repository, never from visitors. None of it is in the deployed Worker.',
};
const LEVELS = ['info', 'low', 'moderate', 'high', 'critical'];
const threshold = LEVELS.indexOf('moderate');

const run = spawnSync('npm', ['audit', '--json'], { encoding: 'utf8' });
let report;
try {
  report = JSON.parse(run.stdout);
} catch {
  console.error(run.stdout || run.stderr);
  console.error('npm audit did not return a JSON report.');
  process.exit(2);
}
if (report.error) {
  console.error(`npm audit failed: ${report.error.summary ?? report.error.code}`);
  process.exit(2);
}

// Advisories appear as objects in `via`; plain strings are the packages that
// depend on a vulnerable package, which each advisory already covers.
const advisories = new Map();
for (const vulnerability of Object.values(report.vulnerabilities ?? {}))
  for (const via of vulnerability.via)
    if (typeof via === 'object' && LEVELS.indexOf(via.severity) >= threshold) {
      const id = via.url.split('/').pop();
      const entry = advisories.get(id) ?? { ...via, packages: new Set() };
      entry.packages.add(vulnerability.name);
      advisories.set(id, entry);
    }

const unaccepted = [...advisories].filter(([id]) => !(id in ACCEPTED));
for (const [id, advisory] of advisories)
  if (id in ACCEPTED)
    console.log(`accepted ${id} (${advisory.name}, ${advisory.severity}): ${ACCEPTED[id]}`);
for (const id of Object.keys(ACCEPTED))
  if (!advisories.has(id))
    console.log(`note: ${id} is no longer reported; remove it from ACCEPTED in scripts/audit.mjs.`);

if (unaccepted.length) {
  for (const [id, advisory] of unaccepted)
    console.error(
      `${advisory.severity.toUpperCase()} ${id} ${advisory.name} ${advisory.range}: ${advisory.title}\n  ${advisory.url}`,
    );
  console.error(`\n${unaccepted.length} advisory(ies) at moderate or above. Run npm audit for details.`);
  process.exit(1);
}
console.log('No unaccepted advisories at moderate or above.');
