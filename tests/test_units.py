"""Unit tests for the pure parts of fxcss -- no Firefox, no display, no network.

These pin behaviour that was previously only checked by throwaway scripts:
the fuzzy matcher's judgement calls, token extraction quirks, and the small
parsers. The full pipeline is exercised by the smoke job in CI; this file is
for the logic that can regress silently inside it.
"""

import json
import re
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


class HeadOnlyViewsTests(unittest.TestCase):
    """A capture with no base-side counterpart must still count as a change.

    A PR that adds a variant stylesheet produces a head-only capture: nothing
    exists to diff it against, so it can never reach changed_views. If
    any_change stayed false, the preview comment would say "no visual change"
    about the one PR whose entire point is a new look.
    """

    def test_only_in_head_marks_any_change(self):
        from PIL import Image
        from fxcss.compare import run
        with tempfile.TemporaryDirectory() as td:
            base, head, out = (Path(td) / n for n in ("base", "head", "out"))
            base.mkdir()
            head.mkdir()
            img = Image.new("RGB", (8, 8), (100, 100, 100))
            img.save(base / "light-01-window.png")
            img.save(head / "light-01-window.png")
            img.save(head / "variant-new-look.png")
            run(base, head, out, "testos")
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual(summary["only_in_head"], ["variant-new-look"])
            self.assertEqual(summary["changed_views"], [])
            self.assertTrue(summary["any_change"])

    def test_identical_sides_stay_unchanged(self):
        from PIL import Image
        from fxcss.compare import run
        with tempfile.TemporaryDirectory() as td:
            base, head, out = (Path(td) / n for n in ("base", "head", "out"))
            base.mkdir()
            head.mkdir()
            img = Image.new("RGB", (8, 8), (100, 100, 100))
            img.save(base / "light-01-window.png")
            img.save(head / "light-01-window.png")
            run(base, head, out, "testos")
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual(summary["only_in_head"], [])
            self.assertFalse(summary["any_change"])


class ImportabilityTests(unittest.TestCase):
    def test_every_module_imports(self):
        # Doubles as the Python-floor check when CI runs this on 3.9.
        import fxcss.audit, fxcss.catalogue, fxcss.cli   # noqa: F401,E401
        import fxcss.compare, fxcss.core, fxcss.fetch    # noqa: F401,E401
        import fxcss.install, fxcss.probe                # noqa: F401,E401


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


class ScaffoldTests(unittest.TestCase):
    def test_variant_alternation(self):
        from fxcss.scaffold import variant_alternation
        self.assertEqual(variant_alternation([]), "")
        self.assertEqual(variant_alternation(["b-2", "a"]), "|variant-(?:a|b-2)")
        # Anything that could break out of the regex is dropped, not escaped:
        # the allowlist is a security boundary and only boring names belong.
        self.assertEqual(variant_alternation(["ok", "Bad.Name", "x|y"]),
                         "|variant-(?:ok)")

    def test_https_repo_url(self):
        from fxcss.scaffold import https_repo_url
        self.assertEqual(https_repo_url("git@github.com:o/r.git"),
                         "https://github.com/o/r")
        self.assertEqual(https_repo_url("https://github.com/o/r.git"),
                         "https://github.com/o/r")
        self.assertEqual(https_repo_url("https://github.com/o/r/"),
                         "https://github.com/o/r")
        self.assertIsNone(https_repo_url(""))
        self.assertIsNone(https_repo_url("not a remote"))

    def test_write_workflows_and_skip(self):
        from fxcss.scaffold import write_workflows
        with tempfile.TemporaryDirectory() as td:
            theme = Path(td)
            (theme / "chrome").mkdir()
            (theme / "custom").mkdir()
            (theme / "custom" / "my-variant.css").write_text("x{}")
            written, skipped = write_workflows(theme, ["my-variant"],
                                               version="9.9.9")
            self.assertEqual(len(written), 3)
            self.assertEqual(skipped, [])
            publish = (theme / ".github" / "workflows"
                       / "pr-preview-publish.yml").read_text()
            self.assertIn("|variant-(?:my-variant)", publish)
            self.assertNotIn("__FXCSS_", publish)
            preview = (theme / ".github" / "workflows" / "pr-preview.yml").read_text()
            self.assertIn("FXCSS_VERSION: 9.9.9", preview)
            self.assertIn("fxcss[images]==${{ env.FXCSS_VERSION }}", preview)
            self.assertNotIn("git+https", preview)
            # second run leaves the files alone
            written2, skipped2 = write_workflows(theme, [], version="9.9.9")
            self.assertEqual(written2, [])
            self.assertEqual(len(skipped2), 3)


