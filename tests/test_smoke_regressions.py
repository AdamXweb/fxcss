"""Mocked coverage for the fixtures and startup diagnostics the smoke runs rely on.

The smoke modules need a real Firefox. These tests pin the parts that do not:
what the fixture themes contain, how install and try place their files, how a
fixture navigation timeout is recorded and classified, and how the Windows
process check reads tasklist.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from fxcss import core, fetch, install
from tests import smoke_startup, smoke_variants
from tests.theme_fixtures import EXPECTED_LAYOUTS, SHIPPED_CUSTOM_CHROME, write_themes

# Verbatim from the windows-latest run of PR #85 at 1ebe0c9 (CI run
# 41004012303): the content café session's first fixture navigation timed out
# a few seconds after a fresh start, with no record of the tab's state.
CI_TIMEOUT = ("WebDriver:Navigate failed: {'error': 'timeout', 'message': "
              "'Navigation timed out after 30000 ms', 'stacktrace': "
              "'RemoteError@chrome://remote/content/shared/RemoteError.sys.mjs:14:8'}")
DISCARDED = ("WebDriver:Navigate failed: {'error': 'no such window', "
             "'message': 'Browsing context has been discarded'}")
UNATTACHED = {"url": "about:blank", "remoteType": None, "process": None,
              "document": "about:blank", "discarded": False, "loading": True,
              "busy": True, "tabs": 2, "contentProcesses": 0}
ATTACHED = dict(UNATTACHED, remoteType="file", process=4321, contentProcesses=3)


class FixtureNavigationTimeoutTests(unittest.TestCase):
    def navigate(self, outcomes, state=ATTACHED):
        session = core.Session.__new__(core.Session)
        session.m = Mock()
        session.m.command.side_effect = outcomes
        if isinstance(state, Exception):
            session.m.script.side_effect = state
        else:
            session.m.script.return_value = state
        with patch.object(core.time, "sleep"):
            with self.assertRaises(core.MarionetteError) as raised:
                session._navigate_fixture_tab("new-tab", "file:///start.html")
        return session, raised.exception

    def test_a_timeout_records_the_tab_state_and_is_not_retried_in_place(self):
        session, exc = self.navigate([None, core.MarionetteError(CI_TIMEOUT)], UNATTACHED)
        self.assertIsInstance(exc, core.FixtureNavigationStall)
        self.assertEqual(exc.state, UNATTACHED)
        self.assertIn("file:///start.html", str(exc))
        self.assertIn("Navigation timed out after 30000 ms", str(exc))
        self.assertIn("'process': None", str(exc))
        # One navigation only: the deadline was already spent on it.
        self.assertEqual(session.m.command.call_args_list, [
            call("WebDriver:SwitchToWindow", {"handle": "new-tab"}),
            call("WebDriver:Navigate", {"url": "file:///start.html"}),
        ])
        session.m.script.assert_called_once_with(core.FIXTURE_TAB_STATE)
        # The state is read in chrome context and the caller gets chrome back.
        self.assertEqual(session.m.set_context.call_args_list[-2:],
                         [call("chrome"), call("chrome")])

    def test_unavailable_tab_state_is_reported_and_never_retried(self):
        session, exc = self.navigate(
            [None, core.MarionetteError(CI_TIMEOUT)],
            core.MarionetteError("WebDriver:ExecuteScript failed: gone"))
        self.assertIsInstance(exc, core.FixtureNavigationStall)
        self.assertIsNone(exc.state)
        self.assertIn("tab state unavailable", str(exc))
        self.assertFalse(exc.unattached)
        self.assertFalse(core._is_startup_race(exc))
        session.m.set_context.assert_called_with("chrome")

    def test_discarded_contexts_still_refresh_the_same_tab(self):
        session, exc = self.navigate([None, core.MarionetteError(DISCARDED)] * 3)
        self.assertNotIsInstance(exc, core.FixtureNavigationStall)
        self.assertEqual(session.m.command.call_count, 6)
        session.m.script.assert_not_called()

    def test_other_navigation_errors_surface_unchanged(self):
        error = core.MarionetteError("WebDriver:Navigate failed: {'error': 'unknown error'}")
        session, exc = self.navigate([None, error])
        self.assertIs(exc, error)
        self.assertEqual(session.m.command.call_count, 2)
        session.m.script.assert_not_called()

    def test_only_a_tab_without_a_content_process_counts_as_a_startup_race(self):
        self.assertTrue(core._is_navigation_timeout(core.MarionetteError(CI_TIMEOUT)))
        self.assertFalse(core._is_navigation_timeout(core.MarionetteError(DISCARDED)))
        self.assertFalse(core._is_navigation_timeout(core.MarionetteError(
            "WebDriver:ExecuteScript failed: {'error': 'timeout'}")))
        stalled = core.FixtureNavigationStall("stalled", UNATTACHED)
        self.assertTrue(stalled.unattached)
        self.assertTrue(core._is_startup_race(stalled))
        for state in (ATTACHED, None, "unavailable", {}):
            with self.subTest(state=state):
                exc = core.FixtureNavigationStall("stalled", state)
                self.assertFalse(exc.unattached)
                self.assertFalse(core._is_startup_race(exc))
        # The bare timeout text, without tab state, is still not a race.
        self.assertFalse(core._is_startup_race(core.MarionetteError(CI_TIMEOUT)))

    def test_setup_retries_an_unattached_stall_within_the_existing_budget(self):
        stall = core.FixtureNavigationStall("stalled", UNATTACHED)
        operation = Mock(side_effect=[stall, stall, "ready"])
        sleeps = []
        result = core._retry_startup_race(operation, sleep=sleeps.append)
        self.assertEqual(result, "ready")
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(sleeps, list(core.STARTUP_RACE_DELAYS[:2]))

    def test_a_stall_with_a_content_process_spends_no_retry_budget(self):
        stall = core.FixtureNavigationStall("stalled", ATTACHED)
        sleeps = []
        with self.assertRaises(core.FixtureNavigationStall) as raised:
            core._retry_startup_race(Mock(side_effect=stall), sleep=sleeps.append)
        self.assertIs(raised.exception, stall)
        self.assertEqual(sleeps, [])

    def test_the_retry_budget_is_still_finite_for_a_persistent_stall(self):
        stall = core.FixtureNavigationStall("stalled", UNATTACHED)
        operation = Mock(side_effect=stall)
        sleeps = []
        with self.assertRaises(core.FixtureNavigationStall):
            core._retry_startup_race(operation, sleep=sleeps.append)
        self.assertEqual(operation.call_count, len(core.STARTUP_RACE_DELAYS) + 1)
        self.assertEqual(sleeps, list(core.STARTUP_RACE_DELAYS))

    def test_a_stall_is_reported_as_a_marionette_error_without_a_traceback(self):
        from fxcss import cli
        import contextlib, io
        stderr = io.StringIO()
        error = core.FixtureNavigationStall("fixture page did not load", ATTACHED)
        # Resolve a real fixture rather than discovering themes in the checkout,
        # where a package build adds a second copy of the starter template.
        with tempfile.TemporaryDirectory(prefix="fxcss-doctor-") as td:
            theme = write_themes(Path(td))[0]
            with patch.object(cli, "cmd_doctor", side_effect=error) as doctor, \
                    contextlib.redirect_stderr(stderr):
                self.assertEqual(cli.main(["doctor", "--theme", str(theme)]), 2)
            doctor.assert_called_once()
        self.assertIn("error: fixture page did not load", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class ShippedCustomChromeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="fxcss-custom-chrome-")
        self.root = Path(self.td.name)
        self.themes = {theme.name: theme for theme in write_themes(self.root / "themes")}
        self.theme = self.themes["shipped custom chrome"]

    def tearDown(self):
        self.td.cleanup()

    def test_the_fixture_ships_and_imports_its_own_custom_chrome(self):
        self.assertEqual(list(self.themes), list(EXPECTED_LAYOUTS))
        self.assertEqual((self.theme / "chrome" / "customChrome.css").read_text(encoding="utf-8"),
                         SHIPPED_CUSTOM_CHROME)
        self.assertIn('@import "customChrome.css"',
                      (self.theme / "chrome" / "userChrome.css").read_text(encoding="utf-8"))
        self.assertNotEqual(SHIPPED_CUSTOM_CHROME, core.CUSTOM_CHROME_PLACEHOLDER)

    def test_preview_profile_and_live_reload_copies_keep_the_shipped_rules(self):
        profile = self.root / "preview"
        core.build_profile(self.theme, profile)
        self.assertEqual((profile / "chrome" / "customChrome.css").read_text(encoding="utf-8"),
                         SHIPPED_CUSTOM_CHROME)
        live = self.root / "live-1"
        import shutil
        shutil.copytree(self.theme / "chrome", live)
        core._ensure_custom_chrome(self.theme / "chrome", live)
        self.assertEqual((live / "customChrome.css").read_text(encoding="utf-8"),
                         SHIPPED_CUSTOM_CHROME)
        # The courtesy placeholder still appears for a theme that only imports it.
        importing = self.themes["nested imports"]
        (importing / "chrome" / "userChrome.css").write_text(
            '@import "customChrome.css";\n', encoding="utf-8")
        core.build_profile(importing, self.root / "importing")
        self.assertEqual((self.root / "importing" / "chrome" / "customChrome.css").read_text(
            encoding="utf-8"), core.CUSTOM_CHROME_PLACEHOLDER)

    def test_install_keeps_the_shipped_rules_and_uninstall_removes_them(self):
        profile = self.root / "profile"
        profile.mkdir()
        install.install_theme(self.theme, profile, "local-fixture")
        self.assertEqual((profile / "chrome" / "customChrome.css").read_text(encoding="utf-8"),
                         SHIPPED_CUSTOM_CHROME)
        install.uninstall_theme(profile)
        self.assertFalse((profile / "chrome").exists())


class VariantFixtureTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="fxcss-variant-fixture-")
        self.root = Path(self.td.name)
        self.theme, (self.first, self.last) = smoke_variants.write_variant_theme(self.root)

    def tearDown(self):
        self.td.cleanup()

    def test_the_optional_sheet_reaches_outside_chrome(self):
        self.assertEqual(self.first.parent, self.theme / "custom")
        nested = self.theme / "custom" / "parts" / "option extra.css"
        self.assertIn('@import "parts/option extra.css"', self.first.read_text(encoding="utf-8"))
        self.assertIn('url("../assets/option.svg")', nested.read_text(encoding="utf-8"))
        self.assertNotEqual(smoke_variants.BASE_IMAGE_SIZE, smoke_variants.OPTION_IMAGE_SIZE)
        for path, size in (((self.theme / "chrome" / "assets" / "probe.svg"), smoke_variants.BASE_IMAGE_SIZE),
                           ((self.theme / "custom" / "assets" / "option.svg"), smoke_variants.OPTION_IMAGE_SIZE)):
            text = path.read_text(encoding="utf-8")
            self.assertIn(f'width="{size[0]}" height="{size[1]}"', text)

    def test_install_carries_the_nested_sheet_and_image_beside_the_copied_sheet(self):
        from urllib.parse import quote

        profile = self.root / "installed"
        profile.mkdir()
        install.install_theme(self.theme, profile, "smoke", sheets=[self.first, self.last])
        chrome = profile / "chrome"
        first_copy, = chrome.rglob(self.first.name)
        last_copy, = chrome.rglob(self.last.name)
        self.assertEqual((first_copy.parent / "parts" / "option extra.css").read_bytes(),
                         (self.theme / "custom" / "parts" / "option extra.css").read_bytes())
        self.assertEqual((first_copy.parent / "assets" / "option.svg").read_bytes(),
                         (self.theme / "custom" / "assets" / "option.svg").read_bytes())
        self.assertEqual(first_copy.read_bytes(), self.first.read_bytes())
        self.assertEqual(last_copy.read_bytes(), self.last.read_bytes())
        entry = (chrome / "userChrome.css").read_text(encoding="utf-8")
        self.assertEqual([line for line in entry.splitlines() if line.startswith("@import")], [
            '@import "fxcss-base-userChrome.css";',
            f'@import "{quote(first_copy.relative_to(chrome).as_posix())}";',
            f'@import "{quote(last_copy.relative_to(chrome).as_posix())}";',
        ])

    def test_try_layers_the_sheets_in_place_without_copying_their_files(self):
        profile = self.root / "preview"
        core.build_profile(self.theme, profile)
        fetch.apply_variants(profile, [self.first, self.last])
        chrome = profile / "chrome"
        self.assertFalse((chrome / "custom").exists())
        entry = (chrome / "userChrome.css").read_text(encoding="utf-8")
        self.assertEqual([line for line in entry.splitlines() if line.startswith("@import")], [
            '@import "fxcss-base-userChrome.css";',
            f'@import "{self.first.resolve().as_uri()}";',
            f'@import "{self.last.resolve().as_uri()}";',
        ])

    def test_image_urls_are_read_from_computed_values(self):
        self.assertEqual(smoke_variants.image_url('url("file:///a/b%20c.svg")'), "file:///a/b%20c.svg")
        with self.assertRaises(AssertionError):
            smoke_variants.image_url("none")


class WindowsProcessCheckTests(unittest.TestCase):
    def run_check(self, pids, alive):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            pid = int(args[2].split()[-1])
            if pid in alive:
                stdout = f'"firefox.exe","{pid}","Console","1","200,000 K"\r\n'
            else:
                stdout = "INFO: No tasks are running which match the specified criteria.\r\n"
            return Mock(stdout=stdout, returncode=0)

        result = smoke_startup.windows_processes_alive(pids, run=fake_run)
        return result, calls

    def test_every_recorded_pid_is_queried(self):
        result, calls = self.run_check([111, 222, 333], alive=set())
        self.assertEqual(result, [])
        self.assertEqual([args[2] for args in calls], ["PID eq 111", "PID eq 222", "PID eq 333"])
        self.assertTrue(all(args[:2] == ["tasklist", "/FI"] and args[3:] == ["/NH", "/FO", "CSV"]
                            for args in calls))

    def test_a_replaced_browser_that_survives_is_reported(self):
        result, _ = self.run_check([111, 222, 333], alive={111})
        self.assertEqual(result, [111])

    def test_a_pid_that_merely_appears_in_another_column_does_not_count(self):
        def fake_run(args, **kwargs):
            return Mock(stdout='"firefox.exe","999","Console","111","1 K"\r\n')
        self.assertEqual(smoke_startup.windows_processes_alive([111], run=fake_run), [])


if __name__ == "__main__":
    unittest.main()
