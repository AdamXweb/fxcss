"""Installing must leave user.js and optional sheets as their authors wrote them."""

import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from fxcss import install
from tests.test_units import _make_profile, _make_theme

# Not UTF-8, CRLF and CR line endings, a BOM, no final newline: user.js is
# the person's file and any of these may be in it.
ODD_PREFS = [
    b'user_pref("latin1", "caf\xe9 \xff\xfe");\r\nuser_pref("b", 1);',
    b"\xef\xbb\xbfuser_pref(\"bom\", true);\n\n\n",
    b"// old mac\ruser_pref(\"cr\", 1);\r",
    b"   ",
    b"\r",
    b"\n\n",
    b"",
    b'user_pref("plain", 1);\n',
]


class UserJsBytesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.theme = _make_theme(self.root)

    def fresh_profile(self, raw):
        profile = _make_profile(self.root / f"p{len(list(self.root.iterdir()))}")
        (profile / "user.js").write_bytes(raw)
        return profile

    def test_install_keeps_every_byte_outside_the_block(self):
        for raw in ODD_PREFS:
            with self.subTest(raw=raw):
                profile = self.fresh_profile(raw)
                install.install_theme(self.theme, profile, "o/n@v1")
                written = (profile / "user.js").read_bytes()
                self.assertTrue(written.startswith(raw))
                self.assertEqual(written.count(install.BLOCK_BEGIN.encode()), 1)

    def test_uninstall_restores_the_exact_bytes(self):
        for raw in ODD_PREFS:
            with self.subTest(raw=raw):
                profile = self.fresh_profile(raw)
                install.install_theme(self.theme, profile, "o/n@v1")
                install.install_theme(self.theme, profile, "o/n@v2")
                install.uninstall_theme(profile)
                self.assertEqual((profile / "user.js").read_bytes(), raw)

    def test_rollback_paths_restore_the_exact_bytes(self):
        for raw in ODD_PREFS:
            with self.subTest(raw=raw):
                profile = self.fresh_profile(raw)
                first = install.install_theme(self.theme, profile, "o/n@v1",
                                              stamp="20260101000000")
                second = install.install_theme(
                    self.theme, profile, "o/n@v2", stamp="20260102000000",
                    origin_backup=first["backup"])
                # To the previous fxcss install: its block is written back.
                install.rollback_to(profile, second["backup"],
                                    stamp="20260103000000")
                written = (profile / "user.js").read_bytes()
                self.assertTrue(written.startswith(raw))
                # To the user's own chrome/: the block comes out again.
                install.rollback_to(profile, first["backup"],
                                    stamp="20260104000000")
                self.assertEqual((profile / "user.js").read_bytes(), raw)

    def test_user_text_after_the_block_survives_removal(self):
        profile = self.fresh_profile(b'user_pref("a", 1);')
        install.install_theme(self.theme, profile, "o/n@v1")
        with open(profile / "user.js", "ab") as handle:
            handle.write(b'user_pref("added \xe9", 2);\r\n')
        install.uninstall_theme(profile)
        # The terminator fxcss added stays: joining the lines could comment
        # out the added pref.
        self.assertEqual((profile / "user.js").read_bytes(),
                         b'user_pref("a", 1);\nuser_pref("added \xe9", 2);\r\n')

    def test_legacy_windows_blocks_survive_upgrade_rollback_and_uninstall(self):
        original = b'user_pref("personal.setting", true);\r\n\r\n'
        for action in ("uninstall", "upgrade", "rollback"):
            with self.subTest(action=action):
                profile = self.fresh_profile(original)
                first = install.install_theme(self.theme, profile, "o/n@v1",
                                              stamp="20260101000000")
                prefs = profile / "user.js"
                # Older releases used write_text: Windows translated the
                # separator and the entire managed block to CRLF.
                prefs.write_bytes(prefs.read_text(encoding="utf-8").replace(
                    "\n", "\r\n").encode("utf-8"))
                if action == "upgrade":
                    install.install_theme(self.theme, profile, "o/n@v2",
                                          stamp="20260102000000")
                if action == "rollback":
                    install.rollback_to(profile, first["backup"],
                                        stamp="20260103000000")
                else:
                    install.uninstall_theme(profile)
                self.assertEqual(prefs.read_bytes(), original)

    def test_legacy_block_removal_keeps_later_user_preferences(self):
        before = 'user_pref("before", true);\r\n'
        after = 'user_pref("after", true);\r\n'
        legacy = (before + "\r\n" + install.BLOCK_BEGIN + "\r\n" +
                  install.STYLESHEET_PREF + "\r\n" + install.BLOCK_END + "\r\n" + after)
        self.assertEqual(install.strip_user_js_block(legacy), before + after)