class ChromaSensitivityTests(unittest.TestCase):
    def test_chroma_only_shift_is_detected(self):
        # A blue-grey to peach tint changes channels by (+19, -4, -30): its
        # luminance delta is ~11, under the threshold, but any theme user
        # would see it. The per-channel max must catch it.
        from PIL import Image
        from fxcss.compare import diff_stats
        a = Image.new("RGB", (10, 10), (0xEA, 0xEE, 0xFB))
        b = Image.new("RGB", (10, 10), (0xFD, 0xEA, 0xDD))
        changed, total, _ = diff_stats(a, b)
        self.assertEqual((changed, total), (100, 100))


class VariantSpecTests(unittest.TestCase):
    def available(self):
        return {"a": Path("/t/a.css"), "b": Path("/t/b.css"), "c": Path("/t/c.css")}

    def test_all_and_singles(self):
        from fxcss.core import parse_variant_spec
        self.assertEqual(sorted(parse_variant_spec("all", self.available())), ["a", "b", "c"])
        self.assertEqual(parse_variant_spec("b", self.available()),
                         {"b": [Path("/t/b.css")]})

    def test_combo_loads_together(self):
        from fxcss.core import parse_variant_spec
        result = parse_variant_spec("a+c,b", self.available())
        self.assertEqual(result["a+c"], [Path("/t/a.css"), Path("/t/c.css")])
        self.assertEqual(result["b"], [Path("/t/b.css")])

    def test_unknown_names_raise_listing_available(self):
        from fxcss.core import parse_variant_spec
        with self.assertRaises(ValueError) as ctx:
            parse_variant_spec("a+nope,zap", self.available())
        self.assertIn("nope", str(ctx.exception))
        self.assertIn("zap", str(ctx.exception))
        self.assertIn("a, b, c", str(ctx.exception))

    def test_combo_slug_is_regex_escaped_in_allowlist(self):
        from fxcss.scaffold import variant_alternation
        self.assertEqual(variant_alternation(["a+b", "plain"]),
                         "|variant-(?:a\\+b|plain)")


class TweaksMarkdownTests(unittest.TestCase):
    def test_document_shape(self):
        from fxcss.tweaks import render_markdown
        entries = [
            {"slug": "compact-tabs", "sheets": [Path("/t/custom/compact-tabs.css")],
             "percent": 3.21, "image": "compact-tabs-diff.png"},
            {"slug": "a+b", "sheets": [Path("/t/custom/a.css"), Path("/t/custom/b.css")],
             "percent": 5.0, "image": "a+b-diff.png"},
            {"slug": "stale-one", "sheets": [Path("/t/custom/stale-one.css")],
             "percent": 0.0, "image": None},
        ]
        flags = [{"flag": "-c", "text": "Left hand side tab close button"}]
        md = render_markdown(Path("/t"), entries, flags, "153.0.3")
        self.assertIn("<details>", md)
        self.assertIn("changes 3.21% of the chrome", md)
        self.assertIn("a + b", md)                       # combo title humanised
        self.assertIn("changes nothing on current Firefox", md)
        self.assertIn("| `-c` | Left hand side tab close button |", md)
        self.assertIn("![before and after of compact-tabs](compact-tabs-diff.png)", md)
        # relative paths only -- the doc must be committable anywhere
        self.assertNotIn("/t/custom", md.replace("`custom/", ""))


class NewThemeTests(unittest.TestCase):
    def test_scaffolds_the_starter(self):
        from fxcss.scaffold import new_theme
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "my-theme"
            created = new_theme(target)
            self.assertIn(Path("chrome/userChrome.css"), created)
            self.assertIn(Path("custom/accent-red.css"), created)
            text = (target / "chrome/userChrome.css").read_text()
            self.assertIn("--demo-accent", text)

    def test_refuses_non_empty_target(self):
        from fxcss.scaffold import new_theme
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            (target / "existing.txt").write_text("x")
            with self.assertRaises(FileExistsError):
                new_theme(target)


