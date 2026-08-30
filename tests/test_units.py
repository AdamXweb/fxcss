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


class StartupRaceTests(unittest.TestCase):
    """Only the initial-browser race may be retried; real failures must not.

    The accepted string below is the verbatim error from the Windows runners
    that motivated the retry (seen twice, at v0.9.0 and v0.12.0). Retrying
    anything broader would paper over genuine script bugs three times before
    reporting them.
    """

    def is_race(self, message):
        from fxcss.core import MarionetteError, _is_startup_race
        return _is_startup_race(MarionetteError(message))

    def test_the_observed_ci_error_is_retryable(self):
        self.assertTrue(self.is_race(
            "WebDriver:ExecuteScript failed: {'error': 'javascript error', "
            "'message': 'TypeError: can\\'t access property "
            "\"maybeCancelContentJSExecution\", "
            "this._browser.frameLoader.remoteTab is null'}"))

    def test_a_missing_frameloader_is_retryable(self):
        self.assertTrue(self.is_race("TypeError: browser.frameLoader is null"))

    def test_real_failures_are_not(self):
        for message in ("WebDriver:ExecuteScript failed: timeout",
                        "TypeError: gb.addTab is not a function",
                        "ReferenceError: remoteTab is not defined",
                        "Marionette connection closed unexpectedly"):
            with self.subTest(message=message):
                self.assertFalse(self.is_race(message))


class StartupRaceRetryTests(unittest.TestCase):
    """The retry budget must outlast a slow runner without masking real bugs.

    The original budget -- two retries, 2s apart -- was observed being fully
    spent on a windows-latest run that then failed on the same race, which is
    why the delays now back off exponentially. These tests pin the contract:
    only the race is retried, every configured delay is actually slept, and
    the budget is finite.
    """

    RACE = ("TypeError: can't access property "
            '"maybeCancelContentJSExecution", '
            "this._browser.frameLoader.remoteTab is null")

    def run_retry(self, outcomes):
        """Drive _retry_startup_race over scripted attempts.

        outcomes holds one entry per allowed attempt: an error message to
        raise, or None to succeed. Returns (result-or-exception, attempts
        actually made, delays actually slept).
        """
        from fxcss.core import MarionetteError, _retry_startup_race
        remaining, sleeps, made = iter(outcomes), [], []

        def operation():
            message = next(remaining)
            made.append(message)
            if message is None:
                return "ok"
            raise MarionetteError(message)

        try:
            result = _retry_startup_race(operation, sleep=sleeps.append)
        except MarionetteError as exc:
            result = exc
        return result, len(made), sleeps

    def test_a_slow_runner_is_waited_out(self):
        # Three consecutive races would have exhausted the old budget.
        result, attempts, _ = self.run_retry([self.RACE] * 3 + [None])
        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 4)

    def test_delays_back_off_and_widen_the_old_budget(self):
        from fxcss.core import STARTUP_RACE_DELAYS
        result, _, sleeps = self.run_retry(
            [self.RACE] * len(STARTUP_RACE_DELAYS) + [None])
        self.assertEqual(result, "ok")
        self.assertEqual(sleeps, list(STARTUP_RACE_DELAYS))
        # The failed run showed ~4s was not enough, so the total wait must be
        # substantially larger, and each delay should grow so fast runners
        # pay little while slow ones get real headroom.
        self.assertGreater(sum(sleeps), 10)
        self.assertEqual(sleeps, sorted(sleeps))

    def test_the_race_still_fails_once_the_budget_is_spent(self):
        from fxcss.core import MarionetteError, STARTUP_RACE_DELAYS
        budget = len(STARTUP_RACE_DELAYS) + 1
        result, attempts, sleeps = self.run_retry([self.RACE] * budget)
        self.assertIsInstance(result, MarionetteError)
        self.assertEqual(attempts, budget)
        self.assertEqual(len(sleeps), len(STARTUP_RACE_DELAYS))

    def test_real_failures_spend_no_budget(self):
        from fxcss.core import MarionetteError
        result, attempts, sleeps = self.run_retry(
            ["TypeError: gb.addTab is not a function"])
        self.assertIsInstance(result, MarionetteError)
        self.assertEqual(attempts, 1)
        self.assertEqual(sleeps, [])

    def test_success_needs_no_retry(self):
        result, attempts, sleeps = self.run_retry([None])
        self.assertEqual((result, attempts, sleeps), ("ok", 1, []))


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
        import fxcss.install, fxcss.probe, fxcss.sheets  # noqa: F401,E401
        import fxcss.adopt, fxcss.omni                   # noqa: F401,E401


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


class ReadmePreviewsTemplateTests(unittest.TestCase):
    """The previews workflow is opt-in, and must read `shot`'s output shape."""

    def _write(self, **kwargs):
        from fxcss.scaffold import write_workflows
        td = tempfile.mkdtemp()
        theme = Path(td)
        (theme / "chrome").mkdir()
        written, _ = write_workflows(theme, [], version="9.9.9", **kwargs)
        return theme, written

    def test_opt_in_only(self):
        theme, written = self._write()
        self.assertNotIn(Path(".github/workflows/readme-previews.yml"), written)
        theme, written = self._write(previews=True)
        self.assertIn(Path(".github/workflows/readme-previews.yml"), written)

    def test_reads_shot_output_not_compare_output(self):
        # The bug this guards: `shot` writes captures FLAT into --out, while
        # the full/ subdirectory belongs to `compare`. Copying from
        # shots/full/ published nothing and failed the job outright.
        theme, _ = self._write(previews=True)
        wf = (theme / ".github" / "workflows" / "readme-previews.yml").read_text()
        self.assertIn("cp shots/*.png .", wf)
        self.assertNotIn("shots/full/", wf)
        self.assertNotIn("__FXCSS_", wf)
        self.assertIn("FXCSS_VERSION: 9.9.9", wf)

    def test_next_steps_explains_previews_only_when_asked(self):
        # "previews" alone is no signal here: the default text already talks
        # about the ci-previews branch and carries a "theme previews" badge.
        from fxcss.scaffold import next_steps
        off = next_steps([], [], [], False, False)
        self.assertNotIn("readme-previews", off)
        self.assertNotIn("raw.githubusercontent.com", off)
        on = next_steps([], [], [], False, False, True)
        self.assertIn("readme-previews", on)
        self.assertIn("raw.githubusercontent.com", on)


class ProfileKindTests(unittest.TestCase):
    def test_channel_suffixes(self):
        from fxcss.install import profile_kind
        self.assertEqual(profile_kind("8f2b1a.default-release"), "Release")
        self.assertEqual(profile_kind("8f2b1a.dev-edition-default"),
                         "Developer Edition")
        self.assertEqual(profile_kind("8f2b1a.default-esr"), "ESR")
        # longest match wins: default-esr must not be read as default
        self.assertNotEqual(profile_kind("x.default-esr"),
                            profile_kind("x.default"))

    def test_unknown_names_say_nothing(self):
        # A profile someone named themselves gets no label rather than a
        # guessed one -- this is shown next to "about to overwrite chrome/".
        from fxcss.install import profile_kind
        self.assertEqual(profile_kind("work"), "")
        self.assertEqual(profile_kind(""), "")
        self.assertEqual(profile_kind(None), "")

    def test_packaging_from_root(self):
        from fxcss.install import profile_kind
        self.assertEqual(
            profile_kind("a.default-release",
                         "/home/u/snap/firefox/common/.mozilla/firefox"),
            "Release, snap")
        self.assertEqual(
            profile_kind("work", "/home/u/.var/app/org.mozilla.firefox/x"),
            "flatpak")


class RefOptionsTests(unittest.TestCase):
    RELEASE = {"tag": "v2.0", "date": "2025-01-03T00:00:00Z"}

    def _info(self, release=None, commit=None):
        return {"default_branch": "master", "release": release,
                "commit": commit, "owner": "o", "name": "r"}

    def test_commit_is_newer_needs_both_sides(self):
        from fxcss.fetch import commit_is_newer
        newer = {"sha": "abc", "date": "2026-08-01T00:00:00Z", "message": "m"}
        self.assertTrue(commit_is_newer(self._info(self.RELEASE, newer)))
        older = {"sha": "abc", "date": "2024-01-01T00:00:00Z", "message": "m"}
        self.assertFalse(commit_is_newer(self._info(self.RELEASE, older)))
        self.assertFalse(commit_is_newer(self._info(None, newer)))
        self.assertFalse(commit_is_newer(self._info(self.RELEASE, None)))

    def test_release_is_offered_first(self):
        from fxcss.fetch import ref_options
        commit = {"sha": "abc", "date": "2026-08-01T00:00:00Z", "message": "fix"}
        options = ref_options(self._info(self.RELEASE, commit))
        self.assertEqual([o["ref"] for o in options], ["v2.0", "master"])
        self.assertIn("newer than the release", options[1]["note"])

    def test_no_release_still_offers_the_branch(self):
        from fxcss.fetch import ref_options
        options = ref_options(self._info(None, None))
        self.assertEqual([o["ref"] for o in options], ["master"])


class SelectionTests(unittest.TestCase):
    def test_forms_accepted(self):
        from fxcss.cli import parse_selection
        self.assertEqual(parse_selection("1,3", 4), [0, 2])
        self.assertEqual(parse_selection("1 3", 4), [0, 2])
        self.assertEqual(parse_selection("all", 3), [0, 1, 2])
        self.assertEqual(parse_selection("", 3), [])
        self.assertEqual(parse_selection(None, 3), [])

    def test_bad_input_under_selects(self):
        # Out of range and unreadable tokens are dropped: this writes files
        # into a real profile, so a typo must never add something.
        from fxcss.cli import parse_selection
        self.assertEqual(parse_selection("9", 3), [])
        self.assertEqual(parse_selection("2,nonsense,99", 3), [1])
        self.assertEqual(parse_selection("2,2,1", 3), [0, 1])


