"""Which Firefox releases a theme change still works on, without the network."""

import argparse
import contextlib
import gzip
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fxcss import cli, omni, support


def names(ids=(), classes=(), properties=()):
    return {"ids": set(ids), "classes": set(classes), "properties": set(properties)}


# Firefox 158 moved #sidebar-box to a class and dropped --tab-stroke-old,
# which only existed from 155; 157 was never published to the archive here.
BUILDS = {
    "153.0": names({"nav-bar", "sidebar-box"}, {"tabbrowser-tab"}, {"--tab-stroke-keep"}),
    "154.0": names({"nav-bar", "sidebar-box"}, {"tabbrowser-tab"}, {"--tab-stroke-keep"}),
    "155.0": names({"nav-bar", "sidebar-box"}, {"tabbrowser-tab"},
                   {"--tab-stroke-keep", "--tab-stroke-old"}),
    "156.0": names({"nav-bar", "sidebar-box"}, {"tabbrowser-tab"},
                   {"--tab-stroke-keep", "--tab-stroke-old"}),
    "157.0": None,
    "158.0.1": names({"nav-bar"}, {"tabbrowser-tab", "sidebar-box", "ai-chip"},
                     {"--tab-stroke-keep", "--tab-stroke-new"}),
}
RELEASES = {"release": 158, "beta": 160, "nightly": 161, "esr": 153,
            "next_release_date": "2026-11-06"}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.downloads = []
        self.builds = dict(BUILDS)
        self.firefox = self.binary("158.0.1")

    def binary(self, version):
        folder = self.root / f"firefox-{version}"
        folder.mkdir(exist_ok=True)
        (folder / "application.ini").write_text(f"[App]\nVersion={version}\n")
        (folder / "firefox").write_text("")
        return folder / "firefox"

    def theme(self, name, css, variant=None):
        theme = self.root / name
        (theme / "chrome").mkdir(parents=True, exist_ok=True)
        (theme / "chrome/userChrome.css").write_text(css)
        if variant:
            (theme / "custom").mkdir(exist_ok=True)
            (theme / "custom/option.css").write_text(variant)
        return theme

    def download(self, version):
        self.downloads.append(version)
        return self.builds.get(version)

    def check(self, base, head, oldest="esr", **options):
        options.setdefault("releases", lambda: RELEASES)
        return support.run_check(self.theme("base", base), self.theme("head", head), self.firefox,
                                 oldest, self.root / "cache", download=self.download, **options)