class ThemeMarkerTests(unittest.TestCase):
    """A theme's configuration/user.js is untrusted and may hold our markers."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.theme, self.profile = _make_theme(root), _make_profile(root)
        self.original = (self.profile / "user.js").read_bytes()
        (self.theme / "configuration" / "user.js").write_text(
            'user_pref("theme.before", 1);\n'
            f"  {install.BLOCK_END}\n"
            'user_pref("theme.middle", 2);\n'
            f"{install.BLOCK_BEGIN}\r\n"
            'user_pref("theme.after", 3);\n', encoding="utf-8")

    def test_reinstalls_neither_stack_prefs_nor_lose_them(self):
        for version in ("v1", "v2", "v3"):
            install.install_theme(self.theme, self.profile, f"o/n@{version}")
        text = (self.profile / "user.js").read_text(encoding="utf-8")
        for pref in ("theme.before", "theme.middle", "theme.after"):
            self.assertEqual(text.count(pref), 1, pref)
        lines = [line.strip() for line in text.splitlines()]
        self.assertEqual(lines.count(install.BLOCK_BEGIN), 1)
        self.assertEqual(lines.count(install.BLOCK_END), 1)
        install.uninstall_theme(self.profile)
        self.assertEqual((self.profile / "user.js").read_bytes(), self.original)

    def test_manifest_records_the_sanitised_block(self):
        manifest = install.install_theme(self.theme, self.profile, "o/n@v1")
        recorded = [line.strip() for line in manifest["user_js_block"].splitlines()]
        self.assertNotIn(install.BLOCK_BEGIN, recorded)
        self.assertNotIn(install.BLOCK_END, recorded)
        self.assertIn('user_pref("theme.after", 3);', recorded)

    def test_a_block_recorded_by_an_older_fxcss_is_sanitised_on_rollback(self):
        import json
        first = install.install_theme(self.theme, self.profile, "o/n@v1",
                                      stamp="20260101000000")
        second = install.install_theme(self.theme, self.profile, "o/n@v2",
                                       stamp="20260102000000",
                                       origin_backup=first["backup"])
        old = self.profile / second["backup"] / install.MANIFEST_NAME
        data = json.loads(old.read_text(encoding="utf-8"))
        data["user_js_block"] = (f'user_pref("x", 1);\n{install.BLOCK_END}\n'
                                 'user_pref("y", 2);')
        old.write_text(json.dumps(data), encoding="utf-8")
        install.rollback_to(self.profile, second["backup"], stamp="20260103000000")
        install.uninstall_theme(self.profile)
        self.assertEqual((self.profile / "user.js").read_bytes(), self.original)

    def test_pref_names_are_listed_for_confirmation(self):
        (self.theme / "configuration" / "user.js").write_text(
            '// user_pref("commented.out", true);\n'
            '/* user_pref("also.commented", 1); */\n'
            'user_pref("a.real", "has // and /* inside");\n'
            "user_pref('single.quoted', 2);\n"
            'user_pref("a.real", 3);\n'
            'user_pref("\x1b[2Jevil", 1);\n', encoding="utf-8")
        self.assertEqual(install.theme_pref_names(self.theme),
                         ["a.real", "single.quoted", "\\x1b[2Jevil"])

    def test_cli_shows_the_prefs_before_asking(self):
        from fxcss import cli
        args = cli.build_parser().parse_args(
            ["install", str(self.theme), "--profile", str(self.profile),
             "--with", "compact-tabs"])
        output, seen = StringIO(), []

        def answer(prompt):
            seen.append(output.getvalue() + prompt)
            return "n"

        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(output))
            stack.enter_context(redirect_stderr(StringIO()))
            stack.enter_context(patch.object(output, "isatty", return_value=True))
            stack.enter_context(patch("fxcss.cli.sys.stdin.isatty", return_value=True))
            stack.enter_context(patch.dict("os.environ", {"CI": "", "NO_COLOR": "1"}))
            stack.enter_context(patch("builtins.input", side_effect=answer))
            stack.enter_context(patch("fxcss.install.discover_profiles", return_value=[]))
            self.assertEqual(cli.cmd_install(args), 1)
        self.assertIn("Install into this profile?", seen[-1])
        self.assertIn("theme.before, theme.middle, theme.after", seen[-1])
        self.assertIsNone(install.read_manifest(self.profile))


class ImportSiteTests(unittest.TestCase):
    def test_commented_imports_are_not_import_sites(self):
        with tempfile.TemporaryDirectory() as td:
            theme = _make_theme(Path(td))
            (theme / "chrome" / "WhiteSur" / "theme.css").write_text(
                '/* @import "old/compact-tabs.css"; */\n'
                '#a::after { content: "@import \'str/compact-tabs.css\'"; }\n'
                '#b::after { content: "/*"; }\n'
                '@import "custom/compact-tabs.css";\n'
                '#c::after { content: "*/"; }\n', encoding="utf-8")
            dest = install.variant_destination(theme / "chrome", "compact-tabs.css")
            self.assertEqual(dest, theme / "chrome" / "WhiteSur" / "custom" /
                             "compact-tabs.css")

    def test_only_commented_import_means_no_site(self):
        with tempfile.TemporaryDirectory() as td:
            theme = _make_theme(Path(td))
            (theme / "chrome" / "WhiteSur" / "theme.css").write_text(
                '/*\n@import "custom/compact-tabs.css";\n*/\n', encoding="utf-8")
            self.assertIsNone(install.variant_destination(
                theme / "chrome", "compact-tabs.css"))

    def test_references_respect_comments_and_strings(self):
        css = ('@import url(a.css);\n@import url( "b.css" ) screen;\n'
               "/* url(no.png) */ #x { background: url(c.png); }\n"
               '#y::after { content: "url(no.svg) /*"; } '
               "#z { mask: url('d.svg#frag'); } /* unterminated")
        self.assertEqual(list(install.css_references(css)),
                         [("import", "a.css"), ("import", "b.css"),
                          ("url", "c.png"), ("url", "d.svg#frag")])


class SheetDependencyTests(unittest.TestCase):
    """An installed optional sheet must see the files the preview saw."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.theme, self.profile = _make_theme(self.root), _make_profile(self.root)
        custom = self.theme / "custom"
        (custom / "icons").mkdir()
        (custom / "parts").mkdir()
        (self.theme / "shared").mkdir()
        (custom / "icons" / "tab icon.svg").write_text("<svg id='tab'/>")
        (custom / "icons" / "close.svg").write_text("<svg id='close'/>")
        (self.theme / "shared" / "font.woff2").write_bytes(b"\x00font")
        (custom / "parts" / "nested.css").write_text(
            '@import "../../shared/colours.css";\n'
            "#n { background: url(../icons/close.svg?v=2); }\n")
        (self.theme / "shared" / "colours.css").write_text(":root { --c: red; }\n")
        (custom / "loose-sheet.css").write_text(
            '@import "parts/nested.css";\n'
            "/* @import 'parts/disabled.css'; */\n"
            '#l { background: url("icons/tab%20icon.svg#x"); }\n'
            "@font-face { src: url(../shared/font.woff2); }\n"
            "#m { background: url(chrome://global/skin/x.svg); }\n"
            "#o { background: url(data:image/png;base64,AAAA); }\n"
            "#p { background: url(icons/missing.svg); }\n")
        (custom / "parts" / "disabled.css").write_text("#never {}\n")
        self.sheet = custom / "loose-sheet.css"

    def installed(self):
        chrome = self.profile / "chrome"
        return sorted(p.relative_to(chrome).as_posix()
                      for p in chrome.rglob("*") if p.is_file())

    def assert_same_resolution(self, source, placed, chrome):
        """Every local reference resolves to the same bytes from both places."""
        for _kind, target in install.css_references(source.read_text()):
            relative = install._local_reference(target)
            if relative is None:
                continue
            origin = Path(os.path.normpath(source.parent / relative))
            if not origin.is_file():
                continue
            there = Path(os.path.normpath(placed.parent / relative))
            self.assertTrue(there.is_file(), there)
            self.assertEqual(there.read_bytes(), origin.read_bytes())
            if origin.suffix == ".css":
                self.assert_same_resolution(origin, there, chrome)

    def test_wrapper_install_carries_local_dependencies(self):
        manifest = install.install_theme(self.theme, self.profile, "o/n@v1",
                                         sheets=[self.sheet])
        chrome = self.profile / "chrome"
        for expected in ("custom/loose-sheet.css", "custom/icons/tab icon.svg",
                         "custom/icons/close.svg", "custom/parts/nested.css",
                         "shared/colours.css", "shared/font.woff2"):
            self.assertIn(expected, self.installed())
            self.assertIn(f"chrome/{expected}", manifest["files"])
        self.assertNotIn("custom/parts/disabled.css", self.installed())
        self.assertEqual(manifest["sheet_notes"], [])
        self.assert_same_resolution(self.sheet, chrome / "custom" / "loose-sheet.css",
                                    chrome)
        install.uninstall_theme(self.profile)
        self.assertEqual((chrome / "userChrome.css").read_text(),
                         "/* the user's own */\n")
        self.assertFalse((chrome / "shared").exists())

    def test_import_site_install_carries_local_dependencies(self):
        sheet = self.theme / "custom" / "compact-tabs.css"
        sheet.write_text('#t { background: url(icons/close.svg); }\n')
        install.install_theme(self.theme, self.profile, "o/n@v1", sheets=[sheet])
        chrome = self.profile / "chrome"
        placed = chrome / "WhiteSur" / "custom" / "compact-tabs.css"
        self.assertTrue(placed.is_file())
        self.assert_same_resolution(sheet, placed, chrome)

    def test_the_themes_own_files_are_never_overwritten(self):
        (self.theme / "chrome" / "shared").mkdir()
        (self.theme / "chrome" / "shared" / "colours.css").write_text("/* own */")
        manifest = install.install_theme(self.theme, self.profile, "o/n@v1",
                                         sheets=[self.sheet])
        self.assertEqual(
            (self.profile / "chrome" / "shared" / "colours.css").read_text(),
            "/* own */")
        self.assertEqual(len(manifest["sheet_notes"]), 1)
        self.assertIn("chrome/shared/colours.css kept", manifest["sheet_notes"][0])

    def test_references_outside_the_theme_are_reported_not_copied(self):
        secret = self.root / "secret.txt"
        secret.write_text("private")
        (self.theme / "custom" / "link.png").symlink_to(secret)
        self.sheet.write_text("#a { background: url(../../secret.txt); }\n"
                              "#b { background: url(link.png); }\n"
                              "#c { background: url(../../../../../../../x); }\n")
        manifest = install.install_theme(self.theme, self.profile, "o/n@v1",
                                         sheets=[self.sheet])
        chrome = self.profile / "chrome"
        for path in chrome.rglob("*"):
            if path.is_file():
                self.assertNotEqual(path.read_bytes(), b"private", path)
        notes = "\n".join(manifest["sheet_notes"])
        self.assertIn("../../secret.txt (outside the theme", notes)
        self.assertIn("link.png (outside the theme", notes)

    def test_too_many_dependencies_fail_before_the_profile_changes(self):
        before = {p: p.read_bytes() for p in self.profile.rglob("*") if p.is_file()}
        with patch.object(install, "MAX_SHEET_DEPENDENCIES", 2), \
                self.assertRaisesRegex(RuntimeError, "more local files"):
            install.install_theme(self.theme, self.profile, "o/n@v1",
                                  sheets=[self.sheet])
        after = {p: p.read_bytes() for p in self.profile.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertFalse(list(self.profile.glob(".fxcss-*")))

    def test_import_cycles_terminate(self):
        (self.theme / "custom" / "parts" / "nested.css").write_text(
            '@import "../loose-sheet.css";\n')
        install.install_theme(self.theme, self.profile, "o/n@v1",
                              sheets=[self.sheet])
        self.assertIn("custom/parts/nested.css", self.installed())

    def test_existing_identical_css_still_brings_its_dependencies(self):
        parts = self.theme / "chrome" / "custom" / "parts"
        parts.mkdir(parents=True)
        (parts / "nested.css").write_bytes(
            (self.theme / "custom" / "parts" / "nested.css").read_bytes())
        install.install_theme(self.theme, self.profile, "o/n@v1", sheets=[self.sheet])
        self.assert_same_resolution(self.sheet, self.profile / "chrome/custom/loose-sheet.css",
                                    self.profile / "chrome")

    def test_extensionless_imports_bring_their_dependencies(self):
        self.sheet.write_text('@import "parts/rules";')
        (self.sheet.parent / "parts/rules").write_text(
            '#a { background: url(../icons/close.svg); }')
        install.install_theme(self.theme, self.profile, "o/n@v1", sheets=[self.sheet])
        self.assertIn("custom/parts/rules", self.installed())
        self.assertIn("custom/icons/close.svg", self.installed())


if __name__ == "__main__":
    unittest.main()
