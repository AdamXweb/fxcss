"""Exact token renames must not propose duplicate selectors (issue #26)."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fxcss import audit


class DuplicatePatchTests(unittest.TestCase):
    def check_patch(self, css):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "chrome").mkdir()
            (root / "chrome/userChrome.css").write_text(css, encoding="utf-8")
            dom = {"ids": set(), "classes": {"old"}}
            with patch.object(audit, "collect_dom", return_value=dom):
                result = audit.audit(None, root, verbose=False)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                audit.report(result, colour=False)
            path = root / "fix.diff"
            count = audit.write_patch(result, root, path)
            return count, path.read_text() if path.exists() else "", output.getvalue(), result

    def test_duplicate_on_an_adjacent_line_is_reported_but_not_patched(self):
        count, diff, report, result = self.check_patch(
            '#toolbar .old,\n#toolbar #old { display: none; }\n')
        self.assertEqual(count, 0)
        self.assertEqual(diff, "")
        self.assertIn("RENAMED", report)
        self.assertIn("not patched", report)
        self.assertIn("remove the redundant selector manually", report)
        self.assertNotIn("+ #toolbar .old", report)
        finding, = [f for f in result["findings"] if f["token"] == "#old"]
        self.assertEqual(finding["confidence"], "renamed")
        self.assertTrue(audit.actionable([finding]))

    def test_same_line_and_reversed_lists_are_protected(self):
        for css in ('.old, #old {}', '#old, .old {}', '#old,\n.old {}'):
            with self.subTest(css=css):
                self.assertEqual(self.check_patch(css)[0], 0)

    def test_nested_rules_functions_and_attribute_commas(self):
        for css in (
            '@media (prefers-color-scheme: dark) { .old, #old {} }',
            '.outer { color: red; & .old, & #old {} }',
            ':is(.a, .b) .old, :is(.a, .b) #old {}',
            '[label="a,b{c}"] .old, [label="a,b{c}"] #old {}',
            '/* { fake, } */ .old, /* ; , */ #old {}',
            '.old/* comment */.child, #old.child {}',
            '.outer\n  .old,\n.outer #old {}',
        ):
            with self.subTest(css=css):
                self.assertEqual(self.check_patch(css)[0], 0)

    def test_different_selectors_and_rules_still_receive_the_fix(self):
        for css in (
            '.old {}\n#old {}',
            '.left .old, .right #old {}',
            '.old[data-x="a b"], #old[data-x="a  b"] {}',
            '.old .child, #old/* comment */.child {}',
            '.old:focus, #old:hover {}',
            '#old { content: ".old, {}"; }',
            '#old {} /* .old, #old {} */',
        ):
            with self.subTest(css=css):
                count, diff, report, _ = self.check_patch(css)
                self.assertEqual(count, 1)
                self.assertIn("+", diff)
                self.assertNotIn("not patched", report)

    def test_a_blocked_occurrence_does_not_hide_an_independent_fix(self):
        count, diff, report, _ = self.check_patch(
            '.old, #old {}\n#old:hover { color: red; }\n')
        self.assertEqual(count, 1)
        self.assertIn(' .old, #old {}', diff)
        self.assertIn('+.old:hover { color: red; }', diff)
        self.assertNotIn('+.old, .old', diff)
        self.assertIn("not patched", report)

    def test_patch_writer_also_checks_findings_supplied_directly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "style.css").write_text('.old, #old {}')
            result = {"findings": [{"token": "#old", "replacement": ".old",
                      "confidence": "renamed", "uses": [{"file": "style.css", "line": 1}]}]}
            self.assertEqual(audit.write_patch(result, root, root / "fix.diff"), 0)


if __name__ == "__main__":
    unittest.main()