class FirefoxDiscoveryTests(unittest.TestCase):
    def fake_apps(self, td, names):
        for name in names:
            macos = Path(td) / f"{name}.app" / "Contents" / "MacOS"
            macos.mkdir(parents=True)
            binary = macos / "firefox"
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)

    def test_labels(self):
        from fxcss.core import _label_for
        self.assertEqual(_label_for("Firefox"), "stable")
        self.assertEqual(_label_for("Firefox Nightly"), "nightly")
        self.assertEqual(_label_for("Firefox Developer Edition"), "developer")
        self.assertEqual(_label_for("FirefoxESR"), "esr")
        self.assertEqual(_label_for("LibreWolf"), "librewolf")
        self.assertIsNone(_label_for("Google Chrome"))

    def test_discovery_orders_and_dedupes(self):
        from fxcss.core import discover_firefoxes
        with tempfile.TemporaryDirectory() as td:
            self.fake_apps(td, ["Firefox Nightly", "Firefox", "LibreWolf"])
            builds = discover_firefoxes(extra_roots=[Path(td)])
            labels = [b["label"] for b in builds if str(td) in b["path"]]
            self.assertEqual(labels, ["stable", "nightly", "librewolf"])

    def test_channel_resolution_and_helpful_error(self):
        import os
        from fxcss.core import find_firefox
        with tempfile.TemporaryDirectory() as td:
            self.fake_apps(td, ["Firefox Nightly"])
            os.environ["FXCSS_FIREFOX_ROOTS"] = td
            try:
                path = find_firefox("nightly")
                self.assertIn("Firefox Nightly.app", path)
                with self.assertRaises(SystemExit) as ctx:
                    find_firefox("floorp")
                self.assertIn("nightly", str(ctx.exception))
            finally:
                del os.environ["FXCSS_FIREFOX_ROOTS"]


class MenuChoiceTests(unittest.TestCase):
    def test_parse_choice(self):
        from fxcss.cli import _parse_choice
        self.assertEqual(_parse_choice("", 3, 0), 0)       # Enter = default
        self.assertEqual(_parse_choice("2", 3, 0), 1)
        self.assertEqual(_parse_choice("9", 3, 0), 0)      # out of range
        self.assertEqual(_parse_choice("x", 3, 0), 0)      # nonsense
        self.assertEqual(_parse_choice(None, 3, 2), 2)


