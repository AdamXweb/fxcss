"""The README install flags in `try --info`: two columns when that saves rows."""

import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fxcss import cli, style

FLAGS = [
    ("-c", "Left hand side tab close button"),
    ("-w", "Left hand side window close button"),
    ("-p", "Makes tabs height compact like current Safari"),
    ("-f", "To specify the default firefox folder (it will try to find the "
           "profile folder to place the theme within)"),
    ("-l", "Default location of most Linux installations"),
    ("-u", "Remove the animation on URL bar to be clickable throughout"),
    ("-n", "Removes the identity colour from tabs"),
    ("-v", "Re-enables the tabview button"),
    ("-e", "Hides the extension button"),
    ("-s", "Single tab view (Tabs hidden when only one tab)"),
    ("-t <name>", "Colour theme (see Colour themes below)"),
    ("-i", "Increase contrast (see Colour themes below)"),
    ("-a", "Highlight the selected tab (see Colour themes below)"),
    ("-r", "Remove the theme"),
]


class PairedColumnsTests(unittest.TestCase):
    def test_fourteen_flags_take_seven_rows(self):
        self.assertEqual(len(style.paired_columns(FLAGS, width=100, on=False)), 7)

    def test_filled_top_to_bottom_in_the_order_given(self):
        lines = style.paired_columns(FLAGS, width=100, on=False)
        for row, line in enumerate(lines):
            self.assertTrue(line.startswith(f"    {FLAGS[row][0]}  "), line)
            self.assertIn(f"   {FLAGS[row + 7][0]}", line)

    def test_nothing_reaches_the_terminal_edge(self):
        for width in range(72, 111):
            with self.subTest(width=width):
                for line in style.paired_columns(FLAGS, width=width, on=False):
                    self.assertLess(len(line), width, line)

    def test_each_column_lines_up(self):
        lines = style.paired_columns(FLAGS, width=100, on=False)
        column = lines[0].index("-v")
        for row, line in enumerate(lines):
            self.assertTrue(line[column:].startswith(FLAGS[row + 7][0]), line)

    def test_text_is_cut_between_words(self):
        pairs = [("-a", "word " * 20), ("-b", "word " * 20)]
        for line in style.paired_columns(pairs, width=80, on=False):
            for cut in re.findall(rf"(\S+){style.ELLIPSIS}", line):
                self.assertEqual(cut, "word")

    def test_text_that_fits_is_left_whole(self):
        pairs = FLAGS[:2] + FLAGS[7:9]
        self.assertNotIn(style.ELLIPSIS, "".join(style.paired_columns(pairs, width=100, on=False)))

    def test_a_column_with_room_to_spare_gives_it_to_the_other(self):
        pairs = [("-a", "short"), ("-b", "x" * 70)]
        lines = style.paired_columns(pairs, width=100, on=False)
        self.assertIn("x" * 70, lines[0])

    def test_too_narrow_for_two_columns_says_so(self):
        self.assertIsNone(style.paired_columns(FLAGS, width=60, on=False))

    def test_short_text_is_not_mistaken_for_too_narrow(self):
        pairs = [("-a", "ab"), ("-b", "x" * 80)]
        self.assertIsNotNone(style.paired_columns(pairs, width=72, on=False))

    def test_a_single_pair_has_no_second_column(self):
        self.assertIsNone(style.paired_columns(FLAGS[:1], width=100, on=False))

    def test_an_odd_count_leaves_the_left_column_longer(self):
        lines = style.paired_columns(FLAGS[:3], width=100, on=False)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1], f"    -w  {FLAGS[1][1]}")

    def test_colour_changes_no_words_or_spacing(self):
        plain = style.paired_columns(FLAGS, width=100, on=False)
        coloured = style.paired_columns(FLAGS, width=100, on=True)
        self.assertEqual([style._visible(line) for line in coloured], plain)
        self.assertNotEqual(coloured, plain)


class TryInfoFlagsTests(unittest.TestCase):
    def info(self, flags, columns):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "chrome").mkdir()
            (Path(td) / "chrome/userChrome.css").write_text("#nav-bar { }\n")
            (Path(td) / "README.md").write_text(
                "".join(f"- `{flag}` {text}\n" for flag, text in flags), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    mock.patch.dict(os.environ, {"COLUMNS": str(columns), "NO_COLOR": "1"}):
                self.assertEqual(cli.main(["try", td, "--info"]), 0)
        block = out.getvalue().split("README install flags", 1)[1].splitlines()[1:]
        return block[:block.index("")] if "" in block else block

    def test_a_long_list_is_side_by_side(self):
        block = self.info(FLAGS, 100)
        self.assertEqual(len(block), 7)
        for flag, _ in FLAGS:
            self.assertIn(flag, "\n".join(block))

    def test_a_narrow_terminal_keeps_one_to_a_row_in_full(self):
        block = self.info(FLAGS, 60)
        self.assertGreaterEqual(len(block), 14)
        self.assertNotIn(style.ELLIPSIS, "\n".join(block))
        self.assertIn("(it will try to find the profile folder to place the theme within)",
                      " ".join(" ".join(block).split()))

    def test_a_short_list_keeps_one_to_a_row_in_full(self):
        block = self.info(FLAGS[:5], 100)
        self.assertGreaterEqual(len(block), 5)
        self.assertTrue(block[0].startswith("    -c  Left hand side tab close button"), block[0])
        self.assertNotIn(style.ELLIPSIS, "\n".join(block))


if __name__ == "__main__":
    unittest.main()