class CompletionTests(unittest.TestCase):
    def _theme(self, td):
        theme = Path(td)
        (theme / "chrome").mkdir()
        (theme / "custom").mkdir()
        for name in ("compact-tabs", "theme-nord", "theme-dracula"):
            (theme / "custom" / f"{name}.css").write_text("x{}")
        return theme

    def test_subcommands_and_flags(self):
        from fxcss.complete import candidates, subcommands
        self.assertIn("install", subcommands())
        self.assertNotIn("__complete", subcommands())
        # a prefix shared by two commands offers both, and the shell decides
        self.assertEqual(candidates(["fxcss", "ins"], 1), ["inspect", "install"])
        self.assertEqual(candidates(["fxcss", "inst"], 1), ["install"])
        flags = candidates(["fxcss", "install", "--w"], 2)
        self.assertEqual(flags, ["--with"])

    def test_sheet_values_come_from_the_theme(self):
        from fxcss.complete import candidates
        with tempfile.TemporaryDirectory() as td:
            theme = self._theme(td)
            got = candidates(["fxcss", "install", str(theme), "--with", "theme-"], 4)
            self.assertEqual(got, ["theme-dracula", "theme-nord"])

    def test_sheet_values_are_comma_aware(self):
        from fxcss.complete import candidates
        with tempfile.TemporaryDirectory() as td:
            theme = self._theme(td)
            got = candidates(
                ["fxcss", "install", str(theme), "--with", "theme-nord,comp"], 4)
            # carries the part already typed, and does not re-offer the choice
            self.assertEqual(got, ["theme-nord,compact-tabs"])
            got = candidates(
                ["fxcss", "install", str(theme), "--with", "theme-nord,"], 4)
            self.assertNotIn("theme-nord,theme-nord", got)

    def test_path_options_defer_to_the_shell(self):
        from fxcss.complete import candidates
        self.assertEqual(candidates(["fxcss", "shot", "--out", ""], 3), [])

    def test_never_raises(self):
        from fxcss.complete import complete_line
        for words, cword in ((["fxcss"], 99), ([], 0), (["fxcss", "--with"], 1)):
            self.assertIsInstance(complete_line(words, cword), list)

    def test_every_shell_has_a_script(self):
        from fxcss.complete import SHELLS, script
        for shell in SHELLS:
            self.assertIn("fxcss __complete", script(shell))
        self.assertIsNone(script("csh"))


class FocusCropTests(unittest.TestCase):
    """Crops must show what an option did, not the window it did it in."""

    def _mask(self, size, boxes):
        from PIL import Image, ImageDraw
        mask = Image.new("L", size, 0)
        draw = ImageDraw.Draw(mask)
        for box in boxes:
            draw.rectangle(box, fill=255)
        return mask

    def test_clusters_split_on_distance_and_merge_when_adjacent(self):
        from fxcss.tweaks import _clusters
        far = self._mask((400, 200), [(10, 10, 20, 20), (300, 150, 310, 160)])
        self.assertEqual(len(_clusters(far)), 2)
        touching = self._mask((400, 200), [(10, 10, 20, 20), (21, 10, 30, 20)])
        self.assertEqual(len(_clusters(touching)), 1)

    def test_repeated_change_crops_to_one_instance(self):
        # The tab-close-swap case: the same change on every tab. Cropping to
        # the union is the whole strip, which is what made these previews
        # useless -- the crop has to be a fraction of it.
        from fxcss.tweaks import focus_box
        size = (960, 360)
        boxes = [(x, 70, x + 10, 80) for x in range(120, 900, 150)]
        mask = self._mask(size, boxes)
        union = mask.getbbox()
        box = focus_box(mask, size)
        union_width = union[2] - union[0]
        crop_width = box[2] - box[0]
        self.assertLess(crop_width, union_width / 2)
        self.assertLessEqual(crop_width, size[0] * 0.62 + 1)

    def test_tight_change_is_padded_out_for_context(self):
        from fxcss.tweaks import FOCUS_MIN, focus_box
        size = (960, 360)
        box = focus_box(self._mask(size, [(500, 100, 516, 116)]), size)
        self.assertGreaterEqual(box[2] - box[0], FOCUS_MIN[0])
        self.assertGreaterEqual(box[3] - box[1], FOCUS_MIN[1])

    def test_box_stays_inside_the_image(self):
        from fxcss.tweaks import focus_box
        size = (960, 360)
        for spot in ((0, 0, 8, 8), (952, 352, 960, 360)):
            box = focus_box(self._mask(size, [spot]), size)
            self.assertGreaterEqual(box[0], 0)
            self.assertGreaterEqual(box[1], 0)
            self.assertLessEqual(box[2], size[0])
            self.assertLessEqual(box[3], size[1])

    def test_no_change_has_no_focus(self):
        from fxcss.tweaks import focus_box
        self.assertIsNone(focus_box(self._mask((100, 100), []), (100, 100)))

    def test_panels_scale_up_as_well_as_down(self):
        # The old code only shrank, so a correctly cropped 16px button still
        # reached the README at 16px.
        from PIL import Image
        from fxcss.tweaks import _fit
        self.assertEqual(_fit(Image.new("RGB", (100, 50)), 400).width, 300)
        self.assertEqual(_fit(Image.new("RGB", (900, 100)), 400).width, 400)
        self.assertEqual(_fit(Image.new("RGB", (400, 100)), 400).width, 400)


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
            "fxcss.fetch, fxcss.scaffold, fxcss.install, fxcss.omni; "
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
            # Both fields name a directory uninstall may move over chrome/,
            # so both have to be refused.
            data["backup"] = "../precious"
            data["origin_backup"] = "../precious"
            manifest.write_text(json.dumps(data), encoding="utf-8")
            summary = uninstall_theme(profile)
            # the outside directory stays put, nothing is "restored" from it
            self.assertTrue((precious / "keep.txt").is_file())
            self.assertIsNone(summary["restored"])

    def test_a_doctored_backup_cannot_displace_a_sound_origin(self):
        """Tampering with one field does not get to veto the other.

        `origin_backup` is what uninstall restores; a `backup` edited to point
        somewhere else must be ignored rather than allowed to abort the
        restore and strand the user's own chrome/ in a backup directory.
        """
        import json
        from fxcss.install import uninstall_theme
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = self.installed(root)
            manifest = profile / "chrome" / "fxcss-install.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["backup"] = "/etc"
            manifest.write_text(json.dumps(data), encoding="utf-8")
            summary = uninstall_theme(profile)
            self.assertEqual(summary["restored"],
                             "chrome.backup-20260814120000")
            self.assertEqual(
                (profile / "chrome" / "userChrome.css").read_text(),
                "/* the user's own */\n")

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


class ParseThemeIdTests(unittest.TestCase):
    """A schema-1 manifest's only record of the theme is one string.

    Taking it apart is what lets an old install still be described, so the
    shapes it can hold are pinned here. What the string cannot say -- whether
    the ref was a tag or a branch -- must come back as "unknown" rather than
    as a plausible default.
    """

    def test_owner_name_ref(self):
        from fxcss.install import parse_theme_id
        got = parse_theme_id("AdamXweb/WhiteSurFirefoxThemeMacOS@v2.0.0")
        self.assertEqual(got["kind"], "github")
        self.assertEqual(got["owner"], "AdamXweb")
        self.assertEqual(got["name"], "WhiteSurFirefoxThemeMacOS")
        self.assertEqual(got["ref"], "v2.0.0")
        self.assertEqual(got["ref_kind"], "unknown")

    def test_a_ref_may_hold_slashes(self):
        from fxcss.install import parse_theme_id
        got = parse_theme_id("o/n@release/2026-08")
        self.assertEqual((got["kind"], got["ref"]), ("github", "release/2026-08"))

    def test_absolute_path_is_a_local_install(self):
        from fxcss.install import parse_theme_id
        got = parse_theme_id("/Users/me/themes/WhiteSur")
        self.assertEqual(got["kind"], "local")
        self.assertEqual(got["path"], "/Users/me/themes/WhiteSur")

    def test_unreadable_stays_unknown(self):
        from fxcss.install import parse_theme_id
        for text in ("", None, "just-a-name", "o/n"):
            self.assertEqual(parse_theme_id(text)["kind"], "unknown", text)


class ManifestNormalisationTests(unittest.TestCase):
    """Old and tampered manifests both have to come back in one shape."""

    def _write(self, profile, data):
        (profile / "chrome").mkdir(parents=True, exist_ok=True)
        (profile / "chrome" / "fxcss-install.json").write_text(
            json.dumps(data), encoding="utf-8")

    def test_schema_1_is_filled_out_in_memory(self):
        from fxcss.install import read_manifest
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td)
            raw = {"theme": "o/n@v1", "installed": "2026-01-01 00:00:00",
                   "backup": "chrome.backup-1", "user_js_created": False,
                   "sheets": ["compact-tabs"], "files": ["chrome/a.css"]}
            self._write(profile, raw)
            got = read_manifest(profile)
            self.assertEqual(got["schema"], 1)
            self.assertEqual(got["source"]["owner"], "o")
            self.assertEqual(got["origin_backup"], "chrome.backup-1")
            self.assertEqual(got["digests"], {})
            # and the file itself is left exactly as it was found
            on_disk = json.loads(
                (profile / "chrome" / "fxcss-install.json").read_text())
            self.assertEqual(on_disk, raw)

    def test_wrong_types_degrade_rather_than_crash(self):
        from fxcss.install import read_manifest
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td)
            self._write(profile, {"theme": "o/n@v1", "schema": "two",
                                  "source": "not-a-dict", "files": "nope",
                                  "sheets": 7, "digests": ["no"]})
            got = read_manifest(profile)
            self.assertEqual(got["schema"], 1)
            self.assertEqual(got["source"]["kind"], "github")
            self.assertEqual((got["files"], got["sheets"], got["digests"]),
                             ([], [], {}))

    def test_a_list_at_the_top_level_is_not_a_manifest(self):
        from fxcss.install import read_manifest
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td)
            (profile / "chrome").mkdir()
            (profile / "chrome" / "fxcss-install.json").write_text(
                "[1, 2, 3]", encoding="utf-8")
            self.assertIsNone(read_manifest(profile))