class PillowFreeCoreTests(unittest.TestCase):
    """The base install must never touch PIL outside the three image commands.

    This exact regression shipped once: css_references lived in catalogue.py,
    whose module header imports PIL, so `fxcss pick` crashed for anyone
    installed without the [images] extra.
    """

    def run_blocked(self, code):
        import subprocess, sys
        return subprocess.run(
            [sys.executable, "-c", "import sys; sys.modules['PIL'] = None; " + code],
            capture_output=True, text=True)

    def test_core_modules_import_without_pillow(self):
        result = self.run_blocked(
            "import fxcss.cli, fxcss.core, fxcss.audit, fxcss.probe, "
            "fxcss.fetch, fxcss.scaffold, fxcss.install; "
            "from fxcss.audit import css_references; print('ok')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_image_commands_explain_instead_of_crashing(self):
        result = self.run_blocked(
            "from fxcss.cli import main; "
            "import sys; sys.exit(main(['compare', '--base', 'a', '--head', 'b', "
            "'--out', 'c', '--platform', 'p']))")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("needs Pillow", result.stderr)
        self.assertIn("pipx inject fxcss pillow", result.stderr)


class PublishAllowlistCoverageTests(unittest.TestCase):
    """Every view core captures must survive the publish workflow's allowlist.

    The allowlist filters an untrusted artifact, so it is deliberately an
    enumeration rather than a pattern -- and an enumeration someone has to
    remember to extend is one that silently drops views, which is exactly what
    happened once: five newer views vanished from PR comments with no error
    anywhere. This closes that door by construction.
    """

    def view_names(self):
        source = (Path(__file__).parent.parent / "fxcss" / "core.py").read_text()
        # Match the names wherever they are written, not just as a literal
        # argument to _shot: views captured in a loop keep their names in a
        # tuple, and an earlier version of this guard silently skipped those --
        # which is the same class of gap it exists to catch.
        names = set(re.findall(r'"(extra-\d{2}-[a-z0-9-]+)"', source))
        for name in re.findall(r'f"\{mode\}-(\d{2}-[a-z0-9-]+)"', source):
            names.update({f"light-{name}", f"dark-{name}"})
        return names

    def test_every_captured_view_is_publishable(self):
        template = (Path(__file__).parent.parent / "fxcss" / "templates"
                    / "pr-preview-publish.yml").read_text()
        pattern = re.search(r"const NAME = /\^(.+?)\$/", template).group(1)
        pattern = pattern.replace("__FXCSS_VARIANT_ALT__", "")
        names = self.view_names()
        self.assertGreaterEqual(len(names), 12, "view extraction looks broken")
        for name in sorted(names):
            with self.subTest(view=name):
                self.assertRegex(name + ".png", pattern,
                                 f"{name} is captured but the publish allowlist "
                                 f"drops it; add it to NAME in "
                                 f"templates/pr-preview-publish.yml")

    def test_head_only_views_are_surfaced(self):
        # A head-only capture (a variant the PR adds) has no diff image, so the
        # comment must pull it from summary.only_in_head -- otherwise the one
        # PR whose whole point is a new view reads as "no visual change".
        template = (Path(__file__).parent.parent / "fxcss" / "templates"
                    / "pr-preview-publish.yml").read_text()
        self.assertIn("s.only_in_head", template)
        self.assertIn("new in this PR", template)

    def test_every_extra_view_has_a_title(self):
        template = (Path(__file__).parent.parent / "fxcss" / "templates"
                    / "pr-preview-publish.yml").read_text()
        titled = set(re.findall(r"'([0-9]{2}-[a-z0-9-]+)':", template))
        for name in sorted(self.view_names()):
            key = name.split("-", 1)[1] if name.startswith(("light-", "dark-")) else name
            key = key[len("extra-"):] if key.startswith("extra-") else key
            with self.subTest(view=name):
                self.assertIn(key, titled,
                              f"{name} would appear in the PR comment as a raw "
                              f"slug; add it to TITLES")


class ToolbarSpecTests(unittest.TestCase):
    def parse(self, spec):
        from fxcss.core import parse_toolbar_spec
        return parse_toolbar_spec(spec)

    def test_move_remove_and_position(self):
        ops = self.parse("new-tab-button>nav-bar, -downloads-button, "
                         "home-button>nav-bar@2")
        self.assertEqual([o["op"] for o in ops], ["move", "remove", "move"])
        self.assertEqual(ops[0], {"op": "move", "widget": "new-tab-button",
                                  "area": "nav-bar", "position": None})
        self.assertEqual(ops[1]["widget"], "downloads-button")
        self.assertEqual(ops[2]["position"], 2)

    def test_order_is_preserved(self):
        # Positions are resolved against the arrangement as it stands when each
        # step runs, so reordering the ops would change the result.
        ops = self.parse("a>nav-bar@0, b>nav-bar@0")
        self.assertEqual([o["widget"] for o in ops], ["a", "b"])

    def test_whitespace_and_empties_are_tolerated(self):
        self.assertEqual(len(self.parse("  a>nav-bar ,, -b ,  ")), 2)
        self.assertEqual(self.parse(""), [])
        self.assertEqual(self.parse(None), [])

    def test_rejects_nonsense_with_a_usable_message(self):
        for spec, expected in (("new-tab-button", "widget>area"),
                               ("x>somewhere", "not a toolbar area"),
                               ("-", "no widget"),
                               (">nav-bar", "no widget"),
                               ("a>nav-bar@x", "not a position number")):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError) as ctx:
                    self.parse(spec)
                self.assertIn(expected, str(ctx.exception))

    def test_default_arrangement_is_valid_and_small(self):
        from fxcss.core import DEFAULT_TOOLBAR, default_toolbar_ops
        ops = default_toolbar_ops()
        self.assertTrue(all(o["area"] == "nav-bar" for o in ops))
        # A longer list overflows the nav bar at the capture window width,
        # which hides the widgets the view exists to show.
        self.assertLessEqual(len(ops), 6, DEFAULT_TOOLBAR)


