"""Offline regression tests for the actual release workflow scripts and gates."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from tests.test_workflows import condition, script, step


WORKFLOWS = Path(__file__).resolve().parents[1] / '.github/workflows'


def job(text, name):
    return re.search(r'^  ' + re.escape(name) + r':\n(.*?)(?=^  \S|\Z)',
                     text, re.M | re.S)[1]


@unittest.skipUnless(shutil.which('bash'), 'workflow scripts require bash')
class ReleaseOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.publish = (WORKFLOWS / 'publish.yml').read_text()
        self.website = (WORKFLOWS / 'website-docs.yml').read_text()
        (self.root / 'dist').mkdir()
        self.wheel = self.root / 'dist/fxcss-9.8.7-py3-none-any.whl'
        with ZipFile(self.wheel, 'w') as wheel:
            wheel.writestr('fxcss-9.8.7.dist-info/METADATA',
                           'Metadata-Version: 2.1\nName: fxcss\nVersion: 9.8.7\n')

    def run_script(self, body, **env):
        return subprocess.run([shutil.which('bash'), '-c', body], cwd=self.root,
                              env=dict(os.environ, PYTHON=sys.executable,
                                       RUNNER_TEMP=self.root.as_posix(), **env),
                              capture_output=True, text=True, timeout=15)

    def test_release_tag_matches_built_metadata_and_manual_branch_needs_no_tag(self):
        body = 'python() { "$PYTHON" "$@"; }\n' + script(
            self.publish, 'Validate release version')
        for ref_type, ref_name, success in (
                ('tag', 'v9.8.7', True), ('tag', 'v9.8.6', False),
                ('tag', '9.8.7', False), ('branch', 'main', True),
                ('branch', 'release/preparation', True)):
            with self.subTest(ref_type=ref_type, ref_name=ref_name):
                result = self.run_script(body, RELEASE_REF_TYPE=ref_type,
                                         RELEASE_REF_NAME=ref_name)
                self.assertEqual(result.returncode == 0, success, result.stderr)
                self.assertIn('Validated fxcss 9.8.7' if success else
                              'does not match package version', result.stdout + result.stderr)
        # Manual dispatch on a tag still validates the tag; a wheel filename
        # alone must never override the package's own metadata.
        self.wheel.rename(self.root / 'dist/fxcss-1.0.0-py3-none-any.whl')
        result = self.run_script(body, RELEASE_REF_TYPE='tag', RELEASE_REF_NAME='v1.0.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected v9.8.7", result.stderr)
        block = step(self.publish, 'Validate release version')
        self.assertIn('RELEASE_REF_TYPE: ${{ github.ref_type }}', block)
        self.assertIn('RELEASE_REF_NAME: ${{ github.ref_name }}', block)

    def test_installability_retry_immediate_delayed_and_exhausted(self):
        # Functions intercept every pip/sleep invocation. Execute the real loop,
        # including its exit status, without contacting an index or waiting.
        stubs = r'''
        attempts=0
        python3() {
          attempts=$((attempts + 1))
          printf '%s\n' "$*" >> "$RUNNER_TEMP/pip.log"
          [ "$attempts" -ge "$SUCCEED_AT" ]
        }
        sleep() { printf '%s\n' "$*" >> "$RUNNER_TEMP/sleep.log"; }
        '''
        body = stubs + script(self.publish, 'Gate — wait until the release is installable')
        for succeed_at, attempts, sleeps, success in (
                (1, 1, 0, True), (4, 4, 3, True), (40, 40, 39, True),
                (41, 40, 40, False)):
            with self.subTest(succeed_at=succeed_at):
                for name in ('pip.log', 'sleep.log'):
                    (self.root / name).unlink(missing_ok=True)
                result = self.run_script(body, SUCCEED_AT=str(succeed_at))
                self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
                calls = (self.root / 'pip.log').read_text().splitlines()
                self.assertEqual(len(calls), attempts)
                self.assertEqual(set(calls), {
                    '-m pip download --no-deps --no-cache-dir --quiet '
                    f'--dest {self.root.as_posix()}/gate fxcss==9.8.7'})
                sleep_log = self.root / 'sleep.log'
                self.assertEqual(sleep_log.read_text().splitlines() if sleep_log.exists()
                                 else [], ['15'] * sleeps)
                self.assertIn(f'installable (attempt {attempts},' if success else
                              '::error::fxcss==9.8.7 still not installable', result.stdout)

    def test_website_dispatch_events_and_target(self):
        refresh = job(self.website, 'refresh')
        guard = re.search(r'^    if: >-\n(.*?)(?=^    \S)', refresh, re.M | re.S)[1]
        guard = ' '.join(line.strip() for line in guard.splitlines())
        block = step(self.website, 'Start Refresh docs on the website branch')
        command = re.search(r'^        run: (.+)$', block, re.M)[1]
        # Run the extracted command only when the extracted GitHub guard allows
        # it. The gh stub records arguments, including the target branch.
        stub = 'gh() { "$PYTHON" -c \'import json,sys; print(json.dumps(sys.argv[1:]))\' "$@"; }\n'
        for repo in ('AdamXweb/fxcss', 'someone/fxcss'):
            for event, conclusion in (('push', ''), ('workflow_run', 'success'),
                                      ('workflow_run', 'failure'), ('workflow_run', 'cancelled'),
                                      ('workflow_run', 'skipped'), ('workflow_run', 'timed_out')):
                with self.subTest(repo=repo, event=event, conclusion=conclusion):
                    eligible = condition(guard, {'github.repository': repo,
                        'github.event_name': event,
                        'github.event.workflow_run.conclusion': conclusion})
                    expected = repo == 'AdamXweb/fxcss' and (
                        event == 'push' or conclusion == 'success')
                    self.assertEqual(eligible, expected)
                    result = self.run_script(stub + (command if eligible else ':'),
                                             GITHUB_REPOSITORY=repo)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout) if result.stdout else [],
                                     ['workflow', 'run', 'refresh-docs.yml', '--repo', repo,
                                      '--ref', 'website'] if expected else [])
        triggers = self.website.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertIn('workflows: [Publish]', triggers)
        self.assertIn('types: [completed]', triggers)
        self.assertIn('branches: [main]', triggers)
        self.assertIn('paths: [README.md]', triggers)
        self.assertIn('GH_TOKEN: ${{ github.token }}', block)
        self.assertIn('permissions:\n      actions: write', refresh)

    def test_publication_requires_ci_and_checks_of_the_uploaded_distribution(self):
        checks = job(self.publish, 'checks')
        build = job(self.publish, 'build')
        package = job(self.publish, 'package-checks')
        publish = job(self.publish, 'publish')
        for guarded in (checks, build):
            self.assertIn("if: github.repository == 'AdamXweb/fxcss'", guarded)
        self.assertIn('uses: ./.github/workflows/ci.yml', checks)
        self.assertIn('needs: build', package)
        self.assertIn('uses: ./.github/workflows/package-check.yml', package)
        self.assertIn('artifact-name: dist', package)
        needs = re.search(r'^    needs: \[([^\]]+)\]', publish, re.M)[1]
        self.assertEqual({name.strip() for name in needs.split(',')},
                         {'build', 'checks', 'package-checks'})
        self.assertNotRegex(publish, r'(?m)^    if:')  # retain implicit success()
        self.assertNotIn('continue-on-error:', self.publish)
        self.assertIn('environment: pypi', publish)
        self.assertIn('id-token: write', publish)
        self.assertIn('permissions:\n  contents: read', self.publish)
        self.assertIn('tags: [\'v*\']', self.publish)
        self.assertIn('workflow_dispatch:', self.publish)
        self.assertLess(build.index('Validate release version'), build.index('actions/upload-artifact@'))
        self.assertRegex(build, r'actions/upload-artifact@[^\n]+\n        with:\n          name: dist\n          path: dist/')
        self.assertRegex(publish, r'actions/download-artifact@[^\n]+\n        with:\n          name: dist\n          path: dist/')
        self.assertLess(publish.index('gh-action-pypi-publish@'), publish.index('Gate —'))
        package_workflow = (WORKFLOWS / 'package-check.yml').read_text()
        self.assertIn('workflow_call:\n    inputs:\n      artifact-name:\n        type: string\n        required: true', package_workflow)
        self.assertIn('name: ${{ inputs.artifact-name }}', package_workflow)
        self.assertIn('path: package-dist/', package_workflow)
        self.assertIn('python scripts/package_smoke.py --dist package-dist', package_workflow)
        ci = (WORKFLOWS / 'ci.yml').read_text()
        self.assertIn('workflow_call:', ci)
        self.assertRegex(ci, r'compatibility:\n\s+description: [^\n]+\n\s+type: boolean\n\s+default: false')


if __name__ == '__main__':
    unittest.main()
