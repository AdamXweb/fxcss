"""The shared stylesheet inventory: one reading of @import and url() for all.

Reproductions from the 132-theme evaluation: unquoted ``@import url(x)``
lines (FlexFox) were missed and their sheets called orphans; ``@import "x"
screen and -moz-pref("…")`` (the GNOME theme) was missed too; a package with
unresolved required imports read as a clean analysis.
"""

import tempfile
import unittest
from pathlib import Path

from fxcss import audit, inventory


def _write(root, files):
    for relative, text in files.items():
        path = Path(root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class ReferenceSyntaxTests(unittest.TestCase):
    def refs(self, text):
        return list(inventory.references(text))

    def test_quoted_unquoted_and_url_forms_are_all_read(self):
        found = self.refs('@import "a.css";\n@import url(b.css);\n'
                          "@import url('c.css');\n@import url( ./d.css );\n"
                          ".x { background: url(img/e.png) }\n")
        self.assertEqual([(r["kind"], r["target"]) for r in found],
                         [("import", "a.css"), ("import", "b.css"),
                          ("import", "c.css"), ("import", "./d.css"),
                          ("url", "img/e.png")])

    def test_conditions_are_kept_and_classified(self):
        found = self.refs('@import "colors/black.css" screen and -moz-pref("gnomeTheme.oledBlack");\n'
                          '@import "wide.css" (min-width: 1000px);\n'
                          '@import url(sup.css) supports(display: grid);\n'
                          '@import "layered.css" layer(base);\n'
                          '@import "plain.css";\n')
        kinds = [(r["target"], r["condition_kind"], r["prefs"]) for r in found]
        self.assertEqual(kinds, [
            ("colors/black.css", "pref", ["gnomeTheme.oledBlack"]),
            ("wide.css", "media", []),
            ("sup.css", "supports", []),
            ("layered.css", "", []),
            ("plain.css", "", []),
        ])

    def test_comments_and_strings_do_not_create_references(self):
        found = self.refs('/* @import "dead.css"; */\n'
                          '.x { content: "@import \'nope.css\'"; }\n'
                          '@import "live.css"; /**/\n')
        self.assertEqual([r["target"] for r in found], ["live.css"])

    def test_escaped_paths_are_decoded(self):
        found = self.refs('@import "my\\ theme/sheet.css";\n'
                          '@import url("caf\\e9 .css");\n')
        self.assertEqual([r["target"] for r in found],
                         ["my theme/sheet.css", "café.css"])

    def test_local_target_filters_absolute_and_scheme_references(self):
        self.assertIsNone(inventory.local_target("chrome://browser/skin/x.css"))
        self.assertIsNone(inventory.local_target("data:text/css,x"))
        self.assertIsNone(inventory.local_target("/abs.css"))
        self.assertIsNone(inventory.local_target("#frag"))
        self.assertEqual(inventory.local_target("a/b.css?v=2#x"), "a/b.css")
        self.assertEqual(inventory.local_target("a%20b.css"), "a b.css")


class ScanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.chrome = self.root / "chrome"

    def test_flexfox_style_unquoted_imports_are_reachable_not_orphans(self):
        _write(self.chrome, {
            "userChrome.css": "@import url(./components/uc-variables.css);\n"
                              "@import url(./components/uc-tabs-bar.css);\n",
            "components/uc-variables.css": ":root { --a: 1 }\n",
            "components/uc-tabs-bar.css": "#tabs { }\n",
            "userContent.css": "@import url(./content/uc-newtab.css);\n",
            "content/uc-newtab.css": "body { }\n",
        })
        found = inventory.scan(self.chrome)
        self.assertTrue(found["complete"], found["reasons"])
        self.assertEqual(sorted(found["sheets"]), [
            "components/uc-tabs-bar.css", "components/uc-variables.css",
            "content/uc-newtab.css", "userChrome.css", "userContent.css"])
        self.assertEqual(found["sheets"]["content/uc-newtab.css"]["scope"], ["content"])
        self.assertEqual(found["sheets"]["components/uc-tabs-bar.css"]["scope"], ["chrome"])
        static = audit.collect_unused(self.root)
        self.assertEqual(static["orphans"], [])
        self.assertEqual(static["reachable"], 5)
        self.assertTrue(static["analysis"]["complete"])
        self.assertEqual(static["analysis"]["content_only"],
                         ["content/uc-newtab.css", "userContent.css"])

    def test_gnome_style_conditional_import_is_conditional_not_missing(self):
        _write(self.chrome, {
            "userChrome.css": '@import "theme/gnome-theme.css";\n@import "customChrome.css";\n',
            "theme/gnome-theme.css": '@import "colors/light.css";\n'
                                     '@import "colors/black.css" screen and -moz-pref("gnomeTheme.oledBlack");\n'
                                     '@import "platforms/windows.css" (-moz-platform: windows);\n',
            "theme/colors/light.css": ":root { }\n",
            "theme/colors/black.css": ":root { }\n",
        })
        found = inventory.scan(self.chrome)
        self.assertTrue(found["complete"], found["reasons"])
        self.assertTrue(found["sheets"]["theme/colors/black.css"]["conditional"])
        self.assertFalse(found["sheets"]["theme/colors/light.css"]["conditional"])
        self.assertEqual([e["target"] for e in found["conditional"]], ["platforms/windows.css"])
        self.assertEqual([Path(e["resolved"]).name for e in found["hooks"]], ["customChrome.css"])
        self.assertEqual(found["unresolved"], [])

    def test_unresolved_required_import_makes_the_analysis_incomplete(self):
        _write(self.chrome, {
            "userChrome.css": '@import "user-chrome.css";\n@import "custom.css";\n',
        })
        found = inventory.scan(self.chrome)
        self.assertFalse(found["complete"])
        self.assertEqual([e["target"] for e in found["unresolved"]], ["user-chrome.css"])
        self.assertEqual([Path(e["resolved"]).name for e in found["hooks"]], ["custom.css"])
        self.assertTrue(any("user-chrome.css" in r for r in found["reasons"]))
        static = audit.collect_unused(self.root)
        self.assertFalse(static["analysis"]["complete"])

    def test_no_entry_sheet_is_incomplete(self):
        _write(self.chrome, {"theme/part.css": "#a { }\n"})
        found = inventory.scan(self.chrome)
        self.assertFalse(found["complete"])
        self.assertEqual(found["entries"], [])
        self.assertEqual(found["missing_entries"], ["userChrome.css", "userContent.css"])

    def test_content_only_theme_is_complete_with_content_scope(self):
        _write(self.chrome, {"userContent.css": '@import "pages/newtab.css";\n',
                             "chrome.css": "",
                             "pages/newtab.css": "body { }\n"})
        found = inventory.scan(self.chrome)
        self.assertTrue(found["complete"])
        self.assertEqual(found["entries"], ["userContent.css"])
        self.assertEqual(found["sheets"]["pages/newtab.css"]["scope"], ["content"])

    def test_shared_sheet_carries_both_scopes(self):
        _write(self.chrome, {"userChrome.css": '@import "shared.css";\n',
                             "userContent.css": '@import "shared.css";\n',
                             "shared.css": ":root { }\n"})
        found = inventory.scan(self.chrome)
        self.assertEqual(found["sheets"]["shared.css"]["scope"], ["chrome", "content"])
        self.assertTrue(inventory.chrome_scoped(found, "shared.css"))

    def test_assets_and_outside_references_are_separated(self):
        _write(self.root, {
            "chrome/userChrome.css": '@import "../src/base.css";\n'
                                     '#a { background: url(icons/a.svg) }\n'
                                     '#b { list-style-image: url("chrome://browser/skin/x.svg") }\n',
            "chrome/icons/a.svg": "<svg/>",
            "src/base.css": "#c { }\n",
        })
        found = inventory.scan(self.chrome)
        self.assertEqual(found["assets"], {"icons/a.svg"})
        self.assertEqual(len(found["outside"]), 1)
        self.assertEqual(len(found["external"]), 1)
        self.assertEqual(inventory.reachable_paths(self.chrome),
                         {"userChrome.css", "icons/a.svg"})
        # audit still reads the outside sheet, as it always has.
        names = {p.name for p in audit.import_graph(self.root)}
        self.assertIn("base.css", names)

    def test_content_only_sheet_tokens_are_scoped_out_with_a_reason(self):
        _write(self.chrome, {
            "userChrome.css": "#nav-bar { }\n",
            "userContent.css": '@import "pages/newtab.css";\n',
            "pages/newtab.css": "#newtab-grid { }\n",
        })
        tokens = audit.extract_tokens(self.root)
        self.assertFalse(tokens["#nav-bar"][0]["scoped_out"])
        self.assertTrue(tokens["#newtab-grid"][0]["scoped_out"])
        self.assertEqual(tokens["#newtab-grid"][0]["document"], "content")
        finding = audit.suggest("#newtab-grid", {"ids": set(), "classes": set()},
                                scoped_out=True, document="content")
        self.assertEqual(finding["confidence"], "other-document")
        self.assertIn("userContent.css", finding["reason"])


class OptionDiscoveryTests(unittest.TestCase):
    def test_options_have_a_relative_path_identity_and_skip_active_sheets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, {
                "chrome/userChrome.css": '@import "extras/always.css";\n',
                "chrome/extras/always.css": "",
                "chrome/extras/hide-tabs.css": "",
                "custom/compact.css": "",
                "tweaks/extensions.css": "",
                "optional/compact.css": "",
            })
            found = inventory.options(root)
            ids = [o["id"] for o in found]
            self.assertEqual(ids, ["custom/compact.css", "optional/compact.css",
                                   "tweaks/extensions.css", "chrome/extras/hide-tabs.css"])
            self.assertEqual(inventory.match_option("custom/compact.css", found)["id"],
                             "custom/compact.css")
            # A bare stem recorded by an older fxcss is ambiguous here; the
            # sheet beside the theme would have been the only one it offered
            # -- but two of those exist, so nothing is guessed.
            self.assertIsNone(inventory.match_option("compact", found))
            self.assertEqual(inventory.match_option("hide-tabs", found)["id"],
                             "chrome/extras/hide-tabs.css")
            self.assertEqual(inventory.match_option("extensions", found)["folder"], "tweaks")


if __name__ == "__main__":
    unittest.main()