class DriftTests(unittest.TestCase):
    """What changed in chrome/ after the install that recorded it."""

    def installed(self, root):
        from fxcss.install import install_theme
        theme = _make_theme(root)
        profile = _make_profile(root)
        install_theme(theme, profile, "o/n@v1", stamp="20260814120000")
        return profile

    def test_a_clean_install_has_not_drifted(self):
        from fxcss.install import drift
        with tempfile.TemporaryDirectory() as td:
            got = drift(self.installed(Path(td)))
            self.assertEqual((got["modified"], got["missing"], got["extra"]),
                             ([], [], []))
            self.assertGreater(got["checked"], 0)

    def test_edits_additions_and_deletions_are_each_named(self):
        from fxcss.install import drift
        with tempfile.TemporaryDirectory() as td:
            profile = self.installed(Path(td))
            (profile / "chrome" / "userChrome.css").write_text(
                "/* edited by hand */\n", encoding="utf-8")
            (profile / "chrome" / "mine.css").write_text("#a{}\n",
                                                         encoding="utf-8")
            (profile / "chrome" / "customChrome.css").unlink()
            got = drift(profile)
            self.assertEqual(got["modified"], ["chrome/userChrome.css"])
            self.assertEqual(got["missing"], ["chrome/customChrome.css"])
            self.assertEqual(got["extra"], ["chrome/mine.css"])

    def test_the_manifest_itself_is_not_an_addition(self):
        from fxcss.install import drift
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(drift(self.installed(Path(td)))["extra"], [])

    def test_without_digests_nothing_is_claimed(self):
        """Schema 1 recorded no hashes, so "unchanged" is not knowable.

        The danger is the opposite answer: reporting a hand-edited profile as
        clean would let an upgrade overwrite work without warning.
        """
        from fxcss.install import drift
        with tempfile.TemporaryDirectory() as td:
            profile = self.installed(Path(td))
            path = profile / "chrome" / "fxcss-install.json"
            data = json.loads(path.read_text())
            del data["digests"]
            path.write_text(json.dumps(data), encoding="utf-8")
            (profile / "chrome" / "userChrome.css").write_text(
                "/* edited */\n", encoding="utf-8")
            got = drift(profile)
            self.assertEqual(got["checked"], 0)
            self.assertEqual(got["modified"], [])

    def test_no_manifest_means_no_answer(self):
        from fxcss.install import drift
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(drift(Path(td))["checked"], 0)


class SafeBackupNameTests(unittest.TestCase):
    """The manifest names a directory that gets moved over chrome/."""

    def test_a_plain_backup_name_is_allowed(self):
        from fxcss.install import safe_backup_name
        self.assertEqual(safe_backup_name("chrome.backup-20260814120000"),
                         "chrome.backup-20260814120000")

    def test_traversal_absolute_and_lookalikes_are_refused(self):
        from fxcss.install import safe_backup_name
        for bad in ("../chrome.backup-1", "/tmp/chrome.backup-1",
                    "chrome.backup-1/../..", "prefs.js", "chrome", "", None,
                    12, "chrome.removed-1"):
            self.assertIsNone(safe_backup_name(bad), bad)


class OriginBackupTests(unittest.TestCase):
    """After an upgrade, `uninstall` must still reach the *original* chrome/.

    Upgrades move each previous install aside, so the newest chrome.backup-*
    holds the last version of the theme rather than what the user had before
    fxcss arrived. Restoring the newest would hand someone an old copy of the
    theme and call it their own files back.
    """

    def test_uninstall_restores_the_origin_not_the_newest(self):
        from fxcss.install import install_theme, uninstall_theme
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            theme = _make_theme(root)
            profile = _make_profile(root)
            first = install_theme(theme, profile, "o/n@v1",
                                  stamp="20260814120000")
            origin = first["backup"]
            # what an upgrade does: keep the origin, back the theme up again
            install_theme(theme, profile, "o/n@v2", stamp="20260815120000",
                          origin_backup=origin)
            summary = uninstall_theme(profile)
            self.assertEqual(summary["restored"], origin)
            self.assertEqual(
                (profile / "chrome" / "userChrome.css").read_text(),
                "/* the user's own */\n")

    def test_a_first_install_is_its_own_origin(self):
        from fxcss.install import install_theme
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = install_theme(_make_theme(root), _make_profile(root),
                                   "o/n@v1", stamp="20260814120000")
            self.assertEqual(result["origin_backup"], result["backup"])

    def test_a_profile_with_no_chrome_has_no_origin(self):
        from fxcss.install import install_theme
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "clean"
            profile.mkdir()
            (profile / "prefs.js").write_text("", encoding="utf-8")
            result = install_theme(_make_theme(root), profile, "o/n@v1")
            self.assertIsNone(result["origin_backup"])


class UpdateStateTests(unittest.TestCase):
    """Five outcomes, because "cannot tell" is not "up to date"."""

    RELEASED = {"release": {"tag": "v2.0.0", "date": "2026-08-16"},
                "default_branch": "main",
                "commit": {"sha": "abc1234", "message": "tweak the tabs"}}

    def test_a_tracked_release_that_moved_on(self):
        from fxcss.fetch import update_state
        got = update_state({"kind": "github", "ref_kind": "release",
                            "ref": "v1.0.0"}, self.RELEASED)
        self.assertEqual((got["state"], got["ref"]), ("available", "v2.0.0"))

    def test_a_tracked_release_at_the_newest_tag(self):
        from fxcss.fetch import update_state
        got = update_state({"kind": "github", "ref_kind": "release",
                            "ref": "v2.0.0"}, self.RELEASED)
        self.assertEqual(got["state"], "current")

    def test_a_branch_compares_the_commit_not_the_name(self):
        from fxcss.fetch import update_state
        source = {"kind": "github", "ref_kind": "branch", "ref": "main",
                  "resolved": "abc1234"}
        self.assertEqual(update_state(source, self.RELEASED)["state"],
                         "current")
        moved = dict(source, resolved="0000000")
        self.assertEqual(update_state(moved, self.RELEASED)["state"],
                         "available")

    def test_an_explicit_ref_is_pinned_not_behind(self):
        from fxcss.fetch import update_state
        got = update_state({"kind": "github", "ref_kind": "explicit",
                            "ref": "v1.0.0"}, self.RELEASED)
        self.assertEqual(got["state"], "pinned")

    def test_an_unrecorded_ref_kind_is_not_reported_as_current(self):
        from fxcss.fetch import update_state
        got = update_state({"kind": "github", "ref_kind": "unknown",
                            "ref": "v2.0.0"}, self.RELEASED)
        self.assertEqual(got["state"], "unknown")

    def test_local_and_missing_sources(self):
        from fxcss.fetch import update_state
        self.assertEqual(
            update_state({"kind": "local", "path": "/x"}, {})["state"],
            "unsupported")
        self.assertEqual(update_state({}, {})["state"], "unknown")
        self.assertEqual(update_state(None, {})["state"], "unknown")

    def test_a_theme_with_no_releases_is_not_current(self):
        from fxcss.fetch import update_state
        got = update_state({"kind": "github", "ref_kind": "release",
                            "ref": "v1.0.0"}, {"release": None})
        self.assertEqual(got["state"], "unknown")


class InstallSourceTests(unittest.TestCase):
    """`ref_kind` is settled where it is known, and never inferred later."""

    INFO = {"release": {"tag": "v2.0.0"}, "default_branch": "main",
            "commit": {"sha": "abc1234"}}

    def test_a_release_tag_is_recorded_as_a_release(self):
        from fxcss.cli import _install_source
        got = _install_source("o", "n", "v2.0.0", self.INFO, explicit=False)
        self.assertEqual(got["ref_kind"], "release")
        self.assertEqual(got["resolved"], "v2.0.0")

    def test_a_branch_resolves_to_the_commit_it_pointed_at(self):
        from fxcss.cli import _install_source
        got = _install_source("o", "n", "main", self.INFO, explicit=False)
        self.assertEqual(got["ref_kind"], "branch")
        self.assertEqual(got["resolved"], "abc1234")

    def test_an_explicit_ref_says_so_without_a_lookup(self):
        from fxcss.cli import _install_source
        got = _install_source("o", "n", "v1.0.0", None, explicit=True)
        self.assertEqual(got["ref_kind"], "explicit")


class BackupOrderTests(unittest.TestCase):
    """Which backup "the newest" means decides what a rollback restores."""

    def test_spare_copies_sort_numerically_not_as_text(self):
        from fxcss.install import _backup_key
        names = ["chrome.backup-20260814120000",
                 "chrome.backup-20260814120000-2",
                 "chrome.backup-20260814120000-10",
                 "chrome.backup-20260813120000"]
        self.assertEqual(sorted(names, key=_backup_key), [
            "chrome.backup-20260813120000",
            "chrome.backup-20260814120000",
            "chrome.backup-20260814120000-2",
            "chrome.backup-20260814120000-10",
        ])


