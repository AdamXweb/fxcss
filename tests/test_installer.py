"""Exercise installer flows with disposable command stubs; never install on the host."""
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'public/install.sh'
STUB = r'''
import json, os, pathlib, shutil, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
env = os.environ
with open(env['SETUP_TEST_LOG'], 'a') as out:
    out.write(json.dumps([name, *args]) + '\n')
apps = pathlib.Path(env['PIPX_BIN_DIR'])
state = pathlib.Path(env['SETUP_TEST_STATE'])
def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(env['SETUP_TEST_STUB'], path)
    path.chmod(0o755)
def done(code=0): sys.exit(code)
if name == 'uname': print(env.get('SETUP_TEST_OS', 'Linux')); done()
if name == 'id': print(env.get('SETUP_TEST_UID', '1000')); done()
if name == 'fxcss':
    print('fxcss 0.22.0'); done(int(env.get('SETUP_TEST_VERIFY_FAIL', '0')))
if name in ('python3', 'python'):
    if args[0] == '-c': done(int(env.get('SETUP_TEST_PYTHON_FAIL', '0')))
    if args[:2] == ['-m', 'venv']:
        if env.get('SETUP_TEST_VENV_FAIL'): done(1)
        executable(pathlib.Path(args[2]) / 'bin/python')
        executable(pathlib.Path(args[2]) / 'bin/pipx')
        done()
    if args[:3] == ['-m', 'pip', 'install']: done(int(env.get('SETUP_TEST_DOWNLOAD_FAIL', '0')))
if name == 'brew':
    if args == ['--prefix']: print(env['SETUP_TEST_BREW_PREFIX']); done()
    if env.get('SETUP_TEST_BREW_FAIL'): done(1)
    executable(pathlib.Path(env['SETUP_TEST_BREW_PREFIX']) / 'bin/pipx'); done()
if name == 'pipx':
    if args == ['--version']: print('1.17.2'); done()
    if args[:1] == ['environment']: print(str(apps)); done()
    if args[:3] == ['runpip', 'fxcss', 'show']:
        if args[3] == 'fxcss': done(0 if env.get('SETUP_TEST_INSTALLED') or state.exists() else 1)
        done(0 if env.get('SETUP_TEST_PILLOW') or state.exists() else 1)
    if args[:1] == ['install']:
        if env.get('SETUP_TEST_INSTALL_FAIL'): done(1)
        if args[1] == 'pipx': executable(apps / 'pipx')
        else: state.touch(); executable(apps / 'fxcss')
        done()
    if args[:1] == ['inject']: state.touch(); done()
    if args == ['ensurepath']: done(int(env.get('SETUP_TEST_PATH_FAIL', '0')))
print('Unexpected stub invocation', name, args, file=sys.stderr)
done(99)
'''

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fxcss-installer-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'commands'; self.bin.mkdir()
        self.apps = self.root / 'apps with spaces'; self.apps.mkdir()
        self.tmp = self.root / 'temporary'; self.tmp.mkdir()
        self.stub = self.root / 'stub'
        self.stub.write_text(f'#!{sys.executable}\n' + STUB)
        self.stub.chmod(0o755)
        self.log = self.root / 'calls.jsonl'
        self.env = dict(os.environ, PATH=str(self.bin), PIPX_BIN_DIR=str(self.apps),
                        PIPX_HOME=str(self.root / 'pipx'), TMPDIR=str(self.tmp),
                        SETUP_TEST_LOG=str(self.log), SETUP_TEST_STATE=str(self.root / 'installed'),
                        SETUP_TEST_STUB=str(self.stub), SETUP_TEST_BREW_PREFIX=str(self.root / 'brew'))
        for command in ['pipx', 'uname', 'id', 'python3']:
            self.add_command(command)
        for command in ['cat', 'mktemp', 'rm']:
            (self.bin / command).symlink_to(shutil.which(command))
        shutil.copyfile(self.stub, self.apps / 'fxcss')
        (self.apps / 'fxcss').chmod(0o755)

    def add_command(self, name):
        shutil.copyfile(self.stub, self.bin / name)
        (self.bin / name).chmod(0o755)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_setup(self, *args):
        return subprocess.run(['/bin/bash', str(SCRIPT), *args], env=self.env,
                              stdin=subprocess.DEVNULL, text=True, capture_output=True,
                              start_new_session=True, timeout=10)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_help_and_invalid_options_do_not_install(self):
        self.assert_success(self.run_setup('--help'))
        self.assertNotEqual(self.run_setup('--yes', '--intent', 'unknown').returncode, 0)
        self.assertNotEqual(self.run_setup('--intent').returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_truncated_download_cannot_start_installation(self):
        partial = SCRIPT.read_text()[:len(SCRIPT.read_text()) // 2]
        result = subprocess.run(['/bin/bash'], input=partial, text=True,
                                capture_output=True, env=self.env, start_new_session=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_unattended_requires_explicit_yes(self):
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No interactive terminal', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_each_intent_installs_and_prints_matching_next_step(self):
        expected = {'use': 'fxcss try ', 'build': 'fxcss new my-theme',
                    'maintain': 'fxcss init --watch --showcase', 'install': 'fxcss doctor'}
        for intent, command in expected.items():
            with self.subTest(intent=intent):
                result = self.run_setup('--yes', '--intent', intent)
                self.assert_success(result)
                self.assertIn(command, result.stdout)
        self.assertIn(['pipx', 'install', 'fxcss[images]'], self.calls())
        self.assertIn(['pipx', 'ensurepath'], self.calls())
        self.assertTrue(all(call == ['fxcss', '--version'] for call in self.calls() if call[0] == 'fxcss'))

    def test_existing_install_is_preserved_and_missing_images_added(self):
        self.env['SETUP_TEST_INSTALLED'] = '1'
        self.assert_success(self.run_setup('--yes'))
        self.assertIn(['pipx', 'inject', 'fxcss', 'pillow>=10.1'], self.calls())
        self.assertFalse(any(c[1] in ['install', 'upgrade'] for c in self.calls() if c[0] == 'pipx'))

    def test_complete_existing_install_needs_no_package_changes(self):
        self.env.update(SETUP_TEST_INSTALLED='1', SETUP_TEST_PILLOW='1')
        self.assert_success(self.run_setup('--yes'))
        self.assertFalse(any(c[1] in ['install', 'upgrade', 'inject'] for c in self.calls() if c[0] == 'pipx'))

    def test_bootstrap_creates_persistent_pipx_then_cleans_temporary_venv(self):
        (self.bin / 'pipx').unlink()
        self.assert_success(self.run_setup('--yes'))
        self.assertTrue((self.apps / 'pipx').exists())
        self.assertTrue(any(c[:3] == ['pipx', 'install', 'pipx'] for c in self.calls()))
        self.assertIn(['pipx', 'install', 'fxcss[images]'], self.calls())
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_homebrew_installs_pipx_on_macos(self):
        (self.bin / 'pipx').unlink()
        self.add_command('brew'); self.env['SETUP_TEST_OS'] = 'Darwin'
        self.assert_success(self.run_setup('--yes'))
        self.assertIn(['brew', 'install', 'pipx'], self.calls())

    def test_bootstrap_failures_stop_and_clean_up(self):
        (self.bin / 'pipx').unlink()
        for flag in ['SETUP_TEST_PYTHON_FAIL', 'SETUP_TEST_VENV_FAIL', 'SETUP_TEST_DOWNLOAD_FAIL']:
            with self.subTest(flag=flag):
                self.env[flag] = '1'
                result = self.run_setup('--yes')
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('fxcss is installed.', result.stdout)
                self.assertEqual(list(self.tmp.iterdir()), [])
                del self.env[flag]

    def test_install_and_verification_failures_are_not_reported_as_success(self):
        for flag in ['SETUP_TEST_INSTALL_FAIL', 'SETUP_TEST_VERIFY_FAIL']:
            with self.subTest(flag=flag):
                self.env[flag] = '1'
                result = self.run_setup('--yes')
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('fxcss is installed.', result.stdout)
                del self.env[flag]

    def test_root_and_unsupported_platform_stop_before_install(self):
        for flag, value in [('SETUP_TEST_UID', '0'), ('SETUP_TEST_OS', 'MINGW64_NT')]:
            self.env[flag] = value
            self.assertNotEqual(self.run_setup('--yes').returncode, 0)
            del self.env[flag]
        self.assertFalse(any(c[0] == 'pipx' for c in self.calls()))

    def test_path_failure_prints_recovery_after_successful_install(self):
        self.env['SETUP_TEST_PATH_FAIL'] = '1'
        result = self.run_setup('--yes')
        self.assert_success(result)
        self.assertIn('Add this directory to your shell PATH:', result.stdout)

    def run_piped(self, answers):
        pid, fd = pty.fork()
        if pid == 0:
            command = f'/bin/cat {shlex.quote(str(SCRIPT))} | /bin/bash'
            os.execve('/bin/bash', ['bash', '-c', command], self.env)
        output = b''; sent = 0; finished = False
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                if select.select([fd], [], [], 0.1)[0]:
                    try: chunk = os.read(fd, 65536)
                    except OSError: chunk = b''
                    if not chunk:
                        # PTY EOF can arrive just before the child's wait status.
                        _, status = os.waitpid(pid, 0)
                        finished = True
                        return os.waitstatus_to_exitcode(status), output.decode()
                    output += chunk
                    if sent < len(answers) and answers[sent][0].encode() in output:
                        os.write(fd, answers[sent][1].encode() + b'\n'); sent += 1
                got, status = os.waitpid(pid, os.WNOHANG)
                if got:
                    finished = True; return os.waitstatus_to_exitcode(status), output.decode()
            got, status = os.waitpid(pid, os.WNOHANG)
            if got:
                finished = True; return os.waitstatus_to_exitcode(status), output.decode()
            self.fail('Piped setup did not finish: ' + output.decode())
        finally:
            if not finished:
                os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
            os.close(fd)

    def test_piped_script_reads_menu_and_confirmation_from_tty(self):
        status, output = self.run_piped([('Choose [1]:', '2'), ('Continue? [Y/n]:', 'y')])
        self.assertEqual(status, 0, output)
        self.assertIn('fxcss new my-theme', output)
        self.assertIn(['pipx', 'install', 'fxcss[images]'], self.calls())

    def test_cancel_from_piped_menu_does_not_install(self):
        status, output = self.run_piped([('Choose [1]:', '0')])
        self.assertEqual(status, 0, output)
        self.assertIn('Cancelled.', output)
        self.assertEqual(self.calls(), [])

    def test_declining_confirmation_does_not_install(self):
        status, output = self.run_piped([('Choose [1]:', '1'), ('Continue? [Y/n]:', 'n')])
        self.assertEqual(status, 0, output)
        self.assertFalse(any(c[0] == 'pipx' for c in self.calls()))

if __name__ == '__main__':
    unittest.main()