class ProfilesIniTests(unittest.TestCase):
    """profiles.ini fixtures modelled on real files, one shape per Firefox era.

    A wrong answer here does not crash anything -- it installs a theme into
    the profile the user does not use, which reads as 'fxcss did nothing'.
    """

    MODERN = """\
[Install4F96D1932A9F858E]
Default=Profiles/abcd1234.default-release
Locked=1

[Profile1]
Name=default
IsRelative=1
Path=Profiles/oldstyle.default
Default=1

[Profile0]
Name=default-release
IsRelative=1
Path=Profiles/abcd1234.default-release

[General]
StartWithLastProfile=1
Version=2
"""

    def parse(self, text):
        from fxcss.install import parse_profiles_ini
        return parse_profiles_ini(text)

    def pick(self, text):
        from fxcss.install import pick_default
        return pick_default(*self.parse(text))

    def test_install_section_beats_old_style_default_flag(self):
        # Firefox 67+ records the profile each installation opens in an
        # [Install*] section; the Default=1 flag is the pre-67 mechanism and
        # frequently points at a stale profile on upgraded machines.
        picked = self.pick(self.MODERN)
        self.assertEqual(picked["name"], "default-release")

    def test_locked_install_section_still_parses(self):
        profiles, install_defaults = self.parse(self.MODERN)
        self.assertEqual(install_defaults,
                         ["Profiles/abcd1234.default-release"])
        self.assertEqual(len(profiles), 2)

    def test_old_style_default_flag(self):
        picked = self.pick("[Profile0]\nName=a\nIsRelative=1\nPath=Profiles/a\n\n"
                           "[Profile1]\nName=b\nIsRelative=1\nPath=Profiles/b\n"
                           "Default=1\n")
        self.assertEqual(picked["name"], "b")

    def test_single_profile_needs_no_default_marker(self):
        picked = self.pick("[Profile0]\nName=only\nIsRelative=1\n"
                           "Path=Profiles/only.default\n")
        self.assertEqual(picked["name"], "only")

    def test_several_profiles_without_a_default_is_ambiguous(self):
        self.assertIsNone(
            self.pick("[Profile0]\nName=a\nIsRelative=1\nPath=Profiles/a\n\n"
                      "[Profile1]\nName=b\nIsRelative=1\nPath=Profiles/b\n"))

    def test_disagreeing_install_sections_are_ambiguous(self):
        # Two Firefox installations, two default profiles: guessing silently
        # would put the theme where the user did not mean it to go.
        self.assertIsNone(
            self.pick("[InstallAAA]\nDefault=Profiles/a\n\n"
                      "[InstallBBB]\nDefault=Profiles/b\n\n"
                      "[Profile0]\nName=a\nIsRelative=1\nPath=Profiles/a\n\n"
                      "[Profile1]\nName=b\nIsRelative=1\nPath=Profiles/b\n"))

    def test_absolute_path_profile_is_not_resolved_against_root(self):
        from fxcss.install import discover_profiles
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "profiles.ini").write_text(
                "[Profile0]\nName=portable\nIsRelative=0\n"
                "Path=/somewhere/else/profile\n", encoding="utf-8")
            (found,) = discover_profiles(roots=[root])
            self.assertEqual(found["path"], Path("/somewhere/else/profile"))
            self.assertTrue(found["default"])

    def test_relative_path_resolves_against_its_root(self):
        from fxcss.install import discover_profiles
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "profiles.ini").write_text(self.MODERN, encoding="utf-8")
            found = discover_profiles(roots=[root])
            by_name = {p["name"]: p for p in found}
            self.assertEqual(by_name["default-release"]["path"],
                             root / "Profiles" / "abcd1234.default-release")
            self.assertTrue(by_name["default-release"]["default"])
            self.assertFalse(by_name["default"]["default"])

    def test_missing_or_junk_ini_yields_no_profiles(self):
        from fxcss.install import discover_profiles
        self.assertEqual(self.parse("")[0], [])
        self.assertEqual(self.parse("; comment only\nnot ini at all\n")[0], [])
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(discover_profiles(roots=[Path(td)]), [])