class ListBackupsTests(unittest.TestCase):
    """A backup can say what it holds, because the manifest travels with it."""

    def test_backups_describe_themselves_and_the_original_does_not(self):
        from fxcss.install import install_theme, list_backups
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            theme = _make_theme(root)
            profile = _make_profile(root)
            first = install_theme(theme, profile, "o/n@v1",
                                  stamp="20260814120000")
            install_theme(theme, profile, "o/n@v2", stamp="20260815120000",
                          origin_backup=first["backup"])
            backups = list_backups(profile)
            self.assertEqual([b["theme"] for b in backups], ["o/n@v1", None])
            self.assertEqual(backups[-1]["name"], first["backup"])

    def test_stray_directories_are_not_offered_as_backups(self):
        from fxcss.install import list_backups
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td)
            (profile / "chrome.backup-20260814120000").mkdir()
            (profile / "chrome.removed-20260814120000").mkdir()
            (profile / "chrome.backup-notes.txt").write_text("x")
            names = [b["name"] for b in list_backups(profile)]
            self.assertEqual(names, ["chrome.backup-20260814120000"])


class RollbackTests(unittest.TestCase):

    def two_versions(self, root):
        from fxcss.install import install_theme
        theme = _make_theme(root)
        profile = _make_profile(root)
        first = install_theme(theme, profile, "o/n@v1",
                              stamp="20260814120000")
        second = install_theme(theme, profile, "o/n@v2",
                               stamp="20260815120000",
                               origin_backup=first["backup"])
        return profile, first, second

    def test_rollback_restores_the_previous_version(self):
        from fxcss.install import read_manifest, rollback_to
        with tempfile.TemporaryDirectory() as td:
            profile, _, second = self.two_versions(Path(td))
            summary = rollback_to(profile, second["backup"],
                                  stamp="20260816120000")
            self.assertEqual(summary["restored"], second["backup"])
            self.assertEqual(read_manifest(profile)["theme"], "o/n@v1")

    def test_what_was_installed_becomes_a_backup_so_it_is_undoable(self):
        from fxcss.install import read_manifest, rollback_to
        with tempfile.TemporaryDirectory() as td:
            profile, _, second = self.two_versions(Path(td))
            summary = rollback_to(profile, second["backup"],
                                  stamp="20260816120000")
            forward = summary["moved_aside"]
            self.assertTrue((profile / forward).is_dir())
            rollback_to(profile, forward, stamp="20260817120000")
            self.assertEqual(read_manifest(profile)["theme"], "o/n@v2")

    def test_the_origin_carries_through_a_rollback(self):
        """Rolling back must not strand the user's own files.

        The restored manifest is the older one, which already names the
        origin; the risk is a rollback that rewrites or drops it.
        """
        from fxcss.install import read_manifest, rollback_to, uninstall_theme
        with tempfile.TemporaryDirectory() as td:
            profile, first, second = self.two_versions(Path(td))
            rollback_to(profile, second["backup"], stamp="20260816120000")
            self.assertEqual(read_manifest(profile)["origin_backup"],
                             first["backup"])
            uninstall_theme(profile)
            self.assertEqual(
                (profile / "chrome" / "userChrome.css").read_text(),
                "/* the user's own */\n")

    def test_rolling_back_to_the_original_takes_the_prefs_with_it(self):
        from fxcss.install import install_theme, rollback_to
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            theme = _make_theme(root)
            profile = _make_profile(root)
            result = install_theme(theme, profile, "o/n@v1",
                                   stamp="20260814120000")
            summary = rollback_to(profile, result["backup"],
                                  stamp="20260815120000")
            self.assertEqual(summary["user_js"], "removed")
            # the profile's own pref survives; only the fxcss block goes
            self.assertEqual((profile / "user.js").read_text(),
                             'user_pref("mine", 1);\n')
            self.assertEqual(
                (profile / "chrome" / "userChrome.css").read_text(),
                "/* the user's own */\n")

    def test_a_version_that_recorded_no_prefs_is_reported_not_invented(self):
        from fxcss.install import rollback_to
        with tempfile.TemporaryDirectory() as td:
            profile, _, second = self.two_versions(Path(td))
            stale = profile / second["backup"] / "fxcss-install.json"
            data = json.loads(stale.read_text())
            del data["user_js_block"]
            stale.write_text(json.dumps(data), encoding="utf-8")
            before = (profile / "user.js").read_text()
            summary = rollback_to(profile, second["backup"],
                                  stamp="20260816120000")
            self.assertEqual(summary["user_js"], "unknown")
            self.assertEqual((profile / "user.js").read_text(), before)

    def test_a_backup_outside_the_profile_is_refused(self):
        from fxcss.install import rollback_to
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile, _, _ = self.two_versions(root)
            for bad in ("../elsewhere", "/etc", "chrome", "chrome.removed-1"):
                with self.assertRaises(RuntimeError):
                    rollback_to(profile, bad)
            self.assertTrue((profile / "chrome" / "userChrome.css").is_file())


class PruneBackupsTests(unittest.TestCase):

    def _stack(self, profile, count):
        for i in range(count):
            (profile / f"chrome.backup-2026081412000{i}").mkdir(parents=True)

    def test_keeps_the_newest_and_never_the_protected_one(self):
        from fxcss.install import prune_backups
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td)
            self._stack(profile, 5)
            origin = "chrome.backup-20260814120000"
            removed = prune_backups(profile, keep=2, protect=[origin])
            left = sorted(p.name for p in profile.glob("chrome.backup-*"))
            self.assertIn(origin, left)
            self.assertEqual(left, [origin,
                                    "chrome.backup-20260814120003",
                                    "chrome.backup-20260814120004"])
            self.assertEqual(len(removed), 2)

    def test_the_protected_backup_does_not_use_up_the_allowance(self):
        """`--keep 2` means two besides the original, not one plus it."""
        from fxcss.install import prune_backups
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td)
            self._stack(profile, 4)
            prune_backups(profile, keep=2,
                          protect=["chrome.backup-20260814120000"])
            self.assertEqual(len(list(profile.glob("chrome.backup-*"))), 3)

    def test_none_prunes_nothing(self):
        from fxcss.install import prune_backups
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td)
            self._stack(profile, 4)
            self.assertEqual(prune_backups(profile, keep=None), [])
            self.assertEqual(len(list(profile.glob("chrome.backup-*"))), 4)


class SheetContinuityTests(unittest.TestCase):
    """An option that vanishes between versions must not vanish quietly."""

    def variants(self, *names):
        return [Path("custom") / f"{name}.css" for name in names]

    def test_a_renamed_sheet_is_reported_missing(self):
        from fxcss.fetch import sheet_continuity
        got = sheet_continuity(["theme-nord", "compact-tabs"],
                               self.variants("compact-tabs", "theme-nordic"))
        self.assertEqual(got["missing"], ["theme-nord"])
        self.assertEqual([s.stem for s in got["kept"]], ["compact-tabs"])

    def test_matching_ignores_case_and_survives_no_sheets(self):
        from fxcss.fetch import sheet_continuity
        got = sheet_continuity(["Compact-Tabs"], self.variants("compact-tabs"))
        self.assertEqual((got["missing"], len(got["kept"])), ([], 1))
        self.assertEqual(sheet_continuity([], self.variants("a")),
                         {"kept": [], "missing": []})
        self.assertEqual(sheet_continuity(None, []),
                         {"kept": [], "missing": []})


class DeclarationsTests(unittest.TestCase):
    """The parser only has to answer "does this sheet set that, there"."""

    def parse(self, text):
        from fxcss.sheets import declarations
        return declarations(text)

    def test_selector_lists_land_under_every_selector(self):
        got = self.parse("a, b { color: red; }")
        self.assertEqual(got, {((), "a", "color"): "red",
                               ((), "b", "color"): "red"})

    def test_comments_are_not_css_even_when_they_hold_braces(self):
        got = self.parse("/* } .fake { color: blue; */ .real { color: red }")
        self.assertEqual(got, {((), ".real", "color"): "red"})

    def test_a_media_query_is_part_of_the_address(self):
        got = self.parse("""
            :root { --x: 1px }
            @media (-moz-platform: windows) { :root { --x: 2px } }
        """)
        self.assertEqual(got[((), ":root", "--x")], "1px")
        self.assertEqual(
            got[(("@media (-moz-platform: windows)",), ":root", "--x")],
            "2px")

    def test_braces_and_semicolons_inside_strings_are_text(self):
        got = self.parse('.a { content: "}; color: blue"; color: red }')
        self.assertEqual(got, {((), ".a", "content"): '"}; color: blue"',
                               ((), ".a", "color"): "red"})

    def test_a_last_declaration_needs_no_semicolon(self):
        self.assertEqual(self.parse(".a { color: red }"),
                         {((), ".a", "color"): "red"})

    def test_top_level_at_rules_are_not_declarations(self):
        got = self.parse('@namespace url("http://x");\n'
                         '@import "other.css";\n.a { color: red }')
        self.assertEqual(got, {((), ".a", "color"): "red"})

    def test_importance_is_part_of_the_value(self):
        got = self.parse(".a { color: red !important }")
        self.assertEqual(got[((), ".a", "color")], "red !important")

    def test_whitespace_never_changes_the_answer(self):
        self.assertEqual(self.parse(".a>.b{color:red}"),
                         self.parse("  .a > .b  {\n  color :  red ;\n}\n"))


