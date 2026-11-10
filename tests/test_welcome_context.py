"""The line under the welcome tagline, and the OS symbol it shares with doctor."""

import argparse
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fxcss import cli, core, install, style, support


def plain(parts):
    return " · ".join(style._visible(part) for part in parts)


class PlatformLabelTests(unittest.TestCase):
    @unittest.skipUnless(style.OS_MARKS, "this console cannot show the symbols")
    def test_each_system_uses_its_special_key_symbol(self):
        self.assertEqual(style.OS_MARKS, {"macOS": "⌘", "Windows": "⊞", "Linux": "◆"})

    def test_each_os_has_its_symbol_under_every_name_it_goes_by(self):
        with mock.patch.dict(style.OS_MARKS, {"macOS": "⌘", "Windows": "⊞", "Linux": "◆"}):
            for names, label in ((("Darwin", "darwin"), "⌘ macOS"),
                                 (("WINNT", "Windows_NT", "win32"), "⊞ Windows"),
                                 (("Linux", "linux"), "◆ Linux")):
                for name in names:
                    with self.subTest(name=name):
                        self.assertEqual(style.platform_label(name, on=False), label)

    def test_only_the_symbol_takes_the_brand_colour(self):
        with mock.patch.dict(style.OS_MARKS, {"macOS": "⌘"}):
            self.assertEqual(style.platform_label("Darwin", on=True),
                             f"{style.brand('⌘', on=True)} macOS")

    def test_an_os_without_a_symbol_is_just_its_name(self):
        self.assertEqual(style.platform_label("FreeBSD", on=True), "FreeBSD")

    def test_a_terminal_without_the_glyphs_gets_the_name_alone(self):
        with mock.patch.dict(style.OS_MARKS, clear=True):
            self.assertEqual(style.platform_label("Darwin", on=True), "macOS")


class DottedTests(unittest.TestCase):
    def test_items_share_a_line_when_they_fit(self):
        self.assertEqual(style.dotted(["a", "b"], indent=2, width=60, on=False),
                         [f"  a {style.DOT} b"])

    def test_breaks_between_items_at_the_edge(self):
        lines = style.dotted(["a" * 30, "b" * 30, "c" * 30], indent=9, width=60, on=False)
        self.assertEqual(lines, [" " * 9 + "a" * 30, " " * 9 + "b" * 30, " " * 9 + "c" * 30])

    def test_colour_codes_take_no_room(self):
        # Fits in 60 columns on screen; counting the codes would break the line.
        items = [style.brand("a" * 30, on=True), style.brand("b" * 20, on=True)]
        self.assertEqual(len(style.dotted(items, indent=2, width=60, on=True)), 1)


