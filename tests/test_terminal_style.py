"""The terminal style layer: plurals, columns, rows and nothing past the edge."""

import os
import re
import unittest
from unittest import mock

from fxcss import cli, style

ANSI = re.compile(r"\033\[[0-9;]*m")


def visible(text):
    return ANSI.sub("", text)


class PluralTests(unittest.TestCase):
    def test_counts_read_as_words(self):
        self.assertEqual(style.plural(1, "file"), "1 file")
        self.assertEqual(style.plural(3, "file"), "3 files")
        self.assertEqual(style.plural(2, "property"), "2 properties")
        self.assertEqual(style.plural(1373, "id"), "1,373 ids")
        self.assertEqual(style.agree(1, "need", "needs"), "needs")


class LayoutTests(unittest.TestCase):
    def test_columns_keep_one_gutter(self):
        names = ["short", "theme-catppuccin-macchiato", "a", "b", "c", "d"]
        lines = style.columns(names, indent=4, gap=3, width=60, numbered=True)
        for line in lines:
            self.assertLessEqual(len(line), 60)
        # The gap after the longest entry is the full gutter, never less.
        longest = [l for l in lines if "macchiato" in l][0]
        after = longest.split("macchiato", 1)[1]
        if after.strip():
            self.assertTrue(after.startswith("   "))

    def test_rows_wrap_between_words_under_the_value(self):
        style.set_colour(False)
        try:
            lines = style.rows([("label", "word " * 40)], indent=2, width=60)
        finally:
            style.set_colour(None)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(len(line), 60)
            self.assertFalse(line.endswith("wor"))
        self.assertTrue(lines[1].startswith(" " * 9))

    def test_styled_overflow_moves_the_note_down(self):
        style.set_colour(True)
        try:
            value = "x" * 50 + "  " + style.muted("y" * 30)
            lines = style.rows([("k", value)], indent=2, width=60)
        finally:
            style.set_colour(None)
        self.assertEqual(len(lines), 2)
        self.assertTrue(visible(lines[1]).strip().startswith("y"))


class EntryScreenTests(unittest.TestCase):
    def render(self, func, columns):
        with mock.patch.dict(os.environ, {"COLUMNS": str(columns), "NO_COLOR": "1"}), \
                mock.patch("fxcss.style.os.get_terminal_size", side_effect=OSError):
            return func()

    def test_nothing_runs_past_the_terminal_edge(self):
        for columns in (80, 100):
            for func in (cli.landing_text, cli.help_text):
                with self.subTest(screen=func.__name__, columns=columns):
                    for line in self.render(func, columns).splitlines():
                        self.assertLess(len(visible(line)), columns, line)

    def test_every_command_is_listed_once(self):
        text = self.render(cli.help_text, 100)
        for name in cli.COMMANDS:
            self.assertEqual(len(re.findall(rf"^    {name} ", text, re.M)), 1, name)

    def test_help_and_welcome_share_summaries(self):
        welcome = self.render(cli.landing_text, 100)
        for _, question, _, subset in cli.GROUPS:
            for name, _ in subset:
                self.assertIn(cli.COMMANDS[name], " ".join(welcome.split()))



class ColourScopeTests(unittest.TestCase):
    def test_no_colour_does_not_outlive_the_audit(self):
        import argparse
        from pathlib import Path
        args = argparse.Namespace(no_colour=True, theme=Path("."), firefox=None)
        seen = []
        with mock.patch("fxcss.cli._audit_report",
                        side_effect=lambda a: seen.append(style._override) or 0):
            self.assertEqual(cli._run_audit(args), 0)
        self.assertEqual(seen, [False])
        self.assertIsNone(style._override)

if __name__ == "__main__":
    unittest.main()


class ClipTests(unittest.TestCase):
    def test_prose_keeps_its_start_and_breaks_between_words(self):
        self.assertEqual(style.clip("newer than the release — fix tabs", 24), "newer than the release…")
        self.assertEqual(style.clip("short", 24), "short")

    def test_a_path_keeps_its_tail_and_drops_whole_folders(self):
        path = "~/Library/Application Support/Firefox/Profiles/k3x9.default-release"
        self.assertEqual(style.clip(path, 40, path=True), "…/Firefox/Profiles/k3x9.default-release")
        self.assertLessEqual(len(style.clip(path, 12, path=True)), 12)


class MenuTests(unittest.TestCase):
    """Every numbered question reads the same: heading, rows, `Which one?`."""

    BUILDS = [{"label": "stable", "path": "/Applications/Firefox.app/Contents/MacOS/firefox"},
              {"label": "nightly",
               "path": "/Applications/Firefox Nightly.app/Contents/MacOS/firefox"}]

    def ask(self, func, answer, *args):
        prompts, out = [], []
        style.set_colour(False)
        try:
            with mock.patch("builtins.input", lambda p: prompts.append(p) or answer), \
                    mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))), \
                    mock.patch.dict(os.environ, {"COLUMNS": "80"}):
                result = func(*args)
        finally:
            style.set_colour(None)
        return result, "\n".join(out).splitlines(), prompts

    def test_firefox_menu_matches_the_profile_menu(self):
        with mock.patch.object(cli.core, "discover_firefoxes", return_value=self.BUILDS), \
                mock.patch.object(cli.core, "firefox_version",
                                  side_effect=lambda p: "157.0a1" if "Nightly" in p else "155.0.1"), \
                mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(cli.sys.stdout, "isatty", return_value=True), \
                mock.patch.dict(os.environ, {"CI": "", "FIREFOX_BIN": ""}):
            path, lines, prompts = self.ask(cli.choose_firefox, "2", None)
        self.assertEqual(path, self.BUILDS[1]["path"])
        self.assertEqual(prompts, ["  Which one? 1-2 [1] "])
        # Both builds live in /Applications, so the heading names it once.
        self.assertIn("  Which Gecko build?  2 installed in /Applications", lines)
        rows = [l for l in lines if re.match(r"\s+\d\. ", l)]
        self.assertEqual(rows[0], "   1. stable   155.0.1  Firefox.app  (Enter)")
        self.assertEqual(rows[1].index("157.0a1"), rows[0].index("155.0.1"))
        self.assertIn("  using nightly  --firefox nightly skips this question", lines)

    def test_release_menu_cuts_a_long_note_instead_of_wrapping(self):
        info = {"release": {"tag": "v2.1", "date": "2025-03-02T10:00:00Z"},
                "default_branch": "main",
                "commit": {"date": "2026-10-30T10:00:00Z", "message": "fix tabs " * 20}}
        (ref, _why), lines, prompts = self.ask(cli._choose_ref, "", info, None, True)
        self.assertEqual(ref, "v2.1")
        self.assertIn("  using release v2.1  --ref v2.1 skips this question", lines)
        self.assertEqual(prompts, ["  Which one? 1-2 [1] "])
        rows = [l for l in lines if re.match(r"\s+\d\. ", l)]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertLessEqual(len(row), 79)
        self.assertTrue(rows[1].endswith("…"))
        self.assertTrue(rows[0].endswith("(Enter)"))