class OverlapTests(unittest.TestCase):
    """Alternatives, subsumed, overlap — and the difference between them."""

    def verdict(self, a, b):
        from fxcss.sheets import declarations, overlap, verdict
        return verdict(overlap(("a", declarations(a)), ("b", declarations(b))))

    def test_same_ground_different_values_are_alternatives(self):
        """The colour-theme case, which is what this exists for."""
        a = ":root { --bg: #111; --fg: #eee; --accent: #a00 }"
        b = ":root { --bg: #222; --fg: #ddd; --accent: #0a0 }"
        self.assertEqual(self.verdict(a, b), "alternatives")

    def test_agreeing_sheets_are_not_in_conflict(self):
        """Identical declarations compete for nothing.

        This is the case a conflicting-count metric gets right and a
        shared-count metric alone would not.
        """
        a = ":root { --bg: #111; --fg: #eee }"
        self.assertEqual(self.verdict(a, a), "")

    def test_partly_agreeing_palettes_are_still_alternatives(self):
        """Two palettes sharing a few exact colours are not thereby milder.

        Measured on WhiteSur: theme-nord and theme-dracula set the same 122
        declarations but only 87 differ, so judging by differences alone put
        them under the threshold and let both install.
        """
        a = ":root { --bg: #111; --fg: #eee; --edge: #777; --line: #777 }"
        b = ":root { --bg: #222; --fg: #ddd; --edge: #777; --line: #777 }"
        self.assertEqual(self.verdict(a, b), "alternatives")

    def test_a_small_sheet_swallowed_by_a_big_one_is_subsumed(self):
        small = ".tab { height: 28px }"
        big = (".tab { height: 40px }\n.x{a:1}\n.y{b:2}\n.z{c:3}\n"
               ".w{d:4}\n.v{e:5}")
        self.assertEqual(self.verdict(small, big), "subsumed")

    def test_sheets_that_merely_disagree_somewhere_are_overlap(self):
        a = ".tab { height: 28px }\n.a{p:1}\n.b{p:1}\n.c{p:1}\n.d{p:1}"
        b = ".tab { height: 40px }\n.e{p:1}\n.f{p:1}\n.g{p:1}\n.h{p:1}"
        self.assertEqual(self.verdict(a, b), "overlap")

    def test_disjoint_sheets_say_nothing(self):
        self.assertEqual(self.verdict(".a { color: red }",
                                      ".b { height: 10px }"), "")

    def test_the_same_property_elsewhere_is_not_a_clash(self):
        self.assertEqual(self.verdict(".a { color: red }",
                                      ".b { color: blue }"), "")

    def test_the_same_rule_under_different_conditions_is_not_a_clash(self):
        a = "@media (-moz-platform: windows) { .a { color: red } }"
        b = "@media (-moz-platform: macos) { .a { color: blue } }"
        self.assertEqual(self.verdict(a, b), "")

    def test_which_sheet_stops_mattering_is_named(self):
        from fxcss.sheets import declarations, overlap, overridden
        small = declarations(".tab { height: 28px }")
        big = declarations(".tab { height: 40px }\n.x{a:1}\n.y{b:2}\n"
                           ".z{c:3}\n.w{d:4}\n.v{e:5}")
        report = overlap(("compact", small), ("everything", big))
        self.assertEqual(overridden(report), "compact")


class ConflictsTests(unittest.TestCase):

    def _sheets(self, root, **bodies):
        paths = []
        for name, text in bodies.items():
            path = root / f"{name}.css"
            path.write_text(text, encoding="utf-8")
            paths.append(path)
        return paths

    def test_every_clashing_pair_is_found_and_the_rest_left_out(self):
        from fxcss.sheets import conflicts
        with tempfile.TemporaryDirectory() as td:
            paths = self._sheets(
                Path(td),
                nord=":root { --bg: #2e3440; --fg: #eceff4 }",
                dracula=":root { --bg: #282a36; --fg: #f8f8f2 }",
                compact=".tab { height: 28px }")
            found = conflicts(paths)
            self.assertEqual(len(found), 1)
            self.assertEqual({found[0]["a"], found[0]["b"]},
                             {"nord", "dracula"})

    def test_an_unreadable_sheet_is_not_a_crash(self):
        from fxcss.sheets import conflicts, read_declarations
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "gone.css"
            self.assertEqual(read_declarations(missing), {})
            self.assertEqual(conflicts([missing]), [])


class SelectorSpellingTests(unittest.TestCase):
    """Two sheets written by different hands must still compare."""

    def parse(self, text):
        from fxcss.sheets import declarations
        return declarations(text)

    def test_combinator_spacing_is_normalised(self):
        for spelling in (".a>.b", ".a > .b", ".a  >  .b", ".a>  .b"):
            self.assertEqual(list(self.parse(spelling + "{c:1}"))[0][1],
                             ".a > .b", spelling)

    def test_attribute_and_nth_child_operators_are_left_alone(self):
        cases = {
            '[class~="identity-color-blue"]{c:1}':
                '[class~="identity-color-blue"]',
            ".tab:nth-child(2n+1){c:1}": ".tab:nth-child(2n+1)",
            'a[href~="x"] > .b{c:1}': 'a[href~="x"] > .b',
        }
        for text, expected in cases.items():
            self.assertEqual(list(self.parse(text))[0][1], expected, text)


class BlobShaTests(unittest.TestCase):
    """Identification rests on hashing files exactly as git does."""

    def test_matches_git_hash_object(self):
        """Checked against git itself rather than a copied constant.

        Skipped where git is missing; the CI images all have it.
        """
        import shutil as sh
        import subprocess
        from fxcss.adopt import blob_sha
        if not sh.which("git"):
            self.skipTest("git not available")
        with tempfile.TemporaryDirectory() as td:
            for name, data in (("text.css", b"#nav-bar { color: red }\n"),
                               ("empty.css", b""),
                               ("binary.png", bytes(range(256)) * 4),
                               ("crlf.css", b".a {\r\n  color: red;\r\n}\r\n")):
                path = Path(td) / name
                path.write_bytes(data)
                want = subprocess.run(["git", "hash-object", str(path)],
                                      capture_output=True, text=True).stdout
                self.assertEqual(blob_sha(data), want.strip(), name)

    def test_crlf_files_offer_an_lf_reading_and_others_do_not(self):
        from fxcss.adopt import blob_sha, file_blob_sha
        with tempfile.TemporaryDirectory() as td:
            crlf = Path(td) / "a.css"
            crlf.write_bytes(b".a {\r\n  color: red;\r\n}\r\n")
            raw, as_lf = file_blob_sha(crlf)
            self.assertEqual(as_lf, blob_sha(b".a {\n  color: red;\n}\n"))
            self.assertNotEqual(raw, as_lf)

            plain = Path(td) / "b.css"
            plain.write_bytes(b".b { color: red }\n")
            self.assertIsNone(file_blob_sha(plain)[1])

            binary = Path(td) / "c.png"
            binary.write_bytes(b"\x89PNG\0\r\n\x1a\n" + bytes(200))
            self.assertIsNone(file_blob_sha(binary)[1])


class CandidateRepoTests(unittest.TestCase):
    """Where a chrome/ folder might say it came from."""

    def test_a_clone_names_its_own_origin(self):
        from fxcss.adopt import candidates
        with tempfile.TemporaryDirectory() as td:
            chrome = Path(td) / "chrome"
            (chrome / ".git").mkdir(parents=True)
            (chrome / ".git" / "config").write_text(
                '[remote "origin"]\n'
                '\turl = https://github.com/owner/theme.git\n',
                encoding="utf-8")
            found = candidates(chrome)
            self.assertEqual(found[0]["repo"], "owner/theme")
            self.assertTrue(found[0]["certain"])

    def test_an_ssh_remote_reads_the_same(self):
        from fxcss.adopt import git_config_remote
        with tempfile.TemporaryDirectory() as td:
            chrome = Path(td) / "chrome"
            (chrome / ".git").mkdir(parents=True)
            (chrome / ".git" / "config").write_text(
                '[remote "origin"]\n\turl = git@github.com:owner/theme.git\n',
                encoding="utf-8")
            self.assertEqual(git_config_remote(chrome), "owner/theme")

    def test_urls_in_the_theme_are_hints_not_certainties(self):
        from fxcss.adopt import candidates
        with tempfile.TemporaryDirectory() as td:
            chrome = Path(td) / "chrome"
            chrome.mkdir()
            (chrome / "userChrome.css").write_text(
                "/* from https://github.com/owner/theme, with palette\n"
                "   ideas from https://github.com/someone/else */\n",
                encoding="utf-8")
            found = candidates(chrome)
            self.assertEqual([c["repo"] for c in found],
                             ["owner/theme", "someone/else"])
            self.assertFalse(any(c["certain"] for c in found))

    def test_a_theme_that_says_nothing_offers_nothing(self):
        """WhiteSur's installed chrome/ carries no URL at all — measured."""
        from fxcss.adopt import candidates
        with tempfile.TemporaryDirectory() as td:
            chrome = Path(td) / "chrome"
            chrome.mkdir()
            (chrome / "userChrome.css").write_text("#nav-bar { }\n",
                                                   encoding="utf-8")
            self.assertEqual(candidates(chrome), [])


