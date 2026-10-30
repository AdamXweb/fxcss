"""Exact visual limits and migration of colliding optional-sheet names."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fxcss import capture, check, cli, compare, core


class ThresholdAndVariantMigrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base, self.head, self.out = [self.root / n for n in ("base", "head", "out")]
        self.base.mkdir()
        self.head.mkdir()

    def image(self, directory, name="light-01-window", size=(32, 16), changed=0):
        image = Image.new("RGB", size, "grey")
        for pixel in range(changed):
            image.putpixel((pixel % size[0], pixel // size[0]), (255, 0, 0))
        image.save(directory / f"{name}.png")

    def run_compare(self, **options):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(compare.run(self.base, self.head, self.out, "test", **options), 0)
        return json.loads((self.out / "summary.json").read_text())

    def test_one_pixel_on_a_large_capture_exceeds_zero_and_small_limits(self):
        size = (2560, 960)
        self.image(self.base, size=size)
        self.image(self.head, size=size, changed=1)
        report = self.run_compare()
        view, = report["views"]
        self.assertEqual(view["percent"], 0)
        self.assertEqual(view["changed_pixels"], 1)
        self.assertEqual(view["total_pixels"], size[0] * size[1])
        self.assertTrue(report["any_change"])
        self.assertEqual(view["image"], "light-01-window.png")
        self.assertTrue((self.out / view["image"]).is_file())
        for threshold, status in ((None, 0), (0, 1), (0.00004, 1), (0.00005, 0)):
            with self.subTest(threshold=threshold):
                self.assertEqual(check.comparison_status(report, threshold), status)

    def test_zero_changes_pass_a_zero_limit(self):
        self.image(self.base)
        self.image(self.head)
        report = self.run_compare()
        self.assertFalse(report["any_change"])
        self.assertEqual(check.comparison_status(report, 0), 0)

    def test_equality_passes_and_a_smaller_limit_fails(self):
        for size, changed, equal, smaller in (
                ((1000, 100), 1, 0.001, 0.00099),
                ((100, 100), 57, 0.57, 0.56999)):
            with self.subTest(threshold=equal):
                self.image(self.base, size=size)
                self.image(self.head, size=size, changed=changed)
                report = self.run_compare()
                self.assertEqual(check.comparison_status(report, equal), 0)
                self.assertEqual(check.comparison_status(report, smaller), 1)

    def test_display_minimum_does_not_hide_a_limit_violation(self):
        self.image(self.base, size=(1000, 100))
        self.image(self.head, size=(1000, 100), changed=1)
        report = self.run_compare(min_changed_percent=0.01)
        self.assertEqual(len(report["below_threshold"]), 1)
        self.assertEqual(report["changed_views"], [])
        self.assertEqual(check.comparison_status(report, 0), 1)

    def test_legacy_summaries_fall_back_to_percent(self):
        for counts in ({}, {"changed_pixels": 1}, {"total_pixels": 100},
                       {"changed_pixels": 0, "total_pixels": 0}):
            with self.subTest(counts=counts):
                report = {"only_in_base": [], "only_in_head": [],
                          "views": [{"percent": 0.25, **counts}]}
                self.assertEqual(check.comparison_status(report, 0), 1)
                self.assertEqual(check.comparison_status(report, 0.25), 0)

    def test_missing_views_fail_even_without_a_limit(self):
        self.image(self.base)
        self.image(self.head)
        self.image(self.base, "variant-removed")
        report = self.run_compare()
        self.assertEqual(report["only_in_base"], ["variant-removed"])
        for threshold in (None, 0, 0.001, 100):
            with self.subTest(threshold=threshold):
                self.assertEqual(check.comparison_status(report, threshold), 1)

    def migrated_theme(self):
        theme = self.root / "theme"
        (theme / "chrome").mkdir(parents=True)
        (theme / "chrome/userChrome.css").write_text("/* theme */\n")
        (theme / "custom").mkdir()
        custom = theme / "custom/tabs.css"
        custom.write_text("/* custom tabs */\n")
        old = core.find_variant_sheets(theme)
        self.assertEqual(old, {"tabs": custom})
        # Adding a cross-folder collision renames the existing slug too. Old
        # selections and baselines must be migrated, never silently reassigned.
        (theme / "optional").mkdir()
        optional = theme / "optional/tabs.css"
        optional.write_text("/* optional tabs */\n")
        new = core.find_variant_sheets(theme)
        self.assertEqual(new, {"custom-tabs": custom, "optional-tabs": optional})
        return theme, old, new

    def test_renamed_variant_fails_the_old_baseline(self):
        _, old, new = self.migrated_theme()
        for directory in (self.base, self.head):
            self.image(directory)
        for slug in old:
            self.image(self.base, f"variant-{slug}")
        for slug in new:
            self.image(self.head, f"variant-{slug}")
        report = self.run_compare()
        self.assertEqual(report["only_in_base"], ["variant-tabs"])
        self.assertEqual(report["only_in_head"], ["variant-custom-tabs", "variant-optional-tabs"])
        for threshold in (None, 0, 100):
            self.assertEqual(check.comparison_status(report, threshold), 1)
        # An explicit old comparison filter still sees the missing base view.
        selected = self.run_compare(views="variant-tabs")
        self.assertEqual(selected["only_in_base"], ["variant-tabs"])
        self.assertEqual(check.comparison_status(selected, None), 1)

    def test_old_explicit_selections_fail_before_browser_lookup(self):
        theme, _, new = self.migrated_theme()
        for option, value, expected in (
                ("--variants", "tabs", "no optional stylesheet named tabs"),
                ("--views", "variant-tabs", "no view matches variant-tabs")):
            with self.subTest(option=option):
                errors = io.StringIO()
                with patch.object(check.core, "find_firefox") as browser:
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                        status = cli.main(["check", "--theme", str(theme), option, value])
                    browser.assert_not_called()
                self.assertEqual(status, 2)
                self.assertIn(expected, errors.getvalue())
                for slug in new:
                    self.assertIn(slug, errors.getvalue())
        self.assertIn("variant-custom-tabs", capture.expected_views(new))


if __name__ == "__main__":
    unittest.main()
