"""Unit tests for the pure parts of fxcss -- no Firefox, no display, no network.

These pin behaviour that was previously only checked by throwaway scripts:
the fuzzy matcher's judgement calls, token extraction quirks, and the small
parsers. The full pipeline is exercised by the smoke job in CI; this file is
for the logic that can regress silently inside it.
"""

import tempfile
import unittest
from pathlib import Path


class NearMissTests(unittest.TestCase):
    """The matcher must accept Firefox's rename patterns and reject lookalikes.

    Every case here comes from a real WhiteSur audit. The rejected pairs are
    genuinely different controls that share naming scaffolding; suggesting one
    for the other would be worse than saying nothing.
    """

    def check(self, a, b, expected):
        from fxcss.audit import _is_near_miss
        self.assertIs(_is_near_miss(a, b), expected, f"{a} ~ {b}")

    def test_suffix_versioning_accepted(self):
        self.check("appMenu-fullscreen-button", "appMenu-fullscreen-button2", True)
        self.check("appMenu-fxa-label", "appMenu-fxa-label2", True)

    def test_typos_accepted(self):
        self.check("TabsToolbarß", "TabsToolbar", True)
        self.check("idenity-box", "identity-box", True)

    def test_unrelated_controls_rejected(self):
        self.check("appMenu-paste-button", "appMenu-translate-button", False)
        self.check("appMenu-cut-button", "appMenu-copy-button", False)
        self.check("urlbar-go-button", "urlbar-page-action", False)


class ReplaceInLineTests(unittest.TestCase):
    def replace(self, text, token, repl):
        from fxcss.audit import replace_in_line
        return replace_in_line(text, token, repl)

    def test_replaces_whole_token(self):
        self.assertEqual(
            self.replace("#idenity-box { color: red; }", "#idenity-box", "#identity-box"),
            "#identity-box { color: red; }")

    def test_does_not_touch_longer_tokens(self):
        # #urlbar must not rewrite the inside of #urlbar-background.
        line = "#urlbar-background { }"
        self.assertEqual(self.replace(line, "#urlbar", "#renamed"), line)

    def test_replaces_every_occurrence_in_line(self):
        self.assertEqual(
            self.replace(".a-b, .a-b { }", ".a-b", ".c-d"),
            ".c-d, .c-d { }")


class ExtractTokensTests(unittest.TestCase):
    def tokens_for(self, css):
        from fxcss.audit import extract_tokens
        with tempfile.TemporaryDirectory() as td:
            chrome = Path(td) / "chrome"
            chrome.mkdir()
            (chrome / "userChrome.css").write_text(css, encoding="utf-8")
            return extract_tokens(Path(td))

    def test_finds_ids_and_classes(self):
        toks = self.tokens_for("#nav-bar { }\n.tab-content { }\n")
        self.assertIn("#nav-bar", toks)
        self.assertIn(".tab-content", toks)

    def test_ignores_block_comments_across_lines(self):
        toks = self.tokens_for("/* talks about #urlbar-background\n"
                               "across lines */\n#real { }\n")
        self.assertNotIn("#urlbar-background", toks)
        self.assertIn("#real", toks)

    def test_ignores_hex_colours_and_hack_tokens(self):
        toks = self.tokens_for("#abc { color: #ffffff; }\n"
                               "x:not(#hack) { }\n")
        self.assertNotIn("#ffffff", toks)
        self.assertNotIn("#hack", toks)

    def test_line_numbers_survive_comment_blanking(self):
        toks = self.tokens_for("/* one\ntwo */\n#target { }\n")
        self.assertEqual(toks["#target"][0]["line"], 3)


class ImportGraphTests(unittest.TestCase):
    def test_orphan_is_not_reachable(self):
        from fxcss.audit import import_graph
        with tempfile.TemporaryDirectory() as td:
            chrome = Path(td) / "chrome"
            chrome.mkdir()
            (chrome / "userChrome.css").write_text('@import "linked.css";\n')
            (chrome / "linked.css").write_text("#a { }\n")
            (chrome / "orphan.css").write_text("#b { }\n")
            reachable = import_graph(Path(td))
            names = {p.name for p in reachable}
            self.assertIn("linked.css", names)
            self.assertNotIn("orphan.css", names)