class CompareTreeTests(unittest.TestCase):
    """Matching a profile's files against one version of a repository."""

    def build(self, root, **files):
        chrome = root / "chrome"
        for name, text in files.items():
            path = chrome / name.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return chrome

    def tree_of(self, prefix, **files):
        from fxcss.adopt import blob_sha
        return {f"{prefix}{name.replace('__', '/')}":
                blob_sha(text.encode()) for name, text in files.items()}

    def test_an_identical_tree_is_an_exact_match(self):
        from fxcss.adopt import compare, exact
        files = {"userChrome.css": "#nav-bar { }\n", "parts__tabs.css": ".t{}\n"}
        with tempfile.TemporaryDirectory() as td:
            chrome = self.build(Path(td), **files)
            result = compare(chrome, self.tree_of("chrome/", **files))
            self.assertTrue(exact(result))
            self.assertEqual((result["matched"], result["files"]), (2, 2))

    def test_edited_missing_and_added_files_are_each_named(self):
        from fxcss.adopt import compare, exact
        with tempfile.TemporaryDirectory() as td:
            chrome = self.build(Path(td), **{"userChrome.css": "#nav-bar { }\n",
                                             "mine.css": ".mine{}\n"})
            tree = self.tree_of("chrome/", **{"userChrome.css": "#nav-bar { color: red }\n",
                                              "gone.css": ".g{}\n"})
            result = compare(chrome, tree)
            self.assertFalse(exact(result))
            self.assertEqual(result["differing"], ["userChrome.css"])
            self.assertEqual(result["missing"], ["gone.css"])
            self.assertEqual(result["extra"], ["mine.css"])

    def test_a_theme_kept_under_src_is_still_found(self):
        from fxcss.adopt import compare, exact
        files = {"userChrome.css": "#nav-bar { }\n"}
        with tempfile.TemporaryDirectory() as td:
            chrome = self.build(Path(td), **files)
            result = compare(chrome, self.tree_of("src/chrome/", **files))
            self.assertTrue(exact(result))

    def test_files_added_by_hand_do_not_spoil_an_exact_match(self):
        """Scored against the version's files, not the profile's.

        Someone who dropped an extra sheet into chrome/ still has an
        unmodified copy of the release, and pinning the version is the point.
        """
        from fxcss.adopt import compare, exact
        files = {"userChrome.css": "#nav-bar { }\n"}
        with tempfile.TemporaryDirectory() as td:
            chrome = self.build(Path(td), **files)
            (chrome / "extra.css").write_text(".x{}\n", encoding="utf-8")
            result = compare(chrome, self.tree_of("chrome/", **files))
            self.assertTrue(exact(result))
            self.assertEqual(result["extra"], ["extra.css"])

    def test_line_endings_alone_still_match_and_say_so(self):
        from fxcss.adopt import compare, exact
        with tempfile.TemporaryDirectory() as td:
            chrome = Path(td) / "chrome"
            chrome.mkdir()
            (chrome / "userChrome.css").write_bytes(b"#nav-bar {\r\n}\r\n")
            tree = self.tree_of("chrome/",
                                **{"userChrome.css": "#nav-bar {\n}\n"})
            result = compare(chrome, tree)
            self.assertTrue(exact(result))
            self.assertTrue(result["eol"])

    def test_a_tree_with_no_userchrome_matches_nothing(self):
        from fxcss.adopt import compare
        with tempfile.TemporaryDirectory() as td:
            chrome = self.build(Path(td), **{"userChrome.css": "#a{}\n"})
            result = compare(chrome, {"README.md": "abc"})
            self.assertEqual((result["files"], result["matched"]), (0, 0))

    def test_the_closer_of_two_versions_wins(self):
        from fxcss.adopt import better, compare
        files = {"userChrome.css": "#nav-bar { }\n", "b.css": ".b{}\n"}
        with tempfile.TemporaryDirectory() as td:
            chrome = self.build(Path(td), **files)
            near = compare(chrome, self.tree_of("chrome/", **files))
            far = compare(chrome, self.tree_of(
                "chrome/", **{"userChrome.css": "#nav-bar { }\n",
                              "b.css": ".changed{}\n"}))
            self.assertTrue(better(near, far))
            self.assertFalse(better(far, near))
            self.assertTrue(better(far, None))


class AdoptThemeTests(unittest.TestCase):
    """Recording a theme fxcss did not install."""

    def hand_installed(self, root):
        profile = root / "profile"
        (profile / "chrome").mkdir(parents=True)
        (profile / "chrome" / "userChrome.css").write_text(
            "#nav-bar { }\n", encoding="utf-8")
        (profile / "prefs.js").write_text("", encoding="utf-8")
        (profile / "user.js").write_text('user_pref("mine", 1);\n',
                                         encoding="utf-8")
        return profile

    SOURCE = {"kind": "github", "owner": "o", "name": "n", "ref": "v1",
              "ref_kind": "release", "resolved": "v1", "path": None}
    MATCH = {"ref": "v1", "matched": 1, "files": 1, "score": 1.0,
             "differing": [], "missing": [], "extra": [], "eol": False}

    def test_the_theme_is_copied_not_moved(self):
        from fxcss.install import adopt_theme
        with tempfile.TemporaryDirectory() as td:
            profile = self.hand_installed(Path(td))
            result = adopt_theme(profile, "o/n@v1", self.SOURCE, self.MATCH,
                                 stamp="20260814120000")
            self.assertEqual(
                (profile / "chrome" / "userChrome.css").read_text(),
                "#nav-bar { }\n")
            self.assertTrue(
                (profile / result["backup"] / "userChrome.css").is_file())
            self.assertEqual(result["origin_backup"], result["backup"])

    def test_user_js_is_left_exactly_as_it_was(self):
        from fxcss.install import adopt_theme
        with tempfile.TemporaryDirectory() as td:
            profile = self.hand_installed(Path(td))
            adopt_theme(profile, "o/n@v1", self.SOURCE, self.MATCH)
            self.assertEqual((profile / "user.js").read_text(),
                             'user_pref("mine", 1);\n')

    def test_adopting_twice_is_refused(self):
        from fxcss.install import adopt_theme
        with tempfile.TemporaryDirectory() as td:
            profile = self.hand_installed(Path(td))
            adopt_theme(profile, "o/n@v1", self.SOURCE, self.MATCH)
            with self.assertRaises(RuntimeError):
                adopt_theme(profile, "o/n@v1", self.SOURCE, self.MATCH)

    def test_a_profile_with_no_theme_is_refused(self):
        from fxcss.install import adopt_theme
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td) / "empty"
            profile.mkdir()
            with self.assertRaises(RuntimeError):
                adopt_theme(profile, "o/n@v1", self.SOURCE, self.MATCH)

    def test_uninstall_puts_the_adopted_theme_back(self):
        from fxcss.install import adopt_theme, uninstall_theme
        with tempfile.TemporaryDirectory() as td:
            profile = self.hand_installed(Path(td))
            adopt_theme(profile, "o/n@v1", self.SOURCE, self.MATCH)
            uninstall_theme(profile)
            self.assertEqual(
                (profile / "chrome" / "userChrome.css").read_text(),
                "#nav-bar { }\n")

    def test_differences_found_at_adoption_count_as_local_changes(self):
        """An upgrade must not overwrite what already differed.

        Adoption records the files as they are, so `drift` sees nothing --
        the comparison against the release is the only place that knowledge
        lives, and `local_changes` is what joins the two.
        """
        from fxcss.install import adopt_theme, drift, local_changes
        with tempfile.TemporaryDirectory() as td:
            profile = self.hand_installed(Path(td))
            match = dict(self.MATCH, matched=0,
                         differing=["userChrome.css"], score=0.0)
            adopt_theme(profile, "o/n@v1", self.SOURCE, match)
            self.assertEqual(drift(profile)["modified"], [])
            self.assertEqual(local_changes(profile),
                             ["chrome/userChrome.css"])

    def test_local_changes_ignores_a_tampered_adopted_record(self):
        from fxcss.install import adopt_theme, local_changes
        with tempfile.TemporaryDirectory() as td:
            profile = self.hand_installed(Path(td))
            adopt_theme(profile, "o/n@v1", self.SOURCE, self.MATCH)
            path = profile / "chrome" / "fxcss-install.json"
            data = json.loads(path.read_text())
            data["adopted"]["differing"] = ["../../../etc/passwd", "gone.css"]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(local_changes(profile), [])