class MatchProfileTests(unittest.TestCase):
    def profiles(self):
        return [{"name": "default-release", "path": Path("/r/Profiles/x.default-release"),
                 "root": Path("/r"), "default": True},
                {"name": "dev", "path": Path("/r/Profiles/y.dev"),
                 "root": Path("/r"), "default": False}]

    def test_matches_by_name_and_dir_name(self):
        from fxcss.install import match_profile
        self.assertEqual(match_profile(self.profiles(), "dev")["name"], "dev")
        self.assertEqual(
            match_profile(self.profiles(), "x.default-release")["name"],
            "default-release")

    def test_unknown_name_lists_what_exists(self):
        from fxcss.install import match_profile
        with self.assertRaises(ValueError) as ctx:
            match_profile(self.profiles(), "nope")
        self.assertIn("default-release", str(ctx.exception))

    def test_explicit_path_must_look_like_a_profile(self):
        # A typo'd --profile path must not become the place a theme lands.
        from fxcss.install import match_profile
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError) as ctx:
                match_profile([], td)
            self.assertIn("does not look like a Firefox profile",
                          str(ctx.exception))
            (Path(td) / "prefs.js").write_text("", encoding="utf-8")
            picked = match_profile([], td)
            self.assertEqual(picked["path"], Path(td))


class UserJsBlockTests(unittest.TestCase):
    def test_block_carries_the_stylesheet_pref(self):
        from fxcss.install import user_js_with_block, STYLESHEET_PREF
        text = user_js_with_block("", STYLESHEET_PREF)
        self.assertIn("toolkit.legacyUserProfileCustomizations.stylesheets",
                      text)
        self.assertIn("/* >>> fxcss install >>> */", text)

    def test_reinstall_replaces_rather_than_stacks(self):
        from fxcss.install import user_js_with_block
        once = user_js_with_block('user_pref("mine", 1);\n', "a();")
        twice = user_js_with_block(once, "b();")
        self.assertEqual(twice.count("/* >>> fxcss install >>> */"), 1)
        self.assertIn('user_pref("mine", 1);', twice)
        self.assertIn("b();", twice)
        self.assertNotIn("a();", twice)

    def test_strip_removes_block_and_keeps_the_rest(self):
        from fxcss.install import user_js_with_block, strip_user_js_block
        text = user_js_with_block('user_pref("mine", 1);\n', "theirs();")
        stripped = strip_user_js_block(text)
        self.assertEqual(stripped, 'user_pref("mine", 1);\n')

    def test_unclosed_block_is_left_alone(self):
        # Deleting from a lone BEGIN to EOF could take a user's own lines;
        # a duplicated pref is harmless where a lost one is not.
        from fxcss.install import strip_user_js_block
        text = ('/* >>> fxcss install >>> */\n'
                'user_pref("theirs", 1);\n'
                'user_pref("written-after", 1);\n')
        self.assertEqual(strip_user_js_block(text), text)


class VariantDestinationTests(unittest.TestCase):
    def test_import_site_names_the_spot(self):
        # WhiteSur's theme.css imports custom/<name>.css and relies on its
        # installer copying the sheet there; the import of a missing file
        # failing silently is the whole on/off mechanism.
        from fxcss.install import variant_destination
        with tempfile.TemporaryDirectory() as td:
            chrome = Path(td) / "chrome"
            (chrome / "WhiteSur").mkdir(parents=True)
            (chrome / "WhiteSur" / "theme.css").write_text(
                '@import "custom/compact-tabs.css";\n', encoding="utf-8")
            self.assertEqual(
                variant_destination(chrome, "compact-tabs.css"),
                chrome / "WhiteSur" / "custom" / "compact-tabs.css")

    def test_unreferenced_sheet_has_no_destination(self):
        from fxcss.install import variant_destination
        with tempfile.TemporaryDirectory() as td:
            chrome = Path(td) / "chrome"
            chrome.mkdir()
            (chrome / "userChrome.css").write_text("#a { }\n", encoding="utf-8")
            self.assertIsNone(variant_destination(chrome, "compact-tabs.css"))

    def test_import_may_not_point_a_write_outside_chrome(self):
        from fxcss.install import variant_destination
        with tempfile.TemporaryDirectory() as td:
            chrome = Path(td) / "chrome"
            chrome.mkdir()
            (chrome / "userChrome.css").write_text(
                '@import "../../evil/compact-tabs.css";\n', encoding="utf-8")
            self.assertIsNone(variant_destination(chrome, "compact-tabs.css"))