class ContextTests(unittest.TestCase):
    """What the line says, for each state of the machine."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / "firefox"
        self.binary.write_text("")
        self.write_ini("Firefox", "155.0.1")
        self.profile = self.root / "abc.default-release"
        self.profile.mkdir()
        self.profiles = [{"name": "default-release", "path": self.profile, "default": True}]
        for target, value in (("fxcss.cli.core.find_firefox", lambda _: str(self.binary)),
                              ("fxcss.install.discover_profiles", lambda: self.profiles)):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("fxcss.cli.sys.platform", "darwin")
        patcher.start()
        self.addCleanup(patcher.stop)
        style.set_colour(False)
        self.addCleanup(style.set_colour, None)

    def write_ini(self, name, version):
        (self.root / "application.ini").write_text(
            f"; Generated\n[App]\nName={name}\nVersion={version}\n", encoding="utf-8")

    def manifest(self, **fields):
        with mock.patch.object(install, "read_manifest", return_value=fields):
            return plain(cli._machine_context())

    def test_a_theme_from_github_goes_by_its_repository_name(self):
        text = self.manifest(theme="AdamXweb/WhiteSur@v1", source={"name": "WhiteSur"})
        self.assertTrue(text.endswith(
            "macOS · Firefox 155.0.1 · default-release · WhiteSur installed"), text)

    def test_a_local_theme_goes_by_its_folder(self):
        self.assertIn(" · mine installed", self.manifest(source={"path": "/themes/mine"}))

    def test_a_theme_with_only_a_raw_id_shows_it(self):
        self.assertIn(" · whitesur installed", self.manifest(theme="whitesur", source={}))

    def test_a_long_theme_name_is_cut(self):
        text = self.manifest(source={"name": "x" * 80})
        self.assertNotIn("x" * 33, text)
        self.assertIn(f"{style.ELLIPSIS} installed", text)

    def test_an_empty_profile_says_no_theme_yet(self):
        self.assertIn(" · default-release · no theme yet", plain(cli._machine_context()))

    def test_a_hand_installed_theme_is_not_called_no_theme(self):
        chrome = self.profile / "chrome"
        chrome.mkdir()
        (chrome / "userChrome.css").write_text("")
        text = plain(cli._machine_context())
        self.assertIn(" · has its own chrome/", text)
        self.assertNotIn("no theme", text)

    def test_a_fork_goes_by_its_own_name(self):
        self.write_ini("LibreWolf", "140.0")
        self.assertIn(" · LibreWolf 140.0 · ", plain(cli._machine_context()))

    def test_a_build_with_no_readable_version_still_says_firefox(self):
        (self.root / "application.ini").unlink()
        self.assertEqual(plain(cli._machine_context()).split(" · ")[1], "Firefox")

    def test_no_firefox_names_the_setting_that_finds_it(self):
        with mock.patch("fxcss.cli.core.find_firefox", side_effect=SystemExit("none")):
            self.assertTrue(plain(cli._machine_context()).endswith(
                "macOS · Firefox not found · set FIREFOX_BIN to its path"))

    def test_a_firefox_path_that_does_not_exist_is_not_found(self):
        self.binary.unlink()
        self.assertIn("Firefox not found", plain(cli._machine_context()))

    def test_no_clear_default_names_no_profile(self):
        self.profiles[:] = [dict(self.profiles[0], default=True),
                            dict(self.profiles[0], name="other", default=True)]
        self.assertTrue(plain(cli._machine_context()).endswith("Firefox 155.0.1"))

    def test_no_profiles_stops_at_firefox(self):
        self.profiles[:] = []
        self.assertTrue(plain(cli._machine_context()).endswith("Firefox 155.0.1"))

    def test_a_single_profile_needs_no_default_flag(self):
        self.profiles[0]["default"] = False
        self.assertIn(" · default-release · ", plain(cli._machine_context()))

    def test_firefox_is_never_started(self):
        (self.root / "application.ini").unlink()
        with mock.patch("fxcss.core.subprocess.run", side_effect=AssertionError("ran")) as run, \
                mock.patch("fxcss.core.subprocess.Popen", side_effect=AssertionError("ran")):
            cli._machine_context()
        run.assert_not_called()

    def test_the_line_sits_under_the_tagline_lined_up_with_it(self):
        lines = cli.landing_text().splitlines()
        self.assertEqual(lines[0].index("live-reload"), 9)
        self.assertEqual(len(lines[1]) - len(lines[1].lstrip()), 9)
        self.assertIn("macOS", lines[1])

    def test_anything_unreadable_drops_the_line_not_the_welcome(self):
        with mock.patch("fxcss.install.discover_profiles", side_effect=PermissionError("no")):
            lines = cli.landing_text().splitlines()
        self.assertIn(cli.TAGLINE, lines[0])
        self.assertEqual(lines[1], "")
        self.assertIn("Try or install a theme", "\n".join(lines))

    def test_bare_fxcss_prints_it(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main([]), 0)
        self.assertIn("Firefox 155.0.1", out.getvalue())


class DoctorRowTests(unittest.TestCase):
    def test_the_firefox_row_carries_the_symbol(self):
        info = {"version": "155.0.1", "os": "Darwin", "buildID": "20260903",
                "outer": [1200, 800], "dpr": 2, "legacyStylesheets": True,
                "nativeContextMenus": {}}
        session = mock.MagicMock()
        session.__enter__.return_value.info.return_value = info
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("fxcss.cli.choose_firefox", return_value="/x/firefox"), \
                mock.patch("fxcss.cli.core.Session", return_value=session), \
                mock.patch("fxcss.cli.core.discover_firefoxes", return_value=[]), \
                mock.patch.dict(style.OS_MARKS, {"macOS": "⌘"}), \
                contextlib.redirect_stdout(out):
            cli.cmd_doctor(argparse.Namespace(theme=Path(tmp), firefox=None))
        self.assertIn("155.0.1 on ⌘ macOS", style._visible(out.getvalue()))


class DoctorWithoutAThemeTests(unittest.TestCase):
    """`fxcss doctor` is the first thing the installer says to run.

    Run before there is a theme, it stopped with "no userChrome.css or
    userContent.css anywhere" and said nothing about Firefox at all.
    """

    INFO = {"version": "158.0", "os": "Darwin", "buildID": "20261007",
            "outer": [1280, 480], "dpr": 1, "legacyStylesheets": True,
            "nativeContextMenus": {}}

    def doctor(self, files=()):
        session = mock.MagicMock()
        session.__enter__.return_value.info.return_value = self.INFO
        seen = {}

        def opened(root, firefox, **kwargs):
            seen["root"] = Path(root)
            seen["sheet"] = (Path(root) / "chrome" / "userChrome.css").is_file()
            return session

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("fxcss.cli.choose_firefox", return_value="/x/firefox"), \
                mock.patch("fxcss.cli.core.Session", side_effect=opened), \
                mock.patch("fxcss.cli.core.discover_firefoxes", return_value=[]), \
                mock.patch.dict("os.environ", {"NO_COLOR": "1"}), \
                contextlib.redirect_stdout(out):
            for name in files:
                target = Path(tmp) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("#nav-bar { }\n")
            code = cli.main(["doctor", "--theme", tmp])
            seen["own"] = seen["root"].resolve() == Path(tmp).resolve()
        return code, out.getvalue(), seen

    def test_an_empty_folder_still_gets_the_firefox_checks(self):
        code, text, seen = self.doctor()
        self.assertEqual(code, 0)
        self.assertIn("theme    none in this folder", text)
        self.assertIn("userChrome.css loading is enabled", text)
        self.assertIn("No theme in this folder", text)
        self.assertIn("Ready to theme", text)
        self.assertIn("New here?", text)
        self.assertNotIn("to fix", text)
        # The session ran on a blank stand-in, not on the folder.
        self.assertTrue(seen["sheet"])
        self.assertFalse(seen["own"])

    def test_loose_stylesheets_that_are_not_a_theme_are_reported_not_fatal(self):
        code, text, _ = self.doctor(["notes/tabs.css"])
        self.assertEqual(code, 0)
        self.assertIn("No theme in this folder", text)

    def test_a_theme_is_checked_as_before(self):
        code, text, seen = self.doctor(["chrome/userChrome.css"])
        self.assertEqual(code, 0)
        self.assertIn("theme    1 stylesheet, under 1 KB", text)
        self.assertIn("chrome/userChrome.css is in place", text)
        self.assertNotIn("New here?", text)
        self.assertTrue(seen["own"])


class BuildIdentityTests(unittest.TestCase):
    def build(self, *folders, body="[App]\nName=Firefox\nVersion=155.0.1\n"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ini = root.joinpath(*folders, "application.ini")
        ini.parent.mkdir(parents=True, exist_ok=True)
        ini.write_text(body, encoding="utf-8")
        binary = root / "app" / "MacOS" / "firefox"
        binary.parent.mkdir(parents=True, exist_ok=True)
        return binary

    def test_read_beside_the_binary(self):
        self.assertEqual(core.build_identity(self.build("app", "MacOS")), ("Firefox", "155.0.1"))

    def test_read_from_a_mac_bundles_resources(self):
        self.assertEqual(core.build_identity(self.build("app", "Resources")),
                         ("Firefox", "155.0.1"))

    def test_a_missing_file_gives_a_generic_name_and_no_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(core.build_identity(Path(tmp) / "firefox"), ("Firefox", None))

    def test_a_percent_sign_is_not_an_error(self):
        binary = self.build("app", "MacOS", body="[App]\nName=Firefox\nVersion=1\nX=100%\n")
        self.assertEqual(core.build_identity(binary), ("Firefox", "1"))

    def test_build_version_still_asks_the_binary_when_there_is_no_file(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(core, "firefox_version", return_value="9.9") as asked:
            self.assertEqual(support.build_version(Path(tmp) / "firefox"), "9.9")
        asked.assert_called_once()

    def test_build_version_still_asks_the_binary_when_the_file_is_broken(self):
        binary = self.build("app", "MacOS", body="not an ini file\n")
        with mock.patch.object(core, "firefox_version", return_value="9.9"):
            self.assertEqual(support.build_version(binary), "9.9")

    def test_build_version_does_not_run_a_build_with_a_file(self):
        binary = self.build("app", "MacOS")
        with mock.patch.object(core, "firefox_version", side_effect=AssertionError("ran")):
            self.assertEqual(support.build_version(binary), "155.0.1")


if __name__ == "__main__":
    unittest.main()