class ProfileChoiceTests(unittest.TestCase):
    """Which profile a command acts on when several exist.

    The rule that matters: a theme installed deliberately into a profile that
    is *not* the one Firefox opens must still be findable. Choosing Firefox's
    default there would report "nothing installed" -- true of that profile,
    and useless.
    """

    def fake_root(self, root, managed=(), unmanaged=(), default=None):
        import os
        names = list(managed) + list(unmanaged) + ["spare"]
        lines = []
        for i, name in enumerate(names):
            directory = root / "Profiles" / name
            directory.mkdir(parents=True)
            (directory / "prefs.js").write_text("", encoding="utf-8")
            lines.append(f"[Profile{i}]\nName={name}\nIsRelative=1\n"
                         f"Path=Profiles/{name}\n")
            if name in managed:
                chrome = directory / "chrome"
                chrome.mkdir()
                (chrome / "userChrome.css").write_text("#a{}\n",
                                                      encoding="utf-8")
                (chrome / "fxcss-install.json").write_text(
                    json.dumps({"theme": "o/n@v1", "files": []}),
                    encoding="utf-8")
            elif name in unmanaged:
                chrome = directory / "chrome"
                chrome.mkdir()
                (chrome / "userChrome.css").write_text("#a{}\n",
                                                      encoding="utf-8")
        if default:
            lines.append(f"[Install01]\nDefault=Profiles/{default}\n")
        (root / "profiles.ini").write_text("\n".join(lines), encoding="utf-8")
        os.environ["FXCSS_PROFILE_ROOTS"] = str(root)
        self.addCleanup(os.environ.pop, "FXCSS_PROFILE_ROOTS", None)

    def test_the_themed_profile_wins_over_firefoxs_default(self):
        from fxcss.cli import _choose_profile
        with tempfile.TemporaryDirectory() as td:
            self.fake_root(Path(td), managed=["dev"], default="spare")
            self.assertEqual(
                _choose_profile(None, False, prefer="managed")["name"], "dev")

    def test_adopt_looks_for_a_theme_fxcss_did_not_install(self):
        from fxcss.cli import _choose_profile
        with tempfile.TemporaryDirectory() as td:
            self.fake_root(Path(td), managed=["dev"], unmanaged=["byhand"],
                           default="spare")
            self.assertEqual(
                _choose_profile(None, False, prefer="unmanaged")["name"],
                "byhand")

    def test_an_explicit_profile_still_wins(self):
        from fxcss.cli import _choose_profile
        with tempfile.TemporaryDirectory() as td:
            self.fake_root(Path(td), managed=["dev"], default="spare")
            self.assertEqual(
                _choose_profile("spare", False, prefer="managed")["name"],
                "spare")

    def test_several_candidates_fall_back_to_firefoxs_default(self):
        from fxcss.cli import _choose_profile
        with tempfile.TemporaryDirectory() as td:
            self.fake_root(Path(td), managed=["one", "two"], default="one")
            self.assertEqual(
                _choose_profile(None, False, prefer="managed")["name"], "one")

    def test_several_candidates_and_no_default_is_an_error_not_a_guess(self):
        from fxcss.cli import _choose_profile
        with tempfile.TemporaryDirectory() as td:
            self.fake_root(Path(td), managed=["one", "two"])
            with self.assertRaises(SystemExit):
                _choose_profile(None, False, prefer="managed")

    def test_no_candidate_leaves_the_command_to_explain(self):
        """Falling back keeps the old behaviour, so the command still runs
        and reports what it wanted in its own words."""
        from fxcss.cli import _choose_profile
        with tempfile.TemporaryDirectory() as td:
            self.fake_root(Path(td), default="spare")
            self.assertEqual(
                _choose_profile(None, False, prefer="managed")["name"],
                "spare")

    def test_profile_state_tells_the_three_apart(self):
        from fxcss.cli import profile_state
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.fake_root(root, managed=["m"], unmanaged=["u"])
            states = {name: profile_state({"path": root / "Profiles" / name})
                      for name in ("m", "u", "spare")}
            self.assertEqual(states, {"m": "managed", "u": "unmanaged",
                                      "spare": "empty"})


class ModuleUrlTests(unittest.TestCase):
    """Chrome scripts must reach Firefox modules off the window, not by URL.

    Module URLs move. `resource://gre/modules/ContextualIdentityService.sys.mjs`
    became `moz-src:///toolkit/components/contextualidentity/…` in Firefox 154
    and every capture broke the day it shipped, on a line that had been correct
    for a year. `ChromeUtils.defineESModuleGetters` puts the same objects on
    the browser window under a name that does not move, so a script naming a
    URL is a dated assumption waiting for the next uplift.

    Scanning the source rather than listing the scripts by name: a new script
    with a fresh import is exactly what this needs to catch.
    """

    def source(self):
        from fxcss import core
        return Path(core.__file__).read_text(encoding="utf-8")

    def test_no_script_imports_a_module_by_url(self):
        offenders = [line.strip() for line in self.source().splitlines()
                     if "importESModule" in line and not
                     line.lstrip().startswith("#")]
        self.assertEqual(offenders, [], "reach these off the window instead")

    def test_the_scripts_that_needed_modules_still_name_them(self):
        """Guard the guard: a passing scan means nothing if the uses vanished."""
        source = self.source()
        for name in ("ContextualIdentityService", "PlacesUtils"):
            self.assertIn(name, source, name)


class TemplateActionVersionTests(unittest.TestCase):
    """The shipped templates must not fall behind fxcss's own workflows.

    Dependabot watches .github/workflows/ and bumps the actions there; it
    does not know fxcss/templates/ holds the same actions, shipped in the
    wheel for every theme that runs `fxcss init`. That is how the templates
    sat on checkout@v4 / setup-python@v5 -- the Node 20 set GitHub is
    retiring -- while fxcss's CI had moved to v7 months earlier. Pinning the
    two to each other turns the next Dependabot bump into a failing test.
    """

    # `actions/cache/restore@v5` is actions/cache: the sub-path is not a
    # separate action and is versioned with its parent.
    PIN = re.compile(r"uses:\s*actions/([a-z-]+)(?:/[a-z-]+)?@v(\d+)")

    def versions(self, paths):
        found = {}
        for path in paths:
            for name, major in self.PIN.findall(
                    path.read_text(encoding="utf-8")):
                found.setdefault(name, set()).add(int(major))
        return found

    def test_templates_match_the_workflows_fxcss_itself_runs(self):
        root = Path(__file__).resolve().parent.parent
        own = self.versions((root / ".github" / "workflows").glob("*.yml"))
        shipped = self.versions((root / "fxcss" / "templates").glob("*.yml"))
        self.assertTrue(own and shipped)
        for name, majors in shipped.items():
            self.assertEqual(len(majors), 1,
                             f"{name} pinned inconsistently in templates")
            if name in own:
                self.assertEqual(majors, own[name],
                                 f"templates pin actions/{name}@v{majors} but "
                                 f"fxcss's own CI runs v{own[name]}")

    def test_no_template_action_targets_node_20(self):
        """The majors GitHub's deprecation notice names, by action.

        Kept as a floor rather than derived: what counts as "old" is a fact
        about GitHub's runners on a date, and a test should say which date.
        Floors as of 2026-08, each read from the action's own action.yml at
        that tag: the first major declaring `using: node24`.
        """
        root = Path(__file__).resolve().parent.parent
        floors = {"checkout": 5, "setup-python": 6, "upload-artifact": 6,
                  "download-artifact": 7, "github-script": 8, "cache": 5}
        shipped = self.versions((root / "fxcss" / "templates").glob("*.yml"))
        for name, floor in floors.items():
            for major in shipped.get(name, ()):
                self.assertGreaterEqual(
                    major, floor, f"actions/{name}@v{major} runs on Node 20")


class ComboVerdictTests(unittest.TestCase):
    """`a+b` rendering identically to `b` alone proves `a` did nothing there.

    This is the pixel half of the sheet-conflict story: sheets.py sees two
    sheets fighting over the same declarations, and cannot see two sheets
    fighting over the same pixels through different rules (tabs-swapclose vs
    windows-swapclose share no declarations at all). The rendered images are
    the honest judge, with "identical" meaning the same thing it means in
    compare: zero pixels changed above the noise threshold.
    """

    def image(self, colour):
        from PIL import Image
        return Image.new("RGB", (24, 16), colour)

    def entry(self, slug):
        return {"slug": slug, "sheets": [], "percent": 0.0, "image": None}

    def test_a_combo_identical_to_one_part_names_that_part(self):
        from fxcss.tweaks import combo_verdict
        captures = {"a": self.image((200, 50, 50)),
                    "b": self.image((100, 100, 100)),
                    "a+b": self.image((100, 100, 100))}
        self.assertEqual(combo_verdict(self.entry("a+b"), captures), "b")

    def test_noise_below_the_diff_threshold_still_counts_as_identical(self):
        from fxcss.tweaks import combo_verdict
        near = self.image((100, 100, 110))       # the DiffStats noise case
        captures = {"a": self.image((200, 50, 50)),
                    "b": self.image((100, 100, 100)), "a+b": near}
        self.assertEqual(combo_verdict(self.entry("a+b"), captures), "b")

    def test_a_combo_that_is_more_than_its_parts_matches_nothing(self):
        from fxcss.tweaks import combo_verdict
        captures = {"a": self.image((200, 50, 50)),
                    "b": self.image((100, 100, 100)),
                    "a+b": self.image((50, 200, 50))}
        self.assertIsNone(combo_verdict(self.entry("a+b"), captures))

    def test_single_options_are_never_judged(self):
        from fxcss.tweaks import combo_verdict
        captures = {"a": self.image((1, 2, 3)), "b": self.image((1, 2, 3))}
        self.assertIsNone(combo_verdict(self.entry("a"), captures))

    def test_a_window_that_changed_size_is_not_compared(self):
        from PIL import Image
        from fxcss.tweaks import combo_verdict
        captures = {"a": Image.new("RGB", (10, 10), (1, 2, 3)),
                    "a+b": self.image((1, 2, 3))}
        self.assertIsNone(combo_verdict(self.entry("a+b"), captures))

    def test_the_markdown_carries_the_verdict(self):
        from fxcss.tweaks import render_markdown
        with tempfile.TemporaryDirectory() as td:
            entries = [
                {"slug": "a+b", "sheets": [], "percent": 1.0,
                 "image": None, "same_as": "b"},
                {"slug": "compact-tabs", "sheets": [], "percent": 1.0,
                 "image": None},
            ]
            text = render_markdown(Path(td), entries, [], "154.0")
            self.assertIn("renders pixel-identically to `b` alone", text)
            self.assertIn("`a` has no effect here", text)
            self.assertEqual(text.count("Not a real combination"), 1)


