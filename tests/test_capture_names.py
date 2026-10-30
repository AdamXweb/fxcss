"""Names must not collide: theme files, variant slugs and live captures.

Three places used to let a later item silently replace an earlier one with
the same name -- the fxcss placeholder written over a theme's own
customChrome.css, two optional stylesheets folding to one slug, and two --url
captures sharing a slug. Pure filesystem and mocks; no Firefox.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fxcss import core

try:
    import PIL  # noqa: F401 - `tweaks` and `check` refuse to start without it
    HAVE_PILLOW = True
except ImportError:
    HAVE_PILLOW = False


def _theme(root, custom_chrome=None):
    theme = root / "theme"
    (theme / "chrome").mkdir(parents=True)
    (theme / "chrome" / "userChrome.css").write_text(
        '@import "customChrome.css";\n#nav-bar { }\n', encoding="utf-8")
    if custom_chrome is not None:
        (theme / "chrome" / "customChrome.css").write_text(custom_chrome, encoding="utf-8")
    return theme


def _session(theme, profile):
    """A Session with a mocked Marionette, skipping __init__ (which launches)."""
    session = core.Session.__new__(core.Session)
    session.repo, session.profile, session._generation = theme, profile, 0
    session.m = Mock()
    return session


class CustomChromeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_build_profile_keeps_a_theme_shipped_custom_chrome(self):
        theme = _theme(self.root, "#theme-provided { color: red }\n")
        profile = self.root / "profile"
        core.build_profile(theme, profile)
        self.assertEqual((profile / "chrome" / "customChrome.css").read_text(encoding="utf-8"),
                         "#theme-provided { color: red }\n")

    def test_build_profile_still_creates_the_placeholder_when_unshipped(self):
        theme = _theme(self.root)
        profile = self.root / "profile"
        core.build_profile(theme, profile)
        self.assertEqual((profile / "chrome" / "customChrome.css").read_text(encoding="utf-8"),
                         core.CUSTOM_CHROME_PLACEHOLDER)
        # Watch mode also prepares the file for explicitly loaded theme sheets.
        core.build_profile(theme, self.root / "watch", empty_user_chrome=True)
        self.assertTrue((self.root / "watch" / "chrome" / "customChrome.css").is_file())

    def test_reload_theme_keeps_a_theme_shipped_custom_chrome(self):
        theme = _theme(self.root, "#theme-provided { color: red }\n")
        profile = self.root / "profile"
        core.build_profile(theme, profile)
        session = _session(theme, profile)
        with patch.object(core.Session, "apply_harness_css"):
            uri = session.reload_theme()
        live = profile / "chrome" / "live-1"
        self.assertTrue(uri.endswith("live-1/userChrome.css"))
        self.assertEqual((live / "customChrome.css").read_text(encoding="utf-8"),
                         "#theme-provided { color: red }\n")

    def test_empty_profile_does_not_load_theme_overrides_before_watch_reload(self):
        theme = _theme(self.root, "#theme-provided { color: red }\n")
        profile = self.root / "profile"
        core.build_profile(theme, profile, empty_user_chrome=True)
        entry = (profile / "chrome/userChrome.css").read_text(encoding="utf-8")
        self.assertNotIn("@import", entry)
        self.assertNotIn("#theme-provided", entry)
        session = _session(theme, profile)
        with patch.object(core.Session, "apply_harness_css"):
            session.reload_theme()
            (theme / "chrome/customChrome.css").write_text("", encoding="utf-8")
            session.reload_theme()
        self.assertEqual((profile / "chrome/live-2/customChrome.css").read_text(encoding="utf-8"), "")
        self.assertEqual((profile / "chrome/userChrome.css").read_text(encoding="utf-8"), entry)

    def test_reload_theme_creates_the_placeholder_when_unshipped(self):
        theme = _theme(self.root)
        profile = self.root / "profile"
        core.build_profile(theme, profile)
        session = _session(theme, profile)
        with patch.object(core.Session, "apply_harness_css"):
            session.reload_theme()
        self.assertEqual((profile / "chrome" / "live-1" / "customChrome.css").read_text(encoding="utf-8"),
                         core.CUSTOM_CHROME_PLACEHOLDER)


class VariantSlugCollisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.theme = Path(self.temp.name) / "theme"
        (self.theme / "chrome").mkdir(parents=True)
        (self.theme / "chrome" / "userChrome.css").write_text("#nav-bar { }\n", encoding="utf-8")

    def sheet(self, relative):
        path = self.theme / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"/* {relative} */\n", encoding="utf-8")
        return path

    def run_cli(self, *argv):
        from fxcss import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_ordinary_names_are_unchanged(self):
        self.sheet("custom/Compact Tabs.css")
        self.sheet("optional/no-line.css")
        self.assertEqual(sorted(core.find_variant_sheets(self.theme)), ["compact-tabs", "no-line"])

    def test_same_name_in_two_folders_is_told_apart_by_folder(self):
        custom = self.sheet("custom/tabs.css")
        optional = self.sheet("optional/tabs.css")
        plain = self.sheet("custom/compact.css")
        sheets = core.find_variant_sheets(self.theme)
        self.assertEqual(sheets, {"custom-tabs": custom, "optional-tabs": optional,
                                  "compact": plain})
        # Folder-qualified names are stable: they do not depend on which other
        # sheets happen to exist, only on the two files that clash.
        self.sheet("extras/zzz.css")
        self.assertEqual(core.find_variant_sheets(self.theme)["optional-tabs"], optional)
        self.assertNotIn("tabs", core.find_variant_sheets(self.theme))

    def test_same_folder_names_folding_to_one_slug_are_rejected(self):
        self.sheet("custom/Hide_Tabs.css")
        self.sheet("custom/hide-tabs.css")
        with self.assertRaises(ValueError) as caught:
            core.find_variant_sheets(self.theme)
        message = str(caught.exception)
        self.assertIn("custom/Hide_Tabs.css", message)
        self.assertIn("custom/hide-tabs.css", message)
        self.assertIn("`hide-tabs`", message)
        self.assertIn("rename", message)

    def test_folder_qualified_name_taken_by_another_sheet_is_rejected(self):
        self.sheet("custom/tabs.css")
        self.sheet("optional/tabs.css")
        self.sheet("optional/custom-tabs.css")
        with self.assertRaisesRegex(ValueError, "`custom-tabs`"):
            core.find_variant_sheets(self.theme)

    def test_cli_commands_report_the_clash_instead_of_a_traceback(self):
        self.sheet("custom/Hide_Tabs.css")
        self.sheet("custom/hide-tabs.css")
        theme = str(self.theme)
        commands = [["shot", "--theme", theme, "--out", theme + "/shots", "--variants", "all"],
                    ["init", "--theme", theme]]
        if HAVE_PILLOW:
            commands.append(["tweaks", "--theme", theme, "--out", theme + "/tweaks"])
        for argv in commands:
            with self.subTest(command=argv[0]):
                code, _, err = self.run_cli(*argv)
                self.assertEqual(code, 2)
                self.assertIn("error: optional stylesheets ", err)
                self.assertIn("custom/Hide_Tabs.css", err)
                self.assertIn("custom/hide-tabs.css", err)
                self.assertIn("would both be called `hide-tabs`", err)
                self.assertNotIn("Traceback", err)
        self.assertFalse((self.theme / ".github").exists())

    @unittest.skipUnless(HAVE_PILLOW, "fxcss check needs Pillow")
    def test_check_settings_report_the_clash_instead_of_a_traceback(self):
        self.sheet("custom/Hide_Tabs.css")
        self.sheet("custom/hide-tabs.css")
        code, _, err = self.run_cli("check", "--theme", str(self.theme), "--out",
                                    str(self.theme / "out"))
        self.assertEqual(code, 2)
        self.assertIn("would both be called `hide-tabs`", err)

    def test_completion_offers_nothing_rather_than_raising(self):
        from fxcss import complete
        self.sheet("custom/Hide_Tabs.css")
        self.sheet("custom/hide-tabs.css")
        words = ["fxcss", "shot", "--theme", str(self.theme), "--variants", ""]
        self.assertEqual(complete.sheet_names(words, str(self.theme)), [])


class LiveCaptureNameTests(unittest.TestCase):
    def test_unique_slugs_keep_their_names(self):
        urls = ["https://github.com/AdamXweb/fxcss", "https://example.com/docs"]
        self.assertEqual(core.live_capture_names(urls),
                         [(urls[0], "github-com-adamxweb-fxcss"), (urls[1], "example-com-docs")])

    def test_urls_differing_only_in_dropped_parts_get_distinct_names(self):
        urls = ["https://example.com", "http://example.com/", "https://www.example.com/?q=1",
                "https://example.com/#frag"]
        names = [name for _, name in core.live_capture_names(urls)]
        self.assertEqual(names, ["example-com", "example-com-2", "example-com-3", "example-com-4"])
        self.assertEqual(len(set(names)), len(urls))

    def test_truncated_long_paths_get_distinct_names(self):
        long = "https://example.com/" + "a" * 60
        names = [name for _, name in core.live_capture_names([long, long + "b"])]
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(names[1].startswith(names[0]))

    def test_numbering_never_takes_a_name_another_url_holds(self):
        urls = ["https://example.com", "https://example.com/?a", "https://example.com/2"]
        self.assertEqual([name for _, name in core.live_capture_names(urls)],
                         ["example-com", "example-com-3", "example-com-2"])

    def test_names_are_deterministic_for_the_same_request(self):
        urls = ["https://a.com/x?1", "https://a.com/x?2", "https://a.com/x#3"]
        self.assertEqual(core.live_capture_names(urls), core.live_capture_names(list(urls)))

    def test_capture_live_writes_one_file_per_requested_url(self):
        shots = []

        def fake_shot(m, outdir, name, **kwargs):
            (outdir / f"{name}.png").write_bytes(b"png")
            shots.append(name)

        session = Mock()
        session.m.script.return_value = {"busy": False, "title": "Example"}
        urls = ["https://example.com", "https://www.example.com/?utm=1", "http://example.com/#top"]
        with tempfile.TemporaryDirectory() as td, \
                patch.object(core, "_shot", fake_shot), \
                patch.object(core.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            captured = core.capture_live(session, Path(td), urls, settle=0)
            written = sorted(p.name for p in (Path(td) / "live").glob("*.png"))
        self.assertEqual(len(captured), len(urls) * 2)
        self.assertEqual(len(set(captured)), len(captured))
        self.assertEqual(written, sorted(f"{name}-{mode}.png"
                                         for name in ("example-com", "example-com-2", "example-com-3")
                                         for mode in ("light", "dark")))
        self.assertEqual(shots[:2], ["example-com-light", "example-com-dark"])


if __name__ == "__main__":
    unittest.main()
