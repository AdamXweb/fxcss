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

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / 'website'
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
    for destination in (baseline, changed):
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(starter, destination)
    original = (baseline / 'chrome/userChrome.css').read_text()
    assert original.count('--demo-accent: #4f6ef2;') == 1
    (changed / 'chrome/userChrome.css').write_text(original.replace('--demo-accent: #4f6ef2;', '--demo-accent: #ff7139;'))
    for name, theme in [('before', baseline), ('after', changed)]:
        run('shot', '--theme', str(theme), '--firefox', firefox, '--out', str(WORK / name))
    run('compare', '--base', str(WORK / 'before'), '--head', str(WORK / 'after'), '--out', str(WORK / 'comparison'), '--platform', sys.platform)
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
    for source, dest in [('before/light-01-window.png','before.png'), ('after/light-01-window.png','after.png'), ('before/dark-01-window.png','dark.png')]:
        shutil.copy2(WORK / source, PUBLIC / dest)
    shutil.copy2(WORK / 'comparison/light-01-window.png', PUBLIC / 'comparison.png')
    shutil.copy2(WORK / 'comparison/summary.json', PUBLIC / 'comparison-summary.json')
    shutil.copy2(WORK / 'before/capture-coverage.json', PUBLIC / 'before-coverage.json')
    shutil.copy2(WORK / 'after/capture-coverage.json', PUBLIC / 'after-coverage.json')
    shutil.copy2(baseline / 'chrome/userChrome.css', PUBLIC / 'before.css')
    shutil.copy2(changed / 'chrome/userChrome.css', PUBLIC / 'after.css')
    meta = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'fxcssVersion': __version__,
        'browser': json.loads((WORK / 'before/capture-coverage.json').read_text())['browser'],
        'theme': 'fxcss bundled starter',
        'change': {'property':'--demo-accent','before':'#4f6ef2','after':'#ff7139','mode':'light'},
        'comparison': record,
        'dimensions': {'width': before.width, 'height': before.height},
        'noiseThreshold': compare.NOISE_THRESHOLD,
        'assets': {name: hashlib.sha256((PUBLIC/name).read_bytes()).hexdigest() for name in ['before.png','after.png','dark.png','difference.png','comparison.png','before.css','after.css']},
    }
    run('catalogue', '--theme', str(baseline), '--firefox', firefox, '--out', str(SITE / 'public/catalogue'))
    (PUBLIC / 'manifest.json').write_text(json.dumps(meta, indent=2)+'\n')
    print(json.dumps(meta, indent=2), flush=True)

if __name__ == '__main__': main()