def _make_theme(root):
    """A miniature WhiteSur: an @import chain and a custom/ sheet it names."""
    theme = root / "theme"
    (theme / "chrome" / "WhiteSur").mkdir(parents=True)
    (theme / "chrome" / "userChrome.css").write_text(
        '@import "WhiteSur/theme.css";\n@import "customChrome.css";\n',
        encoding="utf-8")
    (theme / "chrome" / "WhiteSur" / "theme.css").write_text(
        '@import "custom/compact-tabs.css";\n#nav-bar { }\n',
        encoding="utf-8")
    (theme / "custom").mkdir()
    (theme / "custom" / "compact-tabs.css").write_text(
        ".tab { height: 28px; }\n", encoding="utf-8")
    (theme / "custom" / "loose-sheet.css").write_text(
        "#loose { }\n", encoding="utf-8")
    (theme / "configuration").mkdir()
    (theme / "configuration" / "user.js").write_text(
        'user_pref("svg.context-properties.content.enabled", true);\n',
        encoding="utf-8")
    return theme


def _make_profile(root):
    profile = root / "profile"
    (profile / "chrome").mkdir(parents=True)
    (profile / "chrome" / "userChrome.css").write_text(
        "/* the user's own */\n", encoding="utf-8")
    (profile / "prefs.js").write_text("", encoding="utf-8")
    (profile / "user.js").write_text('user_pref("mine", 1);\n',
                                     encoding="utf-8")
    return profile


class InstallThemeTests(unittest.TestCase):
    """Install into a fake profile directory -- pure filesystem, no Firefox."""

    def test_install_backs_up_records_and_enables(self):
        from fxcss.install import install_theme, read_manifest
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            theme, profile = _make_theme(root), _make_profile(root)
            result = install_theme(theme, profile, "o/n@v1",
                                   sheets=[theme / "custom" / "compact-tabs.css"],
                                   stamp="20260814120000")
            self.assertEqual(result["backup"], "chrome.backup-20260814120000")
            self.assertEqual(
                (profile / result["backup"] / "userChrome.css").read_text(),
                "/* the user's own */\n")
            # theme is in place, with the sheet where the @import expects it
            self.assertIn("WhiteSur/theme.css",
                          (profile / "chrome" / "userChrome.css").read_text())
            self.assertTrue((profile / "chrome" / "WhiteSur" / "custom"
                             / "compact-tabs.css").is_file())
            # the shipped-nowhere customChrome.css import resolves
            self.assertTrue((profile / "chrome" / "customChrome.css").is_file())
            user_js = (profile / "user.js").read_text()
            self.assertIn('user_pref("mine", 1);', user_js)
            self.assertIn("legacyUserProfileCustomizations", user_js)
            self.assertIn("svg.context-properties", user_js)
            manifest = read_manifest(profile)
            self.assertEqual(manifest["theme"], "o/n@v1")
            self.assertEqual(manifest["backup"], result["backup"])
            self.assertFalse(manifest["user_js_created"])
            self.assertIn("chrome/WhiteSur/custom/compact-tabs.css",
                          manifest["files"])

    def test_unreferenced_sheet_falls_back_to_an_import(self):
        from fxcss.install import install_theme
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            theme, profile = _make_theme(root), _make_profile(root)
            install_theme(theme, profile, "o/n@v1",
                          sheets=[theme / "custom" / "loose-sheet.css"])
            self.assertTrue((profile / "chrome" / "custom"
                             / "loose-sheet.css").is_file())
            self.assertIn('@import "custom/loose-sheet.css";',
                          (profile / "chrome" / "userChrome.css").read_text())

    def test_two_installs_in_one_second_keep_both_backups(self):
        from fxcss.install import install_theme
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            theme, profile = _make_theme(root), _make_profile(root)
            first = install_theme(theme, profile, "o/n@v1",
                                  stamp="20260814120000")
            second = install_theme(theme, profile, "o/n@v1",
                                   stamp="20260814120000")
            self.assertEqual(first["backup"], "chrome.backup-20260814120000")
            self.assertEqual(second["backup"], "chrome.backup-20260814120000-2")
            self.assertTrue((profile / first["backup"]).is_dir())
            self.assertTrue((profile / second["backup"]).is_dir())

    def test_missing_profile_or_theme_refuses(self):
        from fxcss.install import install_theme
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            theme = _make_theme(root)
            with self.assertRaises(RuntimeError):
                install_theme(theme, root / "no-such-profile", "x")
            profile = _make_profile(root)
            with self.assertRaises(RuntimeError):
                install_theme(root / "not-a-theme", profile, "x")


