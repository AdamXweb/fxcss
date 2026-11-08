"""Offline regression tests for the actual release workflow scripts and gates."""

import importlib.util
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


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github/workflows'
# Records each gh call as a JSON argument list, one per line.
GH_STUB = 'gh() { "$PYTHON" -c \'import json,sys; print(json.dumps(sys.argv[1:]))\' "$@"; }\n'


def job(text, name):
    return re.search(r'^  ' + re.escape(name) + r':\n(.*?)(?=^  \S|\Z)',
                     text, re.M | re.S)[1]


def steps(text):
    return re.findall(r'^      - name: (.+)$', text, re.M)


def one_line_run(text, name):
    return re.search(r'^        run: (.+)$', step(text, name), re.M)[1]


def load_release_rules():
    spec = importlib.util.spec_from_file_location('fxcss_release_rules',
                                                  ROOT / 'scripts/release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load_release_rules()


def isolated_git_env(root):
    """Git that ignores the developer's own config, such as commit signing."""
    config = Path(root) / 'gitconfig'
    config.write_text('[user]\n\tname = fxcss tests\n\temail = tests@example.invalid\n'
                      '[init]\n\tdefaultBranch = main\n'
                      '[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n')
    return dict(GIT_CONFIG_GLOBAL=config.as_posix(), GIT_CONFIG_NOSYSTEM='1')


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
        guard = re.search(r'^    if: (.+)$', refresh, re.M)[1]
        block = step(self.website, 'Start Refresh docs on the website branch')
        command = re.search(r'^        run: (.+)$', block, re.M)[1]
        # Run the extracted command only when the extracted GitHub guard allows
        # it. The gh stub records arguments, including the target branch.
        for repo in ('AdamXweb/fxcss', 'someone/fxcss'):
            with self.subTest(repo=repo):
                eligible = condition(guard, {'github.repository': repo})
                self.assertEqual(eligible, repo == 'AdamXweb/fxcss')
                result = self.run_script(GH_STUB + (command if eligible else ':'),
                                         GITHUB_REPOSITORY=repo)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout) if result.stdout else [],
                                 ['workflow', 'run', 'refresh-docs.yml', '--repo', repo,
                                  '--ref', 'website'] if eligible else [])
        # Publish starts the refresh after a release, so a workflow_run trigger
        # here would refresh twice.
        triggers = self.website.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertNotIn('workflow_run', triggers)
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

    def test_build_records_the_version_the_follow_up_reads(self):
        build = job(self.publish, 'build')
        self.assertIn('version: ${{ steps.version.outputs.version }}', build)
        self.assertLess(build.index('Validate release version'), build.index('Record the version'))
        self.assertIn('id: version', step(self.publish, 'Record the version'))
        output = self.root / 'output'
        output.write_text('')
        result = self.run_script(one_line_run(self.publish, 'Record the version'),
                                 GITHUB_OUTPUT=output.as_posix())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(), 'version=9.8.7\n')

    def test_follow_up_waits_for_pypi_and_runs_each_step_after_a_failure(self):
        follow = job(self.publish, 'follow-up')
        needs = re.search(r'^    needs: \[([^\]]+)\]', follow, re.M)[1]
        self.assertEqual({name.strip() for name in needs.split(',')}, {'build', 'publish'})
        self.assertNotRegex(follow, r'(?m)^    if:')  # only after the gate succeeds
        self.assertIn('contents: write', follow)
        self.assertIn('actions: write', follow)
        self.assertRegex(follow, r'actions/checkout@[^\n]+\n        with:\n          ref: main')
        self.assertEqual(steps(follow), ['Move the README pins', 'Refresh the website',
                                         'Create the GitHub release'])
        self.assertNotIn('if:', step(self.publish, 'Move the README pins'))
        for name in ('Refresh the website', 'Create the GitHub release'):
            self.assertIn('if: ${{ !cancelled() }}', step(self.publish, name))
        for name in ('Move the README pins', 'Create the GitHub release'):
            self.assertIn('VERSION: ${{ needs.build.outputs.version }}', step(self.publish, name))
        result = self.run_script(GH_STUB + one_line_run(self.publish, 'Refresh the website'),
                                 GITHUB_REPOSITORY='AdamXweb/fxcss')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['workflow', 'run', 'refresh-docs.yml',
                         '--repo', 'AdamXweb/fxcss', '--ref', 'website'])

    def test_follow_up_creates_the_release_only_when_it_is_missing(self):
        stub = r'''
        gh() {
          printf '%s\n' "$*" >> "$RUNNER_TEMP/gh.log"
          [ "$1 $2" != "release view" ] || [ -n "$RELEASE_EXISTS" ]
        }
        '''
        body = stub + script(self.publish, 'Create the GitHub release')
        log = self.root / 'gh.log'
        for exists in ('', '1'):
            with self.subTest(exists=bool(exists)):
                log.unlink(missing_ok=True)
                result = self.run_script(body, VERSION='9.8.7', RELEASE_EXISTS=exists,
                                         GITHUB_REPOSITORY='AdamXweb/fxcss', GITHUB_SHA='abc123')
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = log.read_text().splitlines()
                self.assertEqual(calls[0], 'release view v9.8.7 --repo AdamXweb/fxcss')
                self.assertEqual(calls[1:], [] if exists else [
                    'release create v9.8.7 --repo AdamXweb/fxcss --target abc123 '
                    '--title v9.8.7 --generate-notes'])

    @unittest.skipUnless(shutil.which('git'), 'the pin update pushes with git')
    def test_follow_up_pushes_the_pin_update_after_main_moves(self):
        env = isolated_git_env(self.root)

        def git(*args, cwd):
            return subprocess.run(['git', *args], cwd=cwd, env=dict(os.environ, **env),
                                  check=True, capture_output=True, text=True).stdout

        origin, seed, work = self.root / 'origin.git', self.root / 'seed', self.root / 'work'
        git('init', '-q', '--bare', origin.as_posix(), cwd=self.root)
        git('clone', '-q', origin.as_posix(), seed.as_posix(), cwd=self.root)
        (seed / 'scripts').mkdir()
        shutil.copy(ROOT / 'scripts/release.py', seed / 'scripts/release.py')
        (seed / 'README.md').write_text('pip install "fxcss[images]==9.8.6"\n')
        git('add', '.', cwd=seed)
        git('commit', '-q', '-m', 'Start', cwd=seed)
        git('push', '-q', 'origin', 'HEAD:main', cwd=seed)
        git('clone', '-q', origin.as_posix(), work.as_posix(), cwd=self.root)
        # main moves after the job's checkout, so the first push is rejected.
        (seed / 'other.txt').write_text('a concurrent change')
        git('add', '.', cwd=seed)
        git('commit', '-q', '-m', 'Concurrent change', cwd=seed)
        git('push', '-q', 'origin', 'HEAD:main', cwd=seed)

        body = 'python3() { "$PYTHON" "$@"; }\n' + script(self.publish, 'Move the README pins')
        run = lambda: subprocess.run([shutil.which('bash'), '-c', body], cwd=work,
                                     env=dict(os.environ, PYTHON=sys.executable,
                                              VERSION='9.8.7', **env),
                                     capture_output=True, text=True, timeout=60)
        result = run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('::notice title=README pins::Moved to fxcss 9.8.7', result.stdout)
        log = git('log', '--format=%s', 'main', cwd=origin).splitlines()
        self.assertEqual(log, ['Update the README pins to fxcss 9.8.7', 'Concurrent change', 'Start'])
        self.assertEqual(git('show', 'main:README.md', cwd=origin),
                         'pip install "fxcss[images]==9.8.7"\n')
        # Already current: nothing to commit or push.
        result = run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(git('log', '--format=%s', 'main', cwd=origin).splitlines()), 3)

    def test_release_runs_on_main_for_version_changes_and_by_hand(self):
        text = (WORKFLOWS / 'release.yml').read_text()
        triggers = text.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertIn('push:\n    branches: [main]\n    paths: [fxcss/__init__.py]', triggers)
        self.assertIn('workflow_dispatch:', triggers)
        self.assertIn('permissions:\n  contents: read', text)
        tag = job(text, 'tag')
        guard = re.search(r'^    if: (.+)$', tag, re.M)[1]
        for repo, ref, expected in (('AdamXweb/fxcss', 'refs/heads/main', True),
                                    ('AdamXweb/fxcss', 'refs/heads/feature', False),
                                    ('someone/fxcss', 'refs/heads/main', False)):
            with self.subTest(repo=repo, ref=ref):
                self.assertEqual(condition(guard, {'github.repository': repo,
                                                   'github.ref': ref}), expected)
        for permission in ('contents: write', 'actions: write', 'pull-requests: read'):
            self.assertIn(permission, tag)
        self.assertIn('fetch-depth: 0', tag)  # the plan reads every tag
        self.assertEqual(steps(tag), ['Find the release pull request', 'Decide whether to release',
                                      'Tag the release', 'Start Publish on the tag'])
        self.assertIn("if: github.event_name == 'push'", step(text, 'Find the release pull request'))
        for name in ('Tag the release', 'Start Publish on the tag'):
            self.assertIn("if: steps.plan.outputs.release == 'yes'", step(text, name))
        # The title reaches the script through the environment, never the shell text.
        plan = step(text, 'Decide whether to release')
        self.assertIn('PR_TITLE: ${{ steps.pr.outputs.title }}', plan)
        self.assertIn('EVENT_NAME: ${{ github.event_name }}', plan)
        self.assertEqual(one_line_run(text, 'Decide whether to release'),
                         'python3 scripts/release.py plan')

    def test_release_finds_the_title_tags_and_starts_publish(self):
        text = (WORKFLOWS / 'release.yml').read_text()
        output = self.root / 'output'
        stub = 'gh() { printf "%s\\n" "$*" >> "$RUNNER_TEMP/gh.log"; printf "%s\\n" "$STUB_TITLE"; }\n'
        for title in ('Prepare v9.8.7 (#99)', '', 'Say "hi" $(exit 3) `exit 4`'):
            with self.subTest(title=title):
                output.write_text('')
                (self.root / 'gh.log').unlink(missing_ok=True)
                result = self.run_script(stub + script(text, 'Find the release pull request'),
                                         STUB_TITLE=title, GITHUB_OUTPUT=output.as_posix(),
                                         GITHUB_REPOSITORY='AdamXweb/fxcss', GITHUB_SHA='abc123')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_text(), f'title={title}\n')
                self.assertEqual((self.root / 'gh.log').read_text(),
                                 'api repos/AdamXweb/fxcss/commits/abc123/pulls --jq '
                                 '[.[] | select(.merged_at != null)][0].title // ""\n')
        result = self.run_script(GH_STUB + script(text, 'Start Publish on the tag'),
                                 VERSION='9.8.7', GITHUB_REPOSITORY='AdamXweb/fxcss')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[0]),
                         ['workflow', 'run', 'publish.yml', '--repo', 'AdamXweb/fxcss',
                          '--ref', 'v9.8.7'])

    @unittest.skipUnless(shutil.which('git'), 'tagging uses git')
    def test_release_pushes_the_tag_for_the_commit(self):
        text = (WORKFLOWS / 'release.yml').read_text()
        env = isolated_git_env(self.root)

        def git(*args, cwd):
            return subprocess.run(['git', *args], cwd=cwd, env=dict(os.environ, **env),
                                  check=True, capture_output=True, text=True).stdout

        origin, work = self.root / 'origin.git', self.root / 'work'
        git('init', '-q', '--bare', origin.as_posix(), cwd=self.root)
        git('clone', '-q', origin.as_posix(), work.as_posix(), cwd=self.root)
        for message in ('Prepare v9.8.7', 'Later change'):
            git('commit', '-q', '--allow-empty', '-m', message, cwd=work)
        git('push', '-q', 'origin', 'HEAD:main', cwd=work)
        released = git('rev-parse', 'HEAD~1', cwd=work).strip()
        result = subprocess.run([shutil.which('bash'), '-c', script(text, 'Tag the release')],
                                cwd=work, env=dict(os.environ, VERSION='9.8.7',
                                                   GITHUB_SHA=released, **env),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git('rev-parse', 'v9.8.7^{commit}', cwd=origin).strip(), released)

    def test_release_version_check_runs_on_retitles_with_the_base_and_title(self):
        text = (WORKFLOWS / 'release-version.yml').read_text()
        triggers = text.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertIn('types: [opened, edited, synchronize, reopened]', triggers)
        self.assertIn('permissions:\n  contents: read', text)
        check = step(text, 'Check the version change')
        self.assertIn('BASE_SHA: ${{ github.event.pull_request.base.sha }}', check)
        self.assertIn('PR_TITLE: ${{ github.event.pull_request.title }}', check)
        self.assertEqual(one_line_run(text, 'Check the version change'),
                         'python3 scripts/release.py check-pr "$BASE_SHA"')
        self.assertIn('fetch-depth: 0', text)