class OmniPackTests(unittest.TestCase):
    """Reading the shipped chrome out of omni.ja without zipfile.

    The real archives are "optimized" ZIPs whose central directory Python's
    zipfile rejects outright, so read_entries walks local file headers
    instead. A zipfile-written archive keeps conventional local headers, which
    makes it the right fixture: it proves the walk and both storage methods
    without needing a Firefox on the test machine.
    """

    def build(self, td, entries, compression):
        import zipfile
        path = Path(td) / "omni.ja"
        with zipfile.ZipFile(path, "w", compression) as zf:
            for name, text in entries:
                zf.writestr(name, text)
        return path

    def test_reads_stored_and_deflated_entries(self):
        import zipfile
        from fxcss import omni
        entries = [("chrome/a.css", ":root { color: var(--live-token); }"),
                   ("chrome/b.js", 'style.getPropertyValue("--js-token");')]
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with tempfile.TemporaryDirectory() as td:
                path = self.build(td, entries, method)
                got = dict(omni.read_entries(path))
                self.assertIn("chrome/a.css", got)
                self.assertIn(b"--live-token", got["chrome/a.css"])
                self.assertIn(b"--js-token", got["chrome/b.js"])

    def fake_firefox(self, td, entries):
        """A macOS-shaped install around a zipfile-built omni.ja."""
        import zipfile
        contents = Path(td) / "Contents"
        binary = contents / "MacOS" / "firefox"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"")
        resources = contents / "Resources"
        resources.mkdir()
        with zipfile.ZipFile(resources / "omni.ja", "w") as zf:
            for name, text in entries:
                zf.writestr(name, text)
        return binary

    def test_scan_separates_consumed_from_declared(self):
        from fxcss import omni
        with tempfile.TemporaryDirectory() as td:
            binary = self.fake_firefox(td, [
                ("chrome/global.css",
                 ":root { --declared-only: white; }\n"
                 "body { background: var(--consumed-token); }"),
                ("chrome/browser.js", 'setProperty("--script-token", v);'),
                ("chrome/commonDialog.xhtml",
                 '<dialog id="commonDialog" class="dialogBody another"/>'),
            ])
            pack = omni.scan(binary)
        self.assertIn("--consumed-token", pack["consumed"])
        self.assertIn("--script-token", pack["consumed"])
        self.assertNotIn("--declared-only", pack["consumed"])
        self.assertIn("--declared-only", pack["declared"])
        self.assertIn("commonDialog", pack["ids"])
        self.assertIn("dialogBody", pack["classes"])
        self.assertIn("another", pack["classes"])

    def test_scan_returns_none_without_archives(self):
        from fxcss import omni
        with tempfile.TemporaryDirectory() as td:
            binary = Path(td) / "firefox"
            binary.write_bytes(b"")
            self.assertIsNone(omni.scan(binary))


class SuggestPropertyTests(unittest.TestCase):
    """The rename inference for custom properties.

    Properties version by appending whole words (--panel-background became
    --panel-background-color), so the near-miss window is wider than the
    two-character one element renames get -- but a name with no close consumed
    relative must yield nothing rather than a guess.
    """

    PACK = {"consumed": {"--panel-background-color": "omni.ja!a.css",
                         "--toolbar-color": "omni.ja!a.css"}}

    def test_suffix_rename_is_suggested(self):
        from fxcss import omni
        self.assertEqual(
            omni.suggest_property("--panel-background", self.PACK),
            "--panel-background-color")

    def test_unrelated_name_gets_no_guess(self):
        from fxcss import omni
        self.assertIsNone(
            omni.suggest_property("--in-content-page-background", self.PACK))


class ClassifyUnusedPackTests(unittest.TestCase):
    """With shipped-chrome evidence, declared-but-unread overrides surface.

    This is the class of bug the resolution probe can never see: the name
    still resolves (something declares it), so probing calls the override
    healthy while no rule reads the value any more. --in-content-page-background
    died exactly this way, and cost a white dialog body under dark palettes.
    """

    def static(self, td, kept=()):
        theme = Path(td)
        sheet = theme / "chrome" / "x.css"
        return {
            "theme": theme,
            "reachable": 1,
            "orphans": [],
            "optional": [],
            "defined": {name: [(sheet, 1)] for name in
                        ("--dead-token", "--stale-token", "--healthy-override",
                         "--kept-token")},
            "used": {},
            "kept": set(kept),
        }

    PACK = {"consumed": {"--healthy-override": "omni.ja!a.css"},
            "declared": {"--stale-token"},
            "ids": {}, "classes": {}, "paths": []}

    def test_pack_splits_dead_stale_and_healthy(self):
        from fxcss import audit
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "chrome").mkdir()
            result = audit.classify_unused(
                self.static(td, kept=("--kept-token",)), set(), pack=self.PACK)
        self.assertEqual([i["name"] for i in result["stale_overrides"]],
                         ["--stale-token"])
        self.assertEqual([i["name"] for i in result["unused_properties"]],
                         ["--dead-token"])
        self.assertEqual(result["overrides"], 1)
        self.assertEqual(result["kept"], ["--kept-token"])
        self.assertTrue(result["packed"])

    def test_without_pack_behaviour_is_the_old_one(self):
        from fxcss import audit
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "chrome").mkdir()
            result = audit.classify_unused(
                self.static(td), {"--stale-token", "--healthy-override"})
        # A resolution probe cannot tell stale from healthy: both count as
        # overrides, and only the never-declared name reads as dead.
        self.assertEqual(result["stale_overrides"], [])
        self.assertEqual(result["overrides"], 2)
        self.assertEqual([i["name"] for i in result["unused_properties"]],
                         ["--dead-token", "--kept-token"])
        self.assertFalse(result["packed"])


class KeepPragmaTests(unittest.TestCase):
    """`/* fxcss-keep */` on a declaration line exempts that property.

    The audit judges against one Firefox, but themes support several: WhiteSur
    keeps --in-content-page-background for ESR 140, which current Firefox has
    dropped entirely. The pragma is how an author records that on the line
    itself rather than in a CI flag nobody finds later.
    """

    def test_pragma_is_collected_from_the_raw_line(self):
        from fxcss import audit
        with tempfile.TemporaryDirectory() as td:
            sheet = Path(td) / "a.css"
            sheet.write_text(
                ":root {\n"
                "  --kept-one: red; /* fxcss-keep: ESR 140 still reads it */\n"
                "  --plain-one: blue;\n"
                "}\n", encoding="utf-8")
            defined, used, kept = audit.custom_properties([sheet])
        self.assertEqual(kept, {"--kept-one"})
        self.assertIn("--plain-one", defined)


class OffscreenSelectorTests(unittest.TestCase):
    """A selector missing live but shipped in chrome markup is healthy.

    The live audit cannot open every state -- the window-modal dialog most of
    all, since Marionette dismisses it on sight -- so #commonDialog used to
    land in `unresolved` with the suspicious framing that implies. The pack
    knows better: the markup ships, the state just never opened.
    """

    DOM = {"ids": set(), "classes": set()}
    PACK = {"ids": {"commonDialog": "omni.ja!chrome/commonDialog.xhtml"},
            "classes": {"dialogBody": "omni.ja!chrome/commonDialog.xhtml"},
            "consumed": {}, "declared": set(), "paths": []}

    def test_shipped_markup_resolves_to_offscreen(self):
        from fxcss import audit
        finding = audit.suggest("#commonDialog", self.DOM, pack=self.PACK)
        self.assertEqual(finding["confidence"], "offscreen")
        self.assertIn("commonDialog.xhtml", finding["reason"])

    def test_unknown_name_stays_unresolved(self):
        from fxcss import audit
        finding = audit.suggest("#neverExisted", self.DOM, pack=self.PACK)
        self.assertEqual(finding["confidence"], "unresolved")

    def test_offscreen_never_reaches_a_patch(self):
        from fxcss import audit
        result = {"findings": [
            {"confidence": "offscreen", "token": "#commonDialog", "uses": []}]}
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "fix.diff"
            self.assertEqual(audit.write_patch(result, Path(td), out), 0)


class DialogViewContractTests(unittest.TestCase):
    """Pin the constraints the modal-dialog capture cannot survive without.

    Each of these was found the hard way: Marionette's prompt listener aborts
    in-flight scripts and dismisses the dialog on the next command (hence the
    handoff file), the parent window's setTimeout is suspended while modal
    (hence nsITimer), and MODAL_TYPE_WINDOW blocks a nested event loop until
    a human closes the OS window (hence INTERNAL_WINDOW only).
    """

    def source(self):
        from fxcss import core
        return core.DIALOG_VIEW

    def test_result_travels_by_file_not_return_value(self):
        self.assertIn("IOUtils.writeUTF8", self.source())

    def test_sleeps_are_nsitimer_not_settimeout(self):
        source = self.source()
        self.assertIn("nsITimer", source)
        self.assertNotIn("setTimeout", source)

    def test_only_the_in_window_modal_type_is_used(self):
        # The comment may (and should) warn about MODAL_TYPE_WINDOW; the
        # argument actually passed is what must stay INTERNAL_WINDOW.
        source = self.source()
        self.assertIn("ps.MODAL_TYPE_INTERNAL_WINDOW,", source)
        self.assertNotIn("ps.MODAL_TYPE_WINDOW,", source)

    def test_the_view_is_captured_per_colour_scheme(self):
        from fxcss import core
        source = Path(core.__file__).read_text(encoding="utf-8")
        self.assertIn('f"{mode}-04-dialog"', source)


class InjectorReachTests(unittest.TestCase):
    """Live sheets must reach every chrome document, present and future.

    Loading into the top window's windowUtils misses the window-modal
    dialog's own document and every window opened later -- a watch session
    then shows edits everywhere except the quit prompt, silently. The swap
    walks all chrome docshells and an observer covers documents created
    afterwards; both halves have to stay in the scripts.
    """

    def test_swaps_walk_every_chrome_docshell(self):
        from fxcss import core
        for script in (core.SWAP_SHEET, core.SWAP_FILE_SHEET):
            self.assertIn("getAllDocShellsInSubtree", script)

    def test_future_documents_are_covered_by_the_observer(self):
        from fxcss import core
        for script in (core.SWAP_SHEET, core.SWAP_FILE_SHEET):
            self.assertIn("chrome-document-global-created", script)

    def test_no_module_urls_sneak_in(self):
        from fxcss import core
        self.assertNotIn("importESModule", core._INJECTOR_JS)
