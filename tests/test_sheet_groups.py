"""The stylesheet menu shows colour schemes apart from tweaks, when a theme has both."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from fxcss import cli, inventory, style


class SchemeNameTests(unittest.TestCase):
    def test_scheme_names(self):
        for name in ("theme-nord", "theme_ayu.css", "theme-material-blue", "nord", "gruvbox.css",
                     "cascade-mocha", "cascade-rose-pine", "one-dark", "tokyo-night",
                     "Dracula", "catppuccin-latte"):
            self.assertTrue(inventory.is_colour_scheme(name), name)

    def test_tweak_names(self):
        for name in ("compact-tabs", "tabs-swapclose", "noidentity", "increase-contrast",
                     "material-icons", "hide-sidebar", "nordic-spacing", "darker"):
            self.assertFalse(inventory.is_colour_scheme(name), name)


class SheetGroupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fxcss-groups-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def sheets(self, *stems, palette=()):
        """Sheets with distinct selectors, so no pair measures as alternatives;
        those in `palette` instead all set the same :root colours."""
        paths = []
        for n, stem in enumerate(stems):
            path = self.root / f"{stem}.css"
            if stem in palette:
                path.write_text(f":root {{ --bg: #{n:06x}; --fg: #{n + 1:06x} }}\n")
            else:
                path.write_text(f"#part-{n} {{ margin: {n}px }}\n")
            paths.append(path)
        return paths

    def test_both_kinds_are_grouped_tweaks_first(self):
        found = self.sheets("compact-tabs", "theme-dracula", "noidentity", "theme-nord")
        shown, groups = cli._sheet_groups(found)
        self.assertEqual([p.stem for p in shown],
                         ["compact-tabs", "noidentity", "theme-dracula", "theme-nord"])
        self.assertEqual(groups, [("Tweaks", 2), ("Colour schemes", 2)])

    def test_schemes_that_all_clash_say_pick_one(self):
        found = self.sheets("compact-tabs", "theme-dracula", "theme-nord", "theme-ayu",
                            palette={"theme-dracula", "theme-nord", "theme-ayu"})
        _, groups = cli._sheet_groups(found)
        self.assertEqual(groups[1], (f"Colour schemes {style.DOT} pick one", 3))

    def test_schemes_that_compose_do_not(self):
        # Two of the three clash; the third adds to either, so "pick one"
        # would be wrong about it.
        found = self.sheets("compact-tabs", "theme-dracula", "theme-nord", "theme-ayu",
                            palette={"theme-dracula", "theme-nord"})
        self.assertEqual(cli._sheet_groups(found)[1][1], ("Colour schemes", 3))

    def test_one_kind_keeps_the_single_list_in_its_order(self):
        for stems in (("tabs-swapclose", "compact-tabs"), ("theme-nord", "theme-ayu")):
            found = self.sheets(*stems)
            self.assertEqual(cli._sheet_groups(found), (found, None))

    def choose(self, found, *answers):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"FXCSS_PLAIN_PROMPTS": "1"}), \
                mock.patch("builtins.input", side_effect=answers), redirect_stdout(out):
            style.set_colour(False)
            try:
                chosen = cli._choose_sheets(found, True)
            finally:
                style.set_colour(None)
        return chosen, out.getvalue()

    def test_typed_numbers_follow_the_menu_and_the_answer_keeps_the_theme_order(self):
        found = self.sheets("compact-tabs", "theme-dracula", "noidentity")
        # Shown as 1 compact-tabs, 2 noidentity, 3 theme-dracula.
        chosen, text = self.choose(found, "3,2")
        self.assertEqual([p.stem for p in chosen], ["theme-dracula", "noidentity"])
        self.assertIn("    Tweaks", text)
        self.assertIn("    Colour schemes", text)
        self.assertIn("3. theme-dracula", text)
        self.assertIn("--with theme-dracula,noidentity", text)

    def test_a_clash_kept_is_not_offered_as_a_with_list(self):
        found = self.sheets("compact-tabs", "theme-dracula", "theme-nord",
                            palette={"theme-dracula", "theme-nord"})
        chosen, text = self.choose(found, "2,3", "2,3", "2,3")
        self.assertEqual([p.stem for p in chosen], ["theme-dracula", "theme-nord"])
        self.assertEqual(text.count("are alternatives, not additions"), 3)
        self.assertNotIn("skips this question", text)
        # Enter on the retry keeps the clash, and is not offered either.
        _, text = self.choose(found, "2,3", "")
        self.assertNotIn("skips this question", text)

    def test_a_clash_resolved_is(self):
        found = self.sheets("compact-tabs", "theme-dracula", "theme-nord",
                            palette={"theme-dracula", "theme-nord"})
        chosen, text = self.choose(found, "2,3", "1,3")
        self.assertEqual([p.stem for p in chosen], ["compact-tabs", "theme-nord"])
        self.assertIn("--with compact-tabs,theme-nord skips this question", text)

    def test_a_theme_with_one_kind_shows_no_titles(self):
        _, text = self.choose(self.sheets("compact-tabs", "noidentity"), "")
        self.assertNotIn("Tweaks", text)
        self.assertNotIn("Colour schemes", text)


if __name__ == "__main__":
    unittest.main()
