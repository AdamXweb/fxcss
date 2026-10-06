#!/usr/bin/env python3
"""Refresh site assets using fxcss itself; never edit captured screenshot pixels."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

# The site is the `website` branch; fxcss and its starter theme are on `main`.
# Pass a checkout of main: `python3 scripts/capture-evidence.py ../fxcss`
# (or `just evidence ../fxcss`).
if len(sys.argv) != 2 or not (Path(sys.argv[1]) / 'fxcss' / '__init__.py').is_file():
    sys.exit('usage: capture-evidence.py <checkout of fxcss main>')
ROOT = Path(sys.argv[1]).resolve()
SITE = Path(__file__).resolve().parents[1]
WORK = SITE / 'work' / 'screenshots'
PUBLIC = SITE / 'public' / 'evidence'
sys.path.insert(0, str(ROOT))
from fxcss import __version__, core, compare
from PIL import Image


def run(*args):
    subprocess.run([sys.executable, '-m', 'fxcss', *args], cwd=ROOT, check=True, env=os.environ)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    tmp = Path('/tmp/fxcss-site-capture')
    tmp.mkdir(exist_ok=True)
    os.environ['TMPDIR'] = str(tmp)
    firefox = core.find_firefox(None)
    starter = ROOT / 'fxcss/templates/starter'
    baseline = WORK / 'baseline-theme'
    changed = WORK / 'changed-theme'
    # A second, deliberately obvious change: the toolbar colour instead of one accent.
    toolbar = WORK / 'toolbar-theme'
    for destination in (baseline, changed, toolbar):
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(starter, destination)
    original = (baseline / 'chrome/userChrome.css').read_text()
    assert original.count('--demo-accent: #4f6ef2;') == 1
    (changed / 'chrome/userChrome.css').write_text(original.replace('--demo-accent: #4f6ef2;', '--demo-accent: #ff7139;'))
    assert original.count('--demo-toolbar: #eaeefb;') == 1
    (toolbar / 'chrome/userChrome.css').write_text(original.replace('--demo-toolbar: #eaeefb;', '--demo-toolbar: #ffe1cf;'))
    for name, theme in [('before', baseline), ('after', changed), ('toolbar-after', toolbar)]:
        run('shot', '--theme', str(theme), '--firefox', firefox, '--out', str(WORK / name))
    run('compare', '--base', str(WORK / 'before'), '--head', str(WORK / 'after'), '--out', str(WORK / 'comparison'), '--platform', sys.platform)
    run('compare', '--base', str(WORK / 'before'), '--head', str(WORK / 'toolbar-after'), '--out', str(WORK / 'toolbar-comparison'), '--platform', sys.platform)
    summary = json.loads((WORK / 'comparison/summary.json').read_text())
    record = next(v for v in summary['views'] if v['view'] == 'light-01-window')
    assert record['changed_pixels'] > 0, 'The intended accent change must be visible'
    before = Image.open(WORK / 'before/light-01-window.png')
    after = Image.open(WORK / 'after/light-01-window.png')
    assert before.size == after.size
    norm_before, norm_after = compare.normalise(before), compare.normalise(after)
    changed_pixels, total, mask = compare.diff_stats(norm_before, norm_after)
    assert record['changed_pixels'] == changed_pixels and record['total_pixels'] == total
    # The same fxcss renderer creates the highlighted panel; raw captures stay intact.
    compare.render_diff_panel(norm_before, mask).save(PUBLIC / 'difference.png', optimize=True)
    toolbar_summary = json.loads((WORK / 'toolbar-comparison/summary.json').read_text())
    toolbar_record = next(v for v in toolbar_summary['views'] if v['view'] == 'light-01-window')
    assert toolbar_record['percent'] > 10 * record['percent'], 'The toolbar change must be the obvious one'
    toolbar_after = Image.open(WORK / 'toolbar-after/light-01-window.png')
    assert toolbar_after.size == before.size
    toolbar_pixels, toolbar_total, toolbar_mask = compare.diff_stats(norm_before, compare.normalise(toolbar_after))
    assert toolbar_record['changed_pixels'] == toolbar_pixels and toolbar_record['total_pixels'] == toolbar_total
    compare.render_diff_panel(norm_before, toolbar_mask).save(PUBLIC / 'toolbar-difference.png', optimize=True)
    for source, dest in [('before/light-01-window.png','before.png'), ('after/light-01-window.png','after.png'), ('before/dark-01-window.png','dark.png')]:
        shutil.copy2(WORK / source, PUBLIC / dest)
    shutil.copy2(WORK / 'comparison/light-01-window.png', PUBLIC / 'comparison.png')
    shutil.copy2(WORK / 'comparison/summary.json', PUBLIC / 'comparison-summary.json')
    shutil.copy2(WORK / 'before/capture-coverage.json', PUBLIC / 'before-coverage.json')
    shutil.copy2(WORK / 'after/capture-coverage.json', PUBLIC / 'after-coverage.json')
    shutil.copy2(baseline / 'chrome/userChrome.css', PUBLIC / 'before.css')
    shutil.copy2(changed / 'chrome/userChrome.css', PUBLIC / 'after.css')
    shutil.copy2(WORK / 'toolbar-after/light-01-window.png', PUBLIC / 'toolbar-after.png')
    shutil.copy2(WORK / 'toolbar-comparison/light-01-window.png', PUBLIC / 'toolbar-comparison.png')
    shutil.copy2(WORK / 'toolbar-comparison/summary.json', PUBLIC / 'toolbar-comparison-summary.json')
    shutil.copy2(WORK / 'toolbar-after/capture-coverage.json', PUBLIC / 'toolbar-after-coverage.json')
    shutil.copy2(toolbar / 'chrome/userChrome.css', PUBLIC / 'toolbar-after.css')
    meta = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'fxcssVersion': __version__,
        'browser': json.loads((WORK / 'before/capture-coverage.json').read_text())['browser'],
        'theme': 'fxcss bundled starter',
        'change': {'property':'--demo-accent','before':'#4f6ef2','after':'#ff7139','mode':'light'},
        'comparison': record,
        'toolbar': {
            'change': {'property':'--demo-toolbar','before':'#eaeefb','after':'#ffe1cf','mode':'light'},
            'comparison': toolbar_record,
        },
        'dimensions': {'width': before.width, 'height': before.height},
        'noiseThreshold': compare.NOISE_THRESHOLD,
        'assets': {name: hashlib.sha256((PUBLIC/name).read_bytes()).hexdigest() for name in ['before.png','after.png','dark.png','difference.png','comparison.png','before.css','after.css','toolbar-after.png','toolbar-difference.png','toolbar-comparison.png','toolbar-after.css']},
    }
    run('catalogue', '--theme', str(baseline), '--firefox', firefox, '--out', str(SITE / 'public/catalogue'))
    (PUBLIC / 'manifest.json').write_text(json.dumps(meta, indent=2)+'\n')
    print(json.dumps(meta, indent=2), flush=True)

if __name__ == '__main__': main()