class ParseRepoTests(unittest.TestCase):
    def parse(self, spec):
        from fxcss.fetch import parse_repo
        return parse_repo(spec)

    def test_accepted_shapes(self):
        for spec in ("owner/name",
                     "github.com/owner/name",
                     "https://github.com/owner/name",
                     "https://www.github.com/owner/name/",
                     "owner/name.git"):
            self.assertEqual(self.parse(spec), ("owner", "name"), spec)

    def test_rejected_shapes(self):
        from fxcss.fetch import parse_repo
        for spec in ("not a repo", "https://gitlab.com/o/n/x/y", "owner"):
            with self.assertRaises(ValueError, msg=spec):
                parse_repo(spec)


class FlagLineTests(unittest.TestCase):
    def test_readme_flag_bullets_parse(self):
        from fxcss.fetch import FLAG_LINE
        m = FLAG_LINE.match("- `-c` Left hand side tab close button")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "-c")
        m = FLAG_LINE.match("* --dark — start in dark mode")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "--dark")

    def test_prose_bullets_do_not_parse(self):
        from fxcss.fetch import FLAG_LINE
        self.assertIsNone(FLAG_LINE.match("- just a sentence about things"))


class SlugifyTests(unittest.TestCase):
    def test_url_slugs(self):
        from fxcss.core import slugify_url
        self.assertEqual(
            slugify_url("https://github.com/AdamXweb/WhiteSurFirefoxThemeMacOS"),
            "github-com-adamxweb-whitesurfirefoxthememacos")
        self.assertEqual(slugify_url("https://adam.kostarelas.com/?utm_source=x"),
                         "adam-kostarelas-com")
        self.assertEqual(slugify_url("https://x.com/a/b#frag"), "x-com-a-b")


class SamplePagesTests(unittest.TestCase):
    def test_pages_path_is_stable_across_calls(self):
        # The file:// path shows in the address bar, so it must not vary
        # between runs -- that was a real determinism bug.
        from fxcss.core import build_pages
        self.assertEqual(build_pages(), build_pages())


class DiffStatsTests(unittest.TestCase):
    def test_threshold_separates_noise_from_change(self):
        from PIL import Image
        from fxcss.compare import diff_stats
        a = Image.new("RGB", (10, 10), (100, 100, 100))
        b = a.copy()
        b.putpixel((5, 5), (100, 100, 110))     # below threshold: noise
        changed, total, _ = diff_stats(a, b)
        self.assertEqual((changed, total), (0, 100))
        b.putpixel((5, 5), (255, 255, 255))     # far above threshold
        changed, _, _ = diff_stats(a, b)
        self.assertEqual(changed, 1)


class ImportabilityTests(unittest.TestCase):
    def test_every_module_imports(self):
        # Doubles as the Python-floor check when CI runs this on 3.9.
        import fxcss.audit, fxcss.catalogue, fxcss.cli   # noqa: F401,E401
        import fxcss.compare, fxcss.core, fxcss.fetch    # noqa: F401,E401
        import fxcss.probe                               # noqa: F401


if __name__ == "__main__":
    unittest.main()


class TitleForTests(unittest.TestCase):
    def title(self, name):
        from fxcss.compare import title_for
        return title_for(name)

    def test_standard_views(self):
        self.assertEqual(self.title("light-01-window"), ("Browser window", "light"))
        self.assertEqual(self.title("dark-03-findbar"), ("Find bar", "dark"))

    def test_extra_views(self):
        self.assertEqual(self.title("extra-09-compact"), ("Compact density", "extra"))
        self.assertEqual(self.title("extra-12-customize"), ("Customize mode", "extra"))

    def test_variant_views_are_generic(self):
        self.assertEqual(self.title("variant-tabs-swapclose"),
                         ("Variant: tabs-swapclose", "variant"))

    def test_unknown_name_falls_back_to_itself(self):
        self.assertEqual(self.title("mystery-view")[0], "mystery-view")


class FindVariantSheetsTests(unittest.TestCase):
    def test_discovers_and_slugs(self):
        from fxcss.core import find_variant_sheets
        with tempfile.TemporaryDirectory() as td:
            theme = Path(td)
            (theme / "custom").mkdir()
            (theme / "custom" / "Compact Tabs.css").write_text("x{}")
            (theme / "optional").mkdir()
            (theme / "optional" / "no-line.css").write_text("y{}")
            (theme / "chrome").mkdir()
            (theme / "chrome" / "not-a-variant.css").write_text("z{}")
            sheets = find_variant_sheets(theme)
            self.assertEqual(sorted(sheets), ["compact-tabs", "no-line"])

    def test_empty_theme_has_none(self):
        from fxcss.core import find_variant_sheets
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(find_variant_sheets(Path(td)), {})