class UninstallThemeTests(unittest.TestCase):
    def installed(self, root):
        from fxcss.install import install_theme
        theme = _make_theme(root)
        profile = _make_profile(root)
        install_theme(theme, profile, "o/n@v1",
                      sheets=[theme / "custom" / "compact-tabs.css"],
                      stamp="20260814120000")
        return profile

    def test_uninstall_restores_the_backup_exactly(self):
        from fxcss.install import uninstall_theme
        with tempfile.TemporaryDirectory() as td:
            profile = self.installed(Path(td))
            summary = uninstall_theme(profile)
            self.assertGreater(summary["removed"], 0)
            self.assertEqual(summary["restored"],
                             "chrome.backup-20260814120000")
            self.assertEqual(
                (profile / "chrome" / "userChrome.css").read_text(),
                "/* the user's own */\n")
            self.assertEqual(list(profile.glob("chrome.backup-*")), [])
            user_js = (profile / "user.js").read_text()
            self.assertEqual(user_js, 'user_pref("mine", 1);\n')

    def test_a_doctored_backup_path_is_ignored(self):
        import json
        from fxcss.install import uninstall_theme
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = self.installed(root)
            precious = root / "precious"
            precious.mkdir()
            (precious / "keep.txt").write_text("mine\n", encoding="utf-8")
            manifest = profile / "chrome" / "fxcss-install.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["backup"] = "../precious"
            manifest.write_text(json.dumps(data), encoding="utf-8")
            summary = uninstall_theme(profile)
            # the outside directory stays put, nothing is "restored" from it
            self.assertTrue((precious / "keep.txt").is_file())
            self.assertIsNone(summary["restored"])

    def test_files_the_user_added_survive_and_block_no_restore(self):
        from fxcss.install import uninstall_theme
        with tempfile.TemporaryDirectory() as td:
            profile = self.installed(Path(td))
            added = profile / "chrome" / "my-notes.css"
            added.write_text("/* added after install */\n", encoding="utf-8")
            summary = uninstall_theme(profile)
            self.assertTrue(added.is_file())
            self.assertEqual(summary["kept"], ["chrome/my-notes.css"])
            # the backup is not allowed to clobber the survivor
            self.assertIsNone(summary["restored"])
            self.assertTrue(
                (profile / "chrome.backup-20260814120000").is_dir())

    def test_without_manifest_nothing_is_deleted(self):
        from fxcss.install import uninstall_theme
        with tempfile.TemporaryDirectory() as td:
            profile = self.installed(Path(td))
            (profile / "chrome" / "fxcss-install.json").unlink()
            summary = uninstall_theme(profile, stamp="20260814130000")
            self.assertEqual(summary["moved_aside"],
                             "chrome.removed-20260814130000")
            self.assertEqual(summary["restored"],
                             "chrome.backup-20260814120000")
            self.assertEqual(
                (profile / "chrome" / "userChrome.css").read_text(),
                "/* the user's own */\n")
            self.assertTrue((profile / summary["moved_aside"]).is_dir())

    def test_nothing_installed_is_an_error_not_a_wipe(self):
        from fxcss.install import uninstall_theme
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td)
            (profile / "chrome").mkdir()
            (profile / "chrome" / "userChrome.css").write_text(
                "/* hand-made */\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                uninstall_theme(profile)
            self.assertTrue((profile / "chrome" / "userChrome.css").is_file())

    def test_tampered_manifest_cannot_reach_outside_chrome(self):
        import json
        from fxcss.install import uninstall_theme
        with tempfile.TemporaryDirectory() as td:
            profile = self.installed(Path(td))
            outside = profile / "prefs.js"
            manifest = profile / "chrome" / "fxcss-install.json"
            data = json.loads(manifest.read_text())
            data["files"] += ["prefs.js", "../outside.txt", "/etc/passwd",
                              "chrome/../prefs.js"]
            manifest.write_text(json.dumps(data), encoding="utf-8")
            uninstall_theme(profile)
            self.assertTrue(outside.is_file())
