"""The stylesheet menu shows colour schemes apart from tweaks, when a theme has both."""

import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from fxcss import cli, inventory, sheets, style


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
                path.write_text(f":root {{ --bg: #{n * 40:02x}2030; --fg: #e0e0{n * 40:02x} }}\n")
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
        replies = iter(answers)

        def typed(prompt=""):
            reply = next(replies)
            print(prompt + reply)
            return reply
        with mock.patch.dict(os.environ, {"FXCSS_PLAIN_PROMPTS": "1"}), \
                mock.patch("builtins.input", typed), redirect_stdout(out):
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

    def test_a_clash_asks_which_to_keep_and_enter_keeps_the_last(self):
        found = self.sheets("compact-tabs", "theme-dracula", "theme-nord",
                            palette={"theme-dracula", "theme-nord"})
        chosen, text = self.choose(found, "1,2,3", "")
        self.assertEqual([p.stem for p in chosen], ["compact-tabs", "theme-nord"])
        self.assertIn("Keep which colour scheme?", text)
        self.assertIn("both set the same 2 colours; only one can take effect", text)
        self.assertIn("Which one? 1-2 [2]", text)
        self.assertNotIn("Pick one of each pair", text)
        self.assertIn("--with compact-tabs,theme-nord skips this question", text)

    def test_the_kept_sheet_is_the_one_picked(self):
        found = self.sheets("compact-tabs", "theme-dracula", "theme-nord",
                            palette={"theme-dracula", "theme-nord"})
        chosen, text = self.choose(found, "2,3", "1")
        self.assertEqual([p.stem for p in chosen], ["theme-dracula"])
        self.assertIn("--with theme-dracula skips this question", text)

    def test_each_choice_shows_its_hex_colours(self):
        found = self.sheets("compact-tabs", "theme-dracula", "theme-nord",
                            palette={"theme-dracula", "theme-nord"})
        _, text = self.choose(found, "2,3", "")
        rows = [l for l in text.splitlines() if ". theme-" in l and "#" in l]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(len(re.findall(r"#[0-9a-f]{6}\b", row)), 2, row)
        self.assertTrue(rows[1].endswith("  (Enter)"))
        # (Enter) sits just after the colours, not out at the terminal's edge.
        self.assertLess(len(rows[1]), 60)

    def test_three_that_all_clash_are_one_question(self):
        found = self.sheets("compact-tabs", "theme-ayu", "theme-dracula", "theme-nord",
                            palette={"theme-ayu", "theme-dracula", "theme-nord"})
        chosen, text = self.choose(found, "2,3,4", "2")
        self.assertEqual([p.stem for p in chosen], ["theme-dracula"])
        self.assertEqual(text.count("Keep which"), 1)
        self.assertIn("all set the same 2 colours", text)

    def test_a_chain_asks_only_about_what_clashes(self):
        # a clashes with b and b with c, but a and c compose: keeping a
        # settles it, keeping b leaves b against c to ask about next.
        (self.root / "theme-a.css").write_text(":root { --x: #111111; --y: #222222 }\n")
        (self.root / "theme-b.css").write_text(
            ":root { --x: #333333; --y: #444444; --z: #555555; --w: #666666 }\n")
        (self.root / "theme-c.css").write_text(":root { --z: #777777; --w: #888888 }\n")
        (self.root / "compact.css").write_text("#tabs { margin: 0 }\n")
        found = [self.root / f"{n}.css" for n in ("compact", "theme-a", "theme-b", "theme-c")]
        chosen, text = self.choose(found, "2,3,4", "1")
        self.assertEqual([p.stem for p in chosen], ["theme-a", "theme-c"])
        self.assertEqual(text.count("Keep which"), 1)
        chosen, text = self.choose(found, "2,3,4", "2", "1")
        self.assertEqual([p.stem for p in chosen], ["theme-b"])
        self.assertEqual(text.count("Keep which"), 2)

    def test_a_theme_with_one_kind_shows_no_titles(self):
        _, text = self.choose(self.sheets("compact-tabs", "noidentity"), "")
        self.assertNotIn("Tweaks", text)
        self.assertNotIn("Colour schemes", text)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fxcss-preview-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        style.set_colour(True)
        self.addCleanup(style.set_colour, None)

    def test_swatch_uses_what_the_terminal_has(self):
        with mock.patch.dict(os.environ, {"COLORTERM": "truecolor", "TERM": "xterm"}):
            self.assertIn("\033[38;2;136;192;208m", style.swatch("#88c0d0"))
        with mock.patch.dict(os.environ, {"COLORTERM": "", "TERM": "xterm-256color"}):
            self.assertIn("\033[38;5;", style.swatch("#88c0d0"))
        with mock.patch.dict(os.environ, {"COLORTERM": "", "TERM": "xterm"}):
            self.assertEqual(style.swatch("#88c0d0"), "")
        style.set_colour(False)
        with mock.patch.dict(os.environ, {"COLORTERM": "truecolor"}):
            self.assertEqual(style.swatch("#88c0d0"), "")

    def test_nearest_256(self):
        self.assertEqual(style._nearest_256((255, 0, 0)), 196)
        self.assertEqual(style._nearest_256((0x1e, 0x1e, 0x2e)), 235)    # a grey
        self.assertEqual(style.rgb("#abc"), (170, 187, 204))
        self.assertEqual(style.rgb("#11223344"), (17, 34, 51))

    def test_palette_prefers_distinct_colours(self):
        a, b = self.root / "a.css", self.root / "b.css"
        a.write_text(":root { --bg: #1e1e2e; --bg2: #181825; --bg3: #11111b; --bg4: #1e1e2e;"
                     " --fg: #cdd6f4; --accent: #cba6f7 !important; --mix: var(--bg) }\n")
        b.write_text(":root { --bg: #2e3440; --bg2: #3b4252; --bg3: #434c5e; --bg4: #2e3440;"
                     " --fg: #eceff4; --accent: #88c0d0; --mix: var(--fg) }\n")
        colours = sheets.palettes([a, b])
        self.assertEqual(colours[a], ["#1e1e2e", "#cdd6f4", "#cba6f7"])
        self.assertEqual(colours[b][:1], ["#2e3440"])
        self.assertIn("#eceff4", colours[b])


if __name__ == "__main__":
    unittest.main()
