"""Installation plans for the layouts real themes use.

Synthetic fixtures modelled on catalogue entries: flat repositories
(ArcWTF, Foxlim), sibling editions (EdgeFox, Simplify), userContent-only
(GlassyFox), nested roots (Not-Holar, NewFox), historical folders (Quietfox),
a committed chrome.zip (Win 7 Aero), named editions assembled from common/
(WhiteSur) and a repository with no entry sheet at all (Human).
"""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from fxcss import inventory, layout


def _write(root, files):
    for relative, text in files.items():
        path = Path(root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
    return Path(root)


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.src = self.root / "src"
        self.work = self.root / "work"

    def plan(self, files, **kwargs):
        import shutil
        shutil.rmtree(self.src, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)
        _write(self.src, files)
        return layout.discover(self.src, workdir=self.work, **kwargs)

    def test_standard_layout_at_the_root_is_selected_in_place(self):
        plan = self.plan({"chrome/userChrome.css": "", "README.md": "x"})
        self.assertEqual((plan["status"], plan["kind"], plan["subpath"]),
                         ("ok", "standard", None))
        self.assertEqual(layout.materialize(plan, self.work), self.src)

    def test_flat_layout_is_the_repository_itself_never_its_parent(self):
        plan = self.plan({"userChrome.css": '@import "theme/a.css";\n#x { background: url(theme/i/a.svg) }\n',
                          "theme/a.css": "", "theme/i/a.svg": "<svg/>",
                          "README.md": "x", "LICENSE": "x", "install.sh": "cp",
                          "screenshots/one.png": b"\x89PNG", ".gitignore": ""})
        self.assertEqual((plan["status"], plan["kind"]), ("ok", "flat"))
        self.assertIsNone(layout.find_theme_root(self.src))
        root = layout.materialize(plan, self.work)
        self.assertTrue(str(root).startswith(str(self.work)))
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        self.assertEqual(files, ["chrome/theme/a.css", "chrome/theme/i/a.svg",
                                 "chrome/userChrome.css"])

    def test_referenced_doc_like_folders_are_kept(self):
        plan = self.plan({"userChrome.css": "#x { background: url(images/bg.png) }\n",
                          "images/bg.png": b"png", "images/unused.png": b"png",
                          "docs/notes.md": "x"})
        root = layout.materialize(plan, self.work)
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        self.assertIn("chrome/images/bg.png", files)
        self.assertIn("chrome/images/unused.png", files)
        self.assertNotIn("chrome/docs/notes.md", files)

    def test_content_only_theme_is_supported(self):
        plan = self.plan({"userContent.css": "body { }\n", "img/a.png": b"png"})
        self.assertEqual((plan["status"], plan["kind"]), ("ok", "content-only"))
        root = layout.materialize(plan, self.work)
        self.assertTrue((root / "chrome" / "userContent.css").is_file())
        self.assertFalse((root / "chrome" / "userChrome.css").exists())

    def test_nested_single_edition_is_found_with_its_subpath(self):
        plan = self.plan({"firefox/chrome/userChrome.css": "", "README.md": "",
                          "tree_style_tabs/extra.css": ""})
        self.assertEqual((plan["kind"], plan["subpath"]), ("standard", "firefox"))
        self.assertEqual(layout.materialize(plan, self.work), self.src / "firefox")
        plan = self.plan({"NewFox/profile/chrome/userChrome.css": "",
                          "NewFox/config.js": "", "README.md": ""})
        self.assertEqual(plan["subpath"], "NewFox/profile")

    def test_sibling_editions_require_an_explicit_choice(self):
        files = {"1.Normal/chrome/userChrome.css": "a", "2.WithTST/chrome/userChrome.css": "b",
                 "3.Old Versions/1.0/userChrome.css": "c", "IMG/x.png": b"png"}
        plan = self.plan(files)
        self.assertEqual(plan["status"], "ambiguous")
        self.assertIn("1.Normal", plan["reason"])
        self.assertIn("2.WithTST", plan["reason"])
        self.assertNotIn("Old Versions", plan["reason"])
        self.assertIsNone(plan["selected"])
        chosen = layout.discover(self.src, subpath="2.WithTST", workdir=self.work)
        self.assertEqual((chosen["status"], chosen["subpath"]), ("ok", "2.WithTST"))
        self.assertEqual(layout.materialize(chosen, self.work), self.src / "2.WithTST")
        missing = layout.discover(self.src, subpath="4.Nope", workdir=self.work)
        self.assertEqual(missing["status"], "unsupported")
        self.assertIn("1.Normal", missing["reason"])

    def test_flat_editions_in_named_folders_are_ambiguous_until_chosen(self):
        plan = self.plan({"Simplify Mint/Clean/userChrome.css": "", "Simplify Mint/Clean/userContent.css": "",
                          "Simplify XP/userChrome.css": "", "README.md": ""})
        self.assertEqual(plan["status"], "ambiguous")
        chosen = layout.discover(self.src, subpath="Simplify Mint/Clean", workdir=self.work)
        self.assertEqual(chosen["kind"], "flat")
        root = layout.materialize(chosen, self.work)
        self.assertEqual(sorted(p.name for p in (root / "chrome").iterdir()),
                         ["userChrome.css", "userContent.css"])

    def test_historical_and_other_application_folders_are_demoted(self):
        plan = self.plan({"chrome/userChrome.css": "", "old/72-74/userChrome.css": "",
                          "old/71/userChrome.css": ""})
        self.assertEqual((plan["status"], plan["subpath"]), ("ok", None))
        plan = self.plan({"Firefox/userChrome.css": "", "Thunderbird/userChrome.css": ""})
        self.assertEqual((plan["status"], plan["subpath"]), ("ok", "Firefox"))

    def test_root_chrome_beats_a_flat_root_copy_and_misnamed_entries_lose(self):
        plan = self.plan({"chrome/userChrome.css": "a", "userChrome.css": "b"})
        self.assertEqual(plan["kind"], "standard")
        self.assertTrue(any("flat" in n for n in plan["notes"]))
        plan = self.plan({"userChrome.css": "a", "userContent.css": "b", "TYH/x.css": "",
                          "chrome/userchrome.css": "legacy", "chrome/usercontent.css": ""})
        self.assertEqual((plan["kind"], plan["subpath"]), ("flat", None))
        root = layout.materialize(plan, self.work)
        self.assertEqual((root / "chrome" / "userChrome.css").read_text(), "a")

    def test_misnamed_entry_alone_is_installed_under_the_exact_name(self):
        plan = self.plan({"chrome/userchrome.css": "x"})
        self.assertEqual(plan["status"], "ok")
        self.assertTrue(plan["selected"]["misnamed"])
        root = layout.materialize(plan, self.work)
        self.assertEqual((root / "chrome" / "userChrome.css").read_text(), "x")
        self.assertIn("userChrome.css", [p.name for p in (root / "chrome").iterdir()])
        self.assertNotEqual(root, self.src)

    def test_alternatives_inside_the_selected_chrome_are_not_competing_editions(self):
        plan = self.plan({"chrome/userChrome.css": "", "chrome/Custom Themes/A/userChrome.css": "",
                          "chrome/Custom Themes/B/userChrome.css": ""})
        self.assertEqual((plan["status"], plan["subpath"]), ("ok", None))
        self.assertTrue(any("inside the selected chrome/" in n for n in plan["notes"]))

    def test_named_editions_are_chosen_by_entry_file_and_assembled_from_common(self):
        files = {
            "src/userChrome-WhiteSur.css": '@import "WhiteSur/theme.css";\n',
            "src/userContent-WhiteSur.css": '@import "WhiteSur/pages/common.css";\n',
            "src/userChrome-Monterey.css": '@import "Monterey/theme.css";\n',
            "src/WhiteSur/theme.css": '@import "parts/buttons.css";\n',
            "src/Monterey/theme.css": "",
            "src/common/parts/buttons.css": "#b { }\n",
            "src/common/pages/common.css": "body { }\n",
            "src/customChrome.css": "",
            "install.sh": "cp -r", "README.md": "",
        }
        plan = self.plan(files)
        self.assertEqual(plan["status"], "ambiguous")
        self.assertIn("src/userChrome-WhiteSur.css", plan["reason"])
        chosen = layout.discover(self.src, subpath="src/userChrome-WhiteSur.css", workdir=self.work)
        self.assertEqual((chosen["status"], chosen["kind"]), ("ok", "named"))
        root = layout.materialize(chosen, self.work)
        chrome = root / "chrome"
        self.assertEqual((chrome / "userChrome.css").read_text(), '@import "WhiteSur/theme.css";\n')
        self.assertTrue((chrome / "userContent.css").is_file())
        self.assertFalse((chrome / "userChrome-Monterey.css").exists())
        self.assertTrue((chrome / "WhiteSur" / "parts" / "buttons.css").is_file())
        self.assertTrue((chrome / "WhiteSur" / "pages" / "common.css").is_file())
        found = inventory.scan(chrome)
        self.assertTrue(found["complete"], found["reasons"])
        self.assertTrue(any("common/ merged into WhiteSur/" in n for n in chosen["notes"]))

    def test_packaged_zip_is_unpacked_as_data_within_bounds(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zipped:
            zipped.writestr("chrome/userChrome.css", "#x { }\n")
            zipped.writestr("chrome/icons/a.svg", "<svg/>")
        plan = self.plan({"chrome.zip": buffer.getvalue(), "README.md": ""})
        self.assertEqual((plan["status"], plan["kind"], plan["packaged"]),
                         ("ok", "standard", "chrome.zip"))
        self.assertEqual(plan["subpath"], "chrome.zip")
        root = layout.materialize(plan, self.work)
        self.assertTrue(str(root).startswith(str(self.work)))
        self.assertTrue((root / "chrome" / "icons" / "a.svg").is_file())

    def test_zip_escaping_its_folder_is_refused(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zipped:
            zipped.writestr("../evil.css", "")
        plan = self.plan({"chrome.zip": buffer.getvalue()})
        self.assertEqual(plan["status"], "unsupported")
        self.assertIn("outside", plan["reason"])
        self.assertFalse((self.root / "evil.css").exists())

    def test_rar_archives_are_a_precise_unsupported_reason(self):
        plan = self.plan({"002.rar": b"Rar!", "theme.rar": b"Rar!", "README.md": ""})
        self.assertEqual(plan["status"], "unsupported")
        self.assertIn("rar", plan["reason"].lower())
        self.assertIn("extract it yourself", plan["reason"])

    def test_no_entry_sheet_is_unsupported_with_a_fragment_hint(self):
        plan = self.plan({"Brave-Fox/Import.css": '@import "tabs.css";\n',
                          "Brave-Fox/tabs.css": "", "README.md": "add @import"})
        self.assertEqual(plan["status"], "unsupported")
        self.assertIn("--entry", plan["reason"])
        self.assertIn("Brave-Fox/Import.css", plan["reason"])
        chosen = layout.discover(self.src, entry="Brave-Fox/Import.css", workdir=self.work)
        self.assertEqual((chosen["status"], chosen["kind"]), ("ok", "fragment"))
        root = layout.materialize(chosen, self.work)
        self.assertIn('@import "Import.css";', (root / "chrome" / "userChrome.css").read_text())
        self.assertTrue((root / "chrome" / "tabs.css").is_file())
        self.assertTrue(inventory.scan(root / "chrome")["complete"])

    def test_entry_outside_the_source_is_refused(self):
        _write(self.root, {"outside.css": ""})
        plan = self.plan({"a/b.css": ""}, entry="../outside.css")
        self.assertEqual(plan["status"], "unsupported")

    def test_install_scripts_are_reported_never_run(self):
        plan = self.plan({"install.sh": "rm -rf /", "README.md": ""})
        self.assertEqual(plan["status"], "unsupported")
        self.assertIn("install.sh", plan["reason"])
        self.assertIn("does not run", plan["reason"])

    def test_configuration_travels_with_a_flat_edition(self):
        plan = self.plan({"userChrome.css": "", "configuration/user.js": 'user_pref("a", 1);\n'})
        root = layout.materialize(plan, self.work)
        self.assertTrue((root / "configuration" / "user.js").is_file())
        self.assertFalse((root / "chrome" / "configuration").exists())


class StandardRootTests(unittest.TestCase):
    def test_standard_themes_are_returned_unchanged_and_flat_ones_materialised(self):
        with tempfile.TemporaryDirectory() as td:
            standard = _write(Path(td) / "s", {"chrome/userChrome.css": ""})
            self.assertEqual(layout.standard_root(standard), standard)
            flat = _write(Path(td) / "f", {"userChrome.css": "#a { }\n"})
            root = layout.standard_root(flat)
            self.assertNotEqual(root, flat)
            self.assertTrue((root / "chrome" / "userChrome.css").is_file())
            self.assertEqual(layout.standard_root(flat), root)
            with self.assertRaises(layout.LayoutError):
                layout.standard_root(_write(Path(td) / "n", {"README.md": ""}))


if __name__ == "__main__":
    unittest.main()
