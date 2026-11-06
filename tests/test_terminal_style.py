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
