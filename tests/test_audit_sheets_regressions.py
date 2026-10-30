"""Regressions for audit source locations and nested sheet comparisons."""

import os
import tempfile
import unittest
from pathlib import Path

from fxcss import audit, sheets


class AuditSourceLocationTests(unittest.TestCase):
    CSS = (":root {\n  --dead: red;\n  --stale: blue;\n  --dt: green;\n"
           "  color: var(--missing);\n  background: var(--dt);\n}\n")
    PACK = {"declared": {"--stale"}, "consumed": {},
            "devtools_declared": set(), "devtools_consumed": {"--dt"}}

    def check_locations(self, theme, source):
        static = audit.collect_unused(theme)
        result = audit.classify_unused(static, set(), pack=self.PACK)
        for category, name, line in (("unused_properties", "--dead", 2),
                                     ("stale_overrides", "--stale", 3),
                                     ("devtools_only", "--dt", 4),
                                     ("missing_properties", "--missing", 5)):
            with self.subTest(category=category):
                item, = result[category]
                self.assertEqual((item["name"], item["line"]), (name, line))
                self.assertEqual((theme / item["file"]).resolve(), source.resolve())
        read = result["devtools_only"][0]["read_here"]
        self.assertEqual(read["line"], 6)
        self.assertEqual((theme / read["file"]).resolve(), source.resolve())
        return result

    def test_root_alias_and_relative_theme_keep_relative_locations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            theme = root / "theme"
            (theme / "chrome").mkdir(parents=True)
            source = theme / "chrome/userChrome.css"
            source.write_text(self.CSS)
            alias = root / "alias"
            alias.symlink_to(theme, target_is_directory=True)
            for base in (theme, alias, Path(os.path.relpath(theme))):
                with self.subTest(base=base):
                    result = self.check_locations(base, source)
                    self.assertEqual(result["unused_properties"][0]["file"],
                                     "chrome/userChrome.css")

    def test_external_directory_file_and_import_locations_remain_usable(self):
        for mode in ("chrome", "file", "import"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                theme = root / "theme"
                theme.mkdir()
                external = root / "external"
                external.mkdir()
                source = external / "userChrome.css"
                source.write_text(self.CSS)
                if mode == "chrome":
                    (theme / "chrome").symlink_to(external, target_is_directory=True)
                else:
                    (theme / "chrome").mkdir()
                    entry = theme / "chrome/userChrome.css"
                    if mode == "file":
                        entry.symlink_to(source)
                    else:
                        entry.write_text('@import "../../external/userChrome.css";\n')
                result = self.check_locations(theme, source)
                self.assertTrue(Path(result["unused_properties"][0]["file"]).is_absolute())

    @unittest.skipUnless(Path('/private/var').is_dir(), 'macOS path alias')
    def test_var_private_var_alias(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as td:
            theme = Path(td)
            (theme / "chrome").mkdir()
            source = theme / "chrome/userChrome.css"
            source.write_text(self.CSS)
            self.assertNotEqual(theme, theme.resolve())
            self.check_locations(theme, source)


class NestedSheetConflictTests(unittest.TestCase):
    def report(self, first, second):
        return sheets.overlap(("first", sheets.declarations(first)),
                              ("second", sheets.declarations(second)))

    def test_distinct_parents_never_become_alternatives(self):
        for child in ("& > #a", "#a", "& > #a, & > #b"):
            with self.subTest(child=child):
                report = self.report(f'.x {{ {child} {{ color: red }} }}',
                                     f'.y {{ {child} {{ color: blue }} }}')
                self.assertEqual(report["shared"], 0)
                self.assertEqual(sheets.verdict(report), "")

    def test_same_nested_address_still_conflicts_and_normalizes_spacing(self):
        report = self.report('.x>.z { &>#a { color: red } }',
                             '.x > .z { & > #a { color: blue } }')
        self.assertEqual(report["conflicting"], 1)
        self.assertEqual(sheets.verdict(report), "alternatives")

    def test_all_ancestor_rules_are_retained(self):
        report = self.report('.x { .z { & > #a { color: red } } }',
                             '.y { .z { & > #a { color: blue } } }')
        self.assertEqual(report["shared"], 0)

    def test_conditions_and_parent_rules_both_remain_part_of_address(self):
        for wrapper in ('@media (prefers-color-scheme: dark)',
                        '@supports (display: grid)', '@layer theme'):
            with self.subTest(wrapper=wrapper):
                first = f'{wrapper} {{ .x {{ & > #a {{ color: red }} }} }}'
                same = f'{wrapper} {{ .x {{ & > #a {{ color: blue }} }} }}'
                other_parent = f'{wrapper} {{ .y {{ & > #a {{ color: blue }} }} }}'
                self.assertEqual(self.report(first, same)["conflicting"], 1)
                self.assertEqual(self.report(first, other_parent)["shared"], 0)
                self.assertEqual(self.report(first, '.x { & > #a { color: blue } }')["shared"], 0)

    def test_condition_inside_style_rule_preserves_parent(self):
        first = '.x { @media (width > 10px) { & > #a { color: red } } }'
        second = '.y { @media (width > 10px) { & > #a { color: blue } } }'
        self.assertEqual(self.report(first, second)["shared"], 0)
        self.assertEqual(self.report(first, first.replace('red', 'blue'))["conflicting"], 1)

    def test_top_level_at_rules_keep_existing_keys(self):
        self.assertEqual(sheets.declarations('@media screen { #a { color: red } }'),
                         {(("@media screen",), "#a", "color"): "red"})
        self.assertEqual(sheets.declarations('@media screen { color: red; }'), {})

    def test_conflicts_reads_nested_sheets_without_false_alternatives(self):
        with tempfile.TemporaryDirectory() as td:
            first, second = Path(td) / 'first.css', Path(td) / 'second.css'
            first.write_text('.x { & > #a { color: red } }')
            second.write_text('.y { & > #a { color: blue } }')
            self.assertEqual(sheets.conflicts([first, second]), [])
            second.write_text('.x { & > #a { color: blue } }')
            report, = sheets.conflicts([first, second])
            self.assertEqual(sheets.verdict(report), 'alternatives')


if __name__ == "__main__":
    unittest.main()