class RenameTests(Fixture):
    def test_a_rename_drops_styling_for_every_older_release_and_says_where(self):
        report = self.check("#nav-bar #sidebar-box { color: red; }\n",
                            "#nav-bar .sidebar-box { color: red; }\n")
        self.assertEqual(report["breaks_below"], 158)
        self.assertEqual(report["new_only_from"], 158)
        dropped, = report["dropped"]
        self.assertEqual((dropped["selector"], dropped["names"], dropped["since"]),
                         ("#nav-bar #sidebar-box", ["#sidebar-box"], 158))
        self.assertNotIn("from", dropped)
        self.assertEqual(report["added"][0]["names"], [".sidebar-box"])
        self.assertTrue(report["exact"])
        text = support.markdown(report)
        self.assertIn("**Merging this drops styling for Firefox 157 and earlier, ESR 153 included.**", text)
        self.assertIn("`#sidebar-box`", text)
        self.assertIn("only match from Firefox 158", text)
        self.assertIn("`chrome/userChrome.css:1` `#nav-bar #sidebar-box`", text)

    def test_a_name_younger_than_the_oldest_release_is_still_caught(self):
        report = self.check(":root { --tab-stroke-old: red; }\n",
                            ":root { --tab-stroke-new: red; }\n")
        dropped, = report["dropped"]
        self.assertEqual((dropped["since"], dropped["from"]), (158, 155))
        text = support.markdown(report)
        self.assertIn("drops styling for Firefox 155 to 157.**", text)
        self.assertNotIn("ESR", text.split("<sub>")[0])

    def test_a_build_missing_from_the_archive_is_skipped_not_guessed(self):
        report = self.check("#sidebar-box {}\n", ".sidebar-box {}\n")
        self.assertEqual(report["breaks_below"], 158)
        self.assertTrue(report["exact"])
        self.assertNotIn(157, report["checked"])
        self.assertIn(156, report["checked"])

    def test_a_spent_download_budget_reports_a_range(self):
        # #old-panel went away in 150, long before the previous release.
        for version in range(140, 153):
            self.builds[f"{version}.0"] = names({"old-panel"} if version < 150 else ())
        report = self.check("#old-panel {}\n", "#nav-bar {}\n", oldest="140", max_downloads=4)
        self.assertFalse(report["exact"])
        dropped, = report["dropped"]
        self.assertLess(dropped["since_low"], dropped["since"])
        text = support.markdown(report)
        self.assertIn(f"Firefox {dropped['since_low'] - 1} and earlier, possibly up to "
                      f"{dropped['since'] - 1}", text)
        self.assertNotIn("ESR 153 included", text)

    def test_keeping_the_old_name_beside_the_new_one_is_not_a_drop(self):
        report = self.check("#sidebar-box { color: red; }\n",
                            "#sidebar-box, .sidebar-box { color: red; }\n")
        self.assertEqual(report["dropped"], [])
        self.assertEqual(report["breaks_below"], None)
        self.assertEqual(report["new_only_from"], 158)

    def test_declaration_changes_download_only_the_oldest_build(self):
        report = self.check("#nav-bar { color: red; }\n", "#nav-bar { color: blue; }\n")
        self.assertEqual(report["candidates"], 0)
        self.assertEqual(self.downloads, ["153.0"])
        self.assertIn("Firefox support is unchanged", support.markdown(report))

    def test_rules_that_match_nowhere_or_everywhere_are_not_reported(self):
        report = self.check("#theme-only { }\n#nav-bar { }\n:root { --own-colour: red; }\n",
                            "#theme-renamed { }\n.tabbrowser-tab { }\n:root { --own-color: red; }\n")
        self.assertEqual((report["dropped"], report["added"]), ([], []))

    def test_variant_sheets_are_checked_too(self):
        base = self.theme("base", "#nav-bar {}\n", "#sidebar-box {}\n")
        head = self.theme("head", "#nav-bar {}\n", ".sidebar-box {}\n")
        report = support.run_check(base, head, self.firefox, 153, self.root / "cache",
                                   download=self.download, releases=lambda: RELEASES)
        self.assertEqual(report["dropped"][0]["file"], "custom/option.css")

    def test_an_unreleased_beta_warns_every_release_user(self):
        self.builds["160.0b1"] = self.builds.pop("158.0.1")
        self.builds["158.0"] = BUILDS["156.0"]
        self.firefox = self.binary("160.0b1")
        report = self.check("#sidebar-box {}\n", ".sidebar-box {}\n")
        self.assertEqual(report["breaks_below"], 160)
        text = support.markdown(report, channel="beta")
        self.assertIn("Firefox 159 and earlier", text)
        self.assertIn("Firefox 160 is not a release yet; release is 158", text)
        self.assertIn("(expected 2026-11-06)", text)
        self.assertIn("against Firefox beta 160.0b1", text)


class CacheAndFailureTests(Fixture):
    def test_indexes_are_cached_except_nightlies_and_missing_builds(self):
        self.check("#sidebar-box {}\n", ".sidebar-box {}\n")
        first = list(self.downloads)
        self.downloads.clear()
        self.check("#sidebar-box {}\n", ".sidebar-box {}\n")
        self.assertEqual(self.downloads, ["157.0"])  # only the build the archive lacked
        self.assertIn("158.0.1", first)
        self.builds["161.0a1"] = self.builds["158.0.1"]
        self.firefox = self.binary("161.0a1")
        self.check("#sidebar-box {}\n", ".sidebar-box {}\n")
        self.assertFalse((self.root / "cache/linux-161.0a1.json.gz").exists())
        cached = json.loads(gzip.decompress((self.root / "cache/linux-153.0.json.gz").read_bytes()))
        self.assertEqual((cached["format"], cached["version"]), (1, "153.0"))

    def test_esr_needs_the_release_feed_and_failures_are_reported(self):
        with self.assertRaisesRegex(support.Unavailable, "current ESR"):
            self.check("#a {}\n", "#b {}\n", releases=lambda: None)
        args = argparse.Namespace(base=self.root / "base", head=self.root / "head",
                                  firefox=str(self.firefox), oldest="esr", cache=self.root / "c",
                                  max_downloads=8, json=self.root / "out/support.json",
                                  markdown=self.root / "out/support.md", channel=None)
        with patch.object(support, "release_info", return_value=None), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(support.run(args), 2)
        self.assertEqual(json.loads(args.json.read_text())["status"], "unavailable")
        self.assertIn("could not be checked", args.markdown.read_text())

    def test_oldest_build_missing_is_unavailable_not_a_pass(self):
        self.builds.pop("153.0")
        with self.assertRaisesRegex(support.Unavailable, "no Linux build of Firefox 153.0"):
            self.check("#sidebar-box {}\n", ".sidebar-box {}\n")

    def test_cli_runs_against_an_explicit_binary(self):
        self.theme("base", "#sidebar-box {}\n")
        head = self.theme("head", ".sidebar-box {}\n")
        out = io.StringIO()
        with patch.object(support, "download_names", side_effect=self.download), \
                patch.object(support, "release_info", return_value=RELEASES), \
                contextlib.redirect_stdout(out):
            code = cli.main(["support", "--base", str(self.root / "base"), "--head", str(head),
                             "--firefox", str(self.firefox), "--cache", str(self.root / "cache"),
                             "--json", str(self.root / "support.json")])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("Firefox 157 and earlier", out.getvalue())
        self.assertEqual(json.loads((self.root / "support.json").read_text())["breaks_below"], 158)