class ReleaseRuleTests(unittest.TestCase):
    TAGS = ['v0.9.0', 'v0.24.0', 'v0.25.0', 'not-a-version', 'v1.0']

    def test_only_a_release_pull_request_may_change_the_version(self):
        self.assertEqual(release.check_pr('0.25.0', '0.25.0', 'Add a thing', self.TAGS), [])
        for title in ('Prepare v0.26.0', 'prepare v0.26.0',
                      'Fix Firefox sidebar CI and prepare v0.26.0'):
            with self.subTest(title=title):
                self.assertEqual(release.check_pr('0.25.0', '0.26.0', title, self.TAGS), [])
        for title in ('Preserve theme customizations', 'Prepare v0.26.1', 'Prepare v0.26.0.1',
                      'Prepare v0.26.01', 'Prepare 0.26.0', ''):
            with self.subTest(title=title):
                problems = release.check_pr('0.25.0', '0.26.0', title, self.TAGS)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn('title it "Prepare v0.26.0"', problems[0])
        downgrade = release.check_pr('0.25.0', '0.24.1', 'Prepare v0.24.1', self.TAGS)
        self.assertEqual(len(downgrade), 1, downgrade)
        self.assertIn('a release must increase it', downgrade[0])
        self.assertEqual(release.check_pr('0.24.0', '0.25.0', 'Prepare v0.25.0', self.TAGS),
                         ['v0.25.0 is already tagged; choose the next version.'])

    def test_plan_releases_new_versions_from_a_release_pull_request_or_by_hand(self):
        for version, event, title, expected, message in (
                ('0.25.0', 'push', 'Prepare v0.25.0 (#82)', False, 'already tagged'),
                ('0.24.1', 'workflow_dispatch', '', False, 'error: '),
                ('0.26.0', 'push', 'Prepare v0.26.0 (#97)', True, 'Releasing fxcss 0.26.0.'),
                ('0.26.0', 'push', 'Preserve theme customizations (#91)', False, 'error: '),
                ('0.26.0', 'push', '', False, 'error: '),
                ('0.26.0', 'workflow_dispatch', '', True, 'Releasing fxcss 0.26.0.'),
                ('0.10.0', 'workflow_dispatch', '', False, 'error: ')):
            with self.subTest(version=version, event=event, title=title):
                result, text = release.plan(version, self.TAGS, event, title)
                self.assertEqual(result, expected)
                self.assertIn(message, text)
                if message != 'error: ':
                    self.assertFalse(text.startswith('error: '), text)
        self.assertEqual(release.plan('0.1.0', [], 'push', 'Prepare v0.1.0'),
                         (True, 'Releasing fxcss 0.1.0.'))

    def test_versions_are_strict_and_compared_as_numbers(self):
        self.assertEqual(release.read_version('"""x"""\n\n__version__ = "0.26.0"\n'), '0.26.0')
        for text in ('__version__ = "0.26"\n', '__version__ = "0.26.0rc1"\n', 'version = 1\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                release.read_version(text)
        self.assertLess(release.parse('0.9.0'), release.parse('0.25.0'))
        self.assertEqual(release.released(self.TAGS), ['0.9.0', '0.24.0', '0.25.0'])

    def test_pins_move_forward_only(self):
        text = ('pip install "fxcss[images]==0.24.0"\n'
                '- run: pip install fxcss==0.24.0\n'
                'pip install "fxcss[images,extra]==0.27.0"\n'
                'needs fxcss **0.26.0 or later**; fxcss==0.26.0 already\n')
        moved, count = release.bump_pins(text, '0.26.0')
        self.assertEqual(count, 2)
        self.assertEqual(moved, text.replace('==0.24.0', '==0.26.0'))
        self.assertEqual(release.bump_pins(moved, '0.26.0'), (moved, 0))

    def test_a_release_moves_only_the_readme_pins(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        version = release.read_version((ROOT / 'fxcss/__init__.py').read_text(encoding='utf-8'))
        pins = release.PIN.findall(readme)
        self.assertTrue(pins, 'the README has no fxcss pins for a release to move')
        for _, pinned in pins:
            self.assertLessEqual(release.parse(pinned), release.parse(version))
        moved, count = release.bump_pins(readme, '99.0.0')
        self.assertEqual(count, len(pins))
        self.assertEqual(release.PIN.sub('PIN', moved), release.PIN.sub('PIN', readme))


@unittest.skipUnless(shutil.which('git'), 'the release rules read git')
class ReleaseCommandTests(unittest.TestCase):
    """The commands the workflows run, against a real repository and its tags."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = isolated_git_env(self.root)
        self.repo = self.root / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        (self.repo / 'fxcss').mkdir()
        shutil.copy(ROOT / 'scripts/release.py', self.repo / 'scripts/release.py')
        self.set_version('0.25.0')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'Prepare v0.25.0')
        self.git('tag', 'v0.25.0')
        self.base = self.git('rev-parse', 'HEAD').strip()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, env=dict(os.environ, **self.env),
                              check=True, capture_output=True, text=True).stdout

    def set_version(self, version):
        (self.repo / 'fxcss/__init__.py').write_text(f'"""fxcss."""\n\n__version__ = "{version}"\n')

    def run_release(self, *args, **env):
        output = self.root / 'output'
        output.write_text('')
        result = subprocess.run([sys.executable, 'scripts/release.py', *args], cwd=self.repo,
                                env=dict(os.environ, GITHUB_OUTPUT=output.as_posix(),
                                         **self.env, **env),
                                capture_output=True, text=True, timeout=30)
        return result, output.read_text()

    def test_check_pr_compares_the_base_with_the_checkout(self):
        result, _ = self.run_release('check-pr', self.base, PR_TITLE='Add a thing')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('fxcss 0.25.0: no version change.', result.stdout)
        self.set_version('0.26.0')
        result, _ = self.run_release('check-pr', self.base, PR_TITLE='Add a thing')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('::error title=Release version::This pull request changes the version '
                      'from 0.25.0 to 0.26.0', result.stdout)
        result, _ = self.run_release('check-pr', self.base, PR_TITLE='Prepare v0.26.0')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('fxcss 0.26.0: release PR from 0.25.0.', result.stdout)

    def test_plan_writes_the_outputs_the_release_workflow_reads(self):
        result, output = self.run_release('plan', EVENT_NAME='push', PR_TITLE='')
        self.assertEqual((result.returncode, output), (0, 'version=0.25.0\nrelease=no\n'))
        self.assertIn('::notice title=Release::fxcss 0.25.0 is already tagged', result.stdout)
        self.set_version('0.26.0')
        result, output = self.run_release('plan', EVENT_NAME='push', PR_TITLE='Prepare v0.26.0 (#97)')
        self.assertEqual((result.returncode, output), (0, 'version=0.26.0\nrelease=yes\n'))
        result, output = self.run_release('plan', EVENT_NAME='push', PR_TITLE='Add a thing (#91)')
        self.assertEqual((result.returncode, output), (1, 'version=0.26.0\nrelease=no\n'))
        self.assertIn('::error title=Release::main is at fxcss 0.26.0, which is not tagged',
                      result.stdout)

    def test_bump_pins_reports_whether_it_changed_the_file(self):
        readme = self.repo / 'README.md'
        readme.write_text('pip install "fxcss[images]==0.24.0"\n')
        result, output = self.run_release('bump-pins', '0.25.0', 'README.md')
        self.assertEqual((result.returncode, output), (0, 'changed=yes\n'), result.stderr)
        self.assertEqual(readme.read_text(), 'pip install "fxcss[images]==0.25.0"\n')
        result, output = self.run_release('bump-pins', '0.25.0', 'README.md')
        self.assertEqual((result.returncode, output), (0, 'changed=no\n'), result.stderr)


if __name__ == '__main__':
    unittest.main()