class ParsingTests(unittest.TestCase):
    def test_selectors_and_declarations_by_line(self):
        selectors, properties = support.stylesheet(
            "/* #commented { } */\n"
            "@media (min-width: 1px) {\n"
            "  #nav-bar > .a,\n"
            "  .b:not(#hidden) { --x: 1; color: red; }\n"
            "}\n"
            "@-moz-document url(chrome://browser/content/browser.xhtml) {\n"
            "  [label=\"{#str}\"] .c { background: url(chrome://x/#frag.svg); }\n"
            "  .d { & .e { --y: var(--z); } }\n"
            "}\n")
        self.assertEqual(selectors, {"#nav-bar > .a": (3, "#nav-bar > .a"),
                                     ".b:not(#hidden)": (4, ".b:not(#hidden)"),
                                     "[label= ] .c": (7, '[label="{#str}"] .c'),
                                     ".d": (8, ".d"), "& .e": (8, "& .e")})
        self.assertEqual(properties, {"--x": 4, "--y": 8})

    def test_required_names_leave_out_what_cannot_stop_a_match(self):
        self.assertEqual(support.required_names("#a > .b:not(#c):is(.d, .e)::part(f) [g=.h]"),
                         ["#a", ".b"])
        self.assertEqual(support.required_names("#a:not(#hack)"), ["#a"])
        self.assertEqual(support.required_names("toolbar[open] > *"), [])

    def test_versions_and_archive_paths(self):
        self.assertEqual([support.major(v) for v in ("158.0.1", "160.0b1", "161.0a1",
                                                      "153.5.1esr", "nope", None)],
                         [158, 160, 161, 153, None, None])
        self.assertEqual(support.archive_urls("158.0.1")[0],
                         "https://archive.mozilla.org/pub/firefox/releases/158.0.1/"
                         "linux-x86_64/en-US/firefox-158.0.1.tar.xz")
        self.assertTrue(support.archive_urls("134.0")[1].endswith("firefox-134.0.tar.bz2"))
        self.assertIn("nightly/latest-mozilla-central/firefox-161.0a1.en-US.linux-x86_64",
                      support.archive_urls("161.0a1")[0])
        for bad in ("../158.0", "158.0/../x", "158"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                support.archive_urls(bad)
        self.assertEqual([support.parse_oldest(v) for v in ("esr", "ESR", " 140 ")],
                         ["esr", "esr", 140])
        for bad in ("", "0", "1000", "latest", "140.0"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                support.parse_oldest(bad)


class ChromeNameTests(unittest.TestCase):
    def test_names_keep_their_kind_wherever_they_are_written(self):
        with tempfile.TemporaryDirectory() as td:
            pack = Path(td) / "omni.ja"
            with zipfile.ZipFile(pack, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("chrome/browser/content/browser.xhtml",
                                 '<toolbar id="nav-bar" class="browser-toolbar chromeclass-x"/>')
                archive.writestr("chrome/browser/skin/browser.css",
                                 '/* #in-comment */ #urlbar .tab-close-button { color: #fff;'
                                 ' background: url("chrome://x/.not-a-class.svg");'
                                 ' fill: var(--css-prop); }')
                archive.writestr("modules/thing.sys.mjs",
                                 'document.getElementById("by-id"); el.classList.add("added", "two");'
                                 ' el.setAttribute("class", "attr-a attr-b"); el.id = "assigned";'
                                 ' doc.querySelector("#qs-id > .qs-class"); x = "--js-prop";')
                archive.writestr("res/image.png", b"#not-text .at-all")
            found = omni.chrome_names([pack])
        self.assertLessEqual({"nav-bar", "urlbar", "by-id", "assigned", "qs-id"}, found["ids"])
        self.assertLessEqual({"browser-toolbar", "chromeclass-x", "tab-close-button", "added",
                              "two", "attr-a", "attr-b", "qs-class"}, found["classes"])
        self.assertLessEqual({"--css-prop", "--js-prop"}, found["properties"])
        self.assertFalse({"by-id", "nav-bar"} & found["classes"])
        self.assertFalse({"added", "tab-close-button"} & found["ids"])
        self.assertFalse({"in-comment", "not-text"} & found["ids"])
        self.assertNotIn("not-a-class", found["classes"])


if __name__ == "__main__":
    unittest.main()
