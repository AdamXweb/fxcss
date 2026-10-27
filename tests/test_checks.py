"""Regression checks for comparison integrity and the combined project check."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fxcss import capture, check, cli, compare


class ComparisonIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base, self.head, self.out = [self.root / n for n in ("base", "head", "out")]
        self.base.mkdir()
        self.head.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(compare, "PANEL_WIDTH", 16).start()

    def image(self, directory, name="light-01-window", colour="grey"):
        Image.new("RGB", (16, 8), colour).save(directory / f"{name}.png")

    def run_compare(self, **options):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return compare.run(self.base, self.head, self.out, "test", **options)

    def test_view_patterns_limit_comparisons_and_full_copies(self):
        for name in ("light-01-window", "dark-01-window"):
            self.image(self.base, name)
            self.image(self.head, name, "red")
        self.image(self.head, "variant-new")
        self.assertEqual(self.run_compare(views="LIGHT-*, variant-new"), 0)
        report = json.loads((self.out / "summary.json").read_text())
        self.assertEqual([v["view"] for v in report["views"]], ["light-01-window"])
        self.assertEqual(report["only_in_head"], ["variant-new"])
        self.assertEqual(report["views_filter"], ["light-*", "variant-new"])
        self.assertEqual(sorted(p.name for p in (self.out / "full").iterdir()),
                         ["light-01-window.png", "variant-new.png"])
        self.assertFalse((self.out / "dark-01-window.png").exists())

    def test_a_view_pattern_that_matches_nothing_is_an_error(self):
        self.image(self.base)
        self.image(self.head)
        for views in ("light-*,dark-02-urlbr", "../light-01-window", ["light-*", 3]):
            with self.subTest(views=views):
                self.assertEqual(self.run_compare(views=views), 2)
                self.assertFalse((self.out / "summary.json").exists())

    def test_changes_under_the_minimum_are_listed_but_not_drawn(self):
        self.image(self.base)
        image = Image.new("RGB", (16, 8), "grey")
        image.putpixel((0, 0), (255, 0, 0))  # 1 of 128 pixels, about 0.8%
        image.save(self.head / "light-01-window.png")
        self.assertEqual(self.run_compare(min_changed_percent=5), 0)
        report = json.loads((self.out / "summary.json").read_text())
        self.assertFalse(report["any_change"])
        self.assertEqual(report["changed_views"], [])
        self.assertEqual([v["view"] for v in report["below_threshold"]], ["light-01-window"])
        self.assertFalse((self.out / "light-01-window.png").exists())
        self.assertEqual(self.run_compare(min_changed_percent=0.5), 0)
        report = json.loads((self.out / "summary.json").read_text())
        self.assertTrue(report["any_change"])
        self.assertEqual(report["below_threshold"], [])
        self.assertTrue((self.out / "light-01-window.png").exists())

    def test_invalid_minimum_is_an_error(self):
        self.image(self.base)
        self.image(self.head)
        for minimum in (-1, 101, float("nan"), "1"):
            with self.subTest(minimum=minimum):
                self.assertEqual(self.run_compare(min_changed_percent=minimum), 2)

    def test_invalid_inputs_fail_without_a_success_report(self):
        self.image(self.base)
        for case in ("missing", "empty", "corrupt"):
            with self.subTest(case=case):
                if case == "missing":
                    self.head.rmdir()
                elif case == "empty":
                    self.head.mkdir()
                else:
                    (self.head / "broken.png").write_bytes(b"not an image")
                self.assertEqual(self.run_compare(), 2)
                self.assertFalse((self.out / "summary.json").exists())

    def test_removed_view_is_a_change(self):
        self.image(self.base)
        self.image(self.head)
        self.image(self.base, "variant-removed")
        self.assertEqual(self.run_compare(), 0)
        report = json.loads((self.out / "summary.json").read_text())
        self.assertTrue(report["any_change"])
        self.assertEqual(report["only_in_base"], ["variant-removed"])

    def test_disjoint_views_are_reported_as_added_and_missing(self):
        self.image(self.base, "old")
        self.image(self.head, "new")
        self.assertEqual(self.run_compare(), 0)
        report = json.loads((self.out / "summary.json").read_text())
        self.assertTrue(report["any_change"])
        self.assertEqual(report["only_in_base"], ["old"])
        self.assertEqual(report["only_in_head"], ["new"])

    def test_reusing_output_removes_stale_images_but_keeps_unrelated_files(self):
        self.image(self.base)
        self.image(self.head, colour="red")
        self.image(self.head, "variant-removed")
        self.assertEqual(self.run_compare(), 0)
        unrelated = self.out / "notes.txt"
        unrelated.write_text("keep this")
        self.assertTrue((self.out / "light-01-window.png").exists())
        self.image(self.head)
        (self.head / "variant-removed.png").unlink()
        self.assertEqual(self.run_compare(), 0)
        self.assertFalse((self.out / "light-01-window.png").exists())
        self.assertFalse((self.out / "full/variant-removed.png").exists())
        self.assertEqual(unrelated.read_text(), "keep this")

    def test_output_cannot_overwrite_input(self):
        self.image(self.base)
        self.image(self.head)
        for out in (self.base, self.root, self.head / "diff"):
            with self.subTest(out=out):
                self.out = out
                self.assertEqual(self.run_compare(), 2)
                self.assertTrue((self.base / "light-01-window.png").exists())

    def test_legacy_missing_view_artifacts_are_cleared(self):
        self.image(self.base)
        self.image(self.head)
        (self.out / "full").mkdir(parents=True)
        self.image(self.out, "variant-old")
        self.image(self.out / "full", "variant-old")
        (self.out / "summary.json").write_text(json.dumps({
            "views": [], "only_in_head": [], "only_in_base": ["variant-old"]}))
        self.assertEqual(self.run_compare(), 0)
        self.assertFalse((self.out / "variant-old.png").exists())
        self.assertFalse((self.out / "full/variant-old.png").exists())

    def test_local_diff_panels_use_baseline_and_current_labels(self):
        self.image(self.base)
        self.image(self.head, colour="red")
        self.out.mkdir()
        labels = []
        def record_panels(panels, width):
            labels.extend(item[0][0] for item in panels)
            return Image.new("RGB", (width, 8))
        with patch.object(compare, "stack", side_effect=record_panels):
            compare.compare_view("light-01-window", self.base / "light-01-window.png",
                                 self.head / "light-01-window.png", self.out)
        self.assertIn("BEFORE  ·  baseline", labels)
        self.assertIn("AFTER  ·  current capture", labels)

    def test_failed_upgrade_comparison_does_not_install(self):
        from fxcss import install
        theme, profile = self.root / "theme", self.root / "profile"
        (theme / "chrome").mkdir(parents=True)
        (theme / "chrome/userChrome.css").write_text(":root { color: red; }")
        profile.mkdir()
        install.install_theme(theme, profile, "local", source={"kind": "local", "path": str(theme)})
        before = {str(p.relative_to(profile)): p.read_bytes() for p in profile.rglob("*") if p.is_file()}
        (theme / "chrome/userChrome.css").write_text(":root { color: blue; }")
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            stack.enter_context(patch.object(cli, "_choose_profile", return_value={"path": profile, "name": "test"}))
            stack.enter_context(patch.object(cli, "choose_firefox", return_value="/fake/firefox"))
            stack.enter_context(patch.object(core := cli.core, "Session"))
            stack.enter_context(patch.object(core, "capture_views"))
            stack.enter_context(patch.object(compare, "run", return_value=2))
            stack.enter_context(patch("tempfile.mkdtemp", return_value=str(self.root / "upgrade-output")))
            apply = stack.enter_context(patch.object(install, "install_theme"))
            self.assertEqual(cli.main(["upgrade", "--compare", "--yes"]), 2)
            apply.assert_not_called()
        after = {str(p.relative_to(profile)): p.read_bytes() for p in profile.rglob("*") if p.is_file()}
        self.assertEqual(after, before)


class DistinctChangeTests(unittest.TestCase):
    """One change in many views is pictured once and placed in the UI."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base, self.head, self.out = [self.root / n for n in ("base", "head", "out")]
        self.base.mkdir()
        self.head.mkdir()

    def pair(self, name, blocks, colour="red", size=(320, 160)):
        Image.new("RGB", size, "grey").save(self.base / f"{name}.png")
        head = Image.new("RGB", size, "grey")
        for box in blocks:
            head.paste(colour, box)
        head.save(self.head / f"{name}.png")

    def run_compare(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(compare.run(self.base, self.head, self.out, "test"), 0)
        return json.loads((self.out / "summary.json").read_text())

    def test_separate_regions_are_found_and_near_ones_join(self):
        mask = Image.new("L", (320, 160), 0)
        mask.paste(255, (8, 8, 40, 24))
        mask.paste(255, (48, 8, 60, 24))     # one empty cell away: same region
        mask.paste(255, (200, 100, 240, 140))
        self.assertEqual(compare.change_regions(mask), [(8, 8, 64, 24), (200, 96, 240, 144)])
        busy = Image.new("L", (320, 160), 0)
        for x in range(0, 320, 32):
            busy.paste(255, (x, 0, x + 4, 4))
        self.assertEqual(compare.change_regions(busy), [(0, 0, 296, 8)])

    def test_the_same_change_in_many_views_is_pictured_once(self):
        strip = [(0, 0, 320, 16)]
        self.pair("light-01-window", strip)
        self.pair("dark-01-window", strip, colour="blue")   # other colours, same pixels
        self.pair("extra-09-compact", strip + [(100, 80, 140, 120)])
        self.pair("variant-theme-nord", [(0, 100, 320, 116)])
        layout = {name: {"width": 160, "height": 80, "boxes": [
            ["Tabs", 0, 0, 160, 10], ["Toolbars", 0, 10, 160, 20], ["Content", 0, 30, 160, 50]]}
            for name in ("light-01-window", "dark-01-window", "extra-09-compact", "variant-theme-nord")}
        (self.head / capture.LAYOUT).write_text(json.dumps(layout))
        report = self.run_compare()
        changes = report["changes"]
        self.assertEqual([(c["category"], c["views"]) for c in changes], [
            ("Tabs", ["light-01-window", "dark-01-window", "extra-09-compact"]),
            ("Content", ["extra-09-compact"]),
            ("Content", ["variant-theme-nord"])])
        self.assertEqual(len(report["changed_views"]), 4)
        for change in changes:
            self.assertTrue((self.out / change["image"]).is_file())
        self.assertEqual({v["view"]: v["changes"] for v in report["views"]}["extra-09-compact"], [1, 2])
        # Every changed view keeps its own full comparison.
        for view in report["changed_views"]:
            self.assertTrue((self.out / view["image"]).is_file())

    def test_a_moved_page_is_one_change_whatever_page_each_view_shows(self):
        for name, text in (("light-01-window", (40, 60, 200, 80)), ("extra-04-audio", (60, 90, 280, 100)),
                           ("extra-10-sidebar-bookmarks", (120, 70, 300, 120))):
            base = Image.new("RGB", (320, 160), "white")
            base.paste("black", text)
            base.paste("grey", (0, 0, 320, 30))
            head = Image.new("RGB", (320, 160), "white")
            head.paste("black", (text[0], text[1] - 2, text[2], text[3] - 2))
            head.paste("grey", (0, 0, 320, 30))
            base.save(self.base / f"{name}.png")
            head.save(self.head / f"{name}.png")
        (self.head / "render-info.json").write_text(json.dumps({"dpr": 2}))
        page = {"width": 320, "height": 160, "boxes": [["Content", 0, 30, 320, 130]]}
        (self.head / capture.LAYOUT).write_text(json.dumps(
            {name: page for name in ("light-01-window", "extra-04-audio", "extra-10-sidebar-bookmarks")}))
        change, = self.run_compare()["changes"]
        self.assertEqual(change["views"], ["light-01-window", "extra-04-audio",
                                           "extra-10-sidebar-bookmarks"])
        self.assertEqual(change["shift"], [0, -1])
        self.assertEqual(change["moved"], "moved up 1px")

    def test_regions_in_one_landmark_are_one_change(self):
        self.pair("light-01-window", [(10, 40, 30, 50), (200, 40, 220, 50), (100, 120, 140, 150)])
        (self.head / capture.LAYOUT).write_text(json.dumps({"light-01-window": {
            "width": 320, "height": 160, "boxes": [["Toolbars", 0, 32, 320, 24],
                                                   ["Content", 0, 56, 320, 104]]}}))
        changes = self.run_compare()["changes"]
        self.assertEqual([(c["category"], c["box"]) for c in changes],
                         [("Toolbars", [8, 40, 224, 56]), ("Content", [96, 120, 144, 152])])

    def test_without_a_layout_a_change_takes_its_view_category(self):
        self.pair("light-08-app-menu", [(10, 10, 60, 40)], size=(200, 300))
        self.pair("light-11-settings", [(10, 10, 60, 40)])
        changes = self.run_compare()["changes"]
        self.assertEqual([c["category"] for c in changes], ["Panels", "Settings"])

    def test_a_rerun_removes_change_pictures_it_no_longer_draws(self):
        self.pair("light-01-window", [(0, 0, 320, 16)])
        self.pair("light-03-findbar", [(0, 140, 320, 160)])
        self.assertEqual(len(self.run_compare()["changes"]), 2)
        self.pair("light-03-findbar", [])
        self.assertEqual(len(self.run_compare()["changes"]), 1)
        self.assertFalse((self.out / "change-02.png").exists())

    def test_change_pictures_are_cropped_from_full_resolution(self):
        self.pair("light-01-window", [(1200, 600, 1240, 640)], size=(2560, 960))
        change, = self.run_compare()["changes"]
        with Image.open(self.out / change["image"]) as picture:
            self.assertLess(picture.width, 960)
        self.assertEqual(change["frame"], [960, 360])

    def test_views_new_to_fxcss_do_not_fail_an_older_baseline(self):
        summary = {"only_in_base": [], "only_in_head": ["light-08-app-menu"],
                   "views": [{"percent": 0.0}]}
        self.assertEqual(check.comparison_status(summary, 0), 0)
        summary["only_in_head"].append("variant-added")
        self.assertEqual(check.comparison_status(summary, 0), 1)


class ProjectCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.theme = Path(self.temp.name) / "theme"
        (self.theme / "chrome").mkdir(parents=True)
        (self.theme / "chrome/userChrome.css").write_text(":root { color: red; }")
        self.colour, self.audit_status, self.dot = "grey", 0, False
        self.views = capture.expected_views()
        self.audit_args = []
        self.addCleanup(patch.stopall)
        patch.object(compare, "PANEL_WIDTH", 16).start()
        self.find = patch.object(check.core, "find_firefox", side_effect=lambda name: "/fake/" + name).start()
        patch.object(cli, "cmd_audit", side_effect=self.audit).start()
        patch.object(cli, "cmd_shot", side_effect=self.capture).start()

    def audit(self, args):
        self.audit_args.append(args)
        print("audit details")
        return self.audit_status

    def capture(self, args):
        args.out.mkdir()
        for view in self.views:
            image = Image.new("RGB", (16, 8), self.colour)
            if self.dot:
                image.putpixel((0, 0), (255, 0, 0))  # 1 of 128 pixels, about 0.8%
            image.save(args.out / f"{view}.png")
        capture.write_coverage(args.out, {"version": "150", "os": "test"},
            capture.expected_views(check.core.parse_variant_spec(args.variants, check.core.find_variant_sheets(args.theme))))
        print("capture details")
        return 0

    def config(self, **settings):
        (self.theme / ".fxcss.json").write_text(json.dumps(settings))

    def run_check(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(["check", "--theme", str(self.theme), *args])

    def summaries(self):
        return list((self.theme / ".fxcss/checks").glob("run-*/summary.json"))

    def test_no_config_runs_audit_and_capture_and_explains_no_comparison(self):
        self.assertEqual(self.run_check(), 0)
        path, = self.summaries()
        data = json.loads(path.read_text())
        self.assertEqual(data["browsers"][0]["comparison_note"], "no baseline configured")
        report = path.with_name("report.md").read_text()
        self.assertIn("Result: **passed; visual comparison not run**", report)
        self.assertIn("Selector audit: **passed**", report)
        self.assertIn("Screenshot capture: **complete**", report)
        self.assertIn("audit.txt", report)
        self.assertIn("Captured views (34)", report)
        self.assertIn("[Browser window (light) (`light-01-window`)](stable/shots/light-01-window.png)", report)
        self.assertTrue(self.audit_args[0].strict)

    def test_baseline_creation_then_identical_comparison(self):
        self.config(baseline=".fxcss/baseline", max_changed_percent=0)
        self.assertEqual(self.run_check("--update-baseline"), 0)
        self.assertTrue((self.theme / ".fxcss/baseline/stable/light-01-window.png").exists())
        self.assertEqual(self.run_check(), 0)
        reports = [json.loads(p.read_text()) for p in self.summaries()]
        current, = [r for r in reports if not r["baseline_updated"]]
        self.assertFalse(current["browsers"][0]["comparison"]["any_change"])

    def test_comparison_shows_baseline_and_current_capture_environments(self):
        self.config(baseline=".fxcss/baseline")
        self.assertEqual(self.run_check("--update-baseline"), 0)
        baseline_coverage = self.theme / ".fxcss/baseline/stable/capture-coverage.json"
        data = json.loads(baseline_coverage.read_text())
        data["browser"]["version"] = "149"
        baseline_coverage.write_text(json.dumps(data))
        self.assertEqual(self.run_check(), 0)
        current, = [p for p in self.summaries()
                    if not json.loads(p.read_text())["baseline_updated"]]
        report = current.with_name("report.md").read_text()
        self.assertIn("Baseline: Firefox 149 on test", report)
        self.assertIn("Current: Firefox 150 on test", report)
        self.assertIn("Capture environments differ in Firefox version", report)

    def test_visual_policy_and_command_line_overrides(self):
        self.config(baseline=".fxcss/baseline", max_changed_percent=0, strict_vars=True)
        self.assertEqual(self.run_check("--update-baseline"), 0)
        self.colour = "red"
        self.assertEqual(self.run_check(), 1)
        self.audit_status = 0
        self.assertEqual(self.run_check("--max-changed-percent", "100", "--no-strict-vars"), 0)
        self.assertFalse(self.audit_args[-1].strict_vars)

    def test_saved_views_and_minimum_scope_the_comparison(self):
        self.config(baseline=".fxcss/baseline", views=["light-*"], min_changed_percent=0,
                    max_changed_percent=0)
        self.assertEqual(self.run_check("--update-baseline"), 0)
        self.dot = True
        self.assertEqual(self.run_check(), 1)
        self.assertEqual(self.run_check("--min-changed-percent", "5",
                                        "--max-changed-percent", "5"), 0)
        path, = [p for p in self.summaries()
                 if json.loads(p.read_text())["settings"]["min_changed_percent"] == 5]
        comparison = json.loads(path.read_text())["browsers"][0]["comparison"]
        light = {v for v in capture.expected_views() if v.startswith("light-")}
        self.assertEqual({v["view"] for v in comparison["views"]}, light)
        self.assertEqual(comparison["changed_views"], [])
        self.assertEqual(len(comparison["below_threshold"]), len(light))
        report = path.with_name("report.md").read_text()
        self.assertIn(f"0 changed of {len(light)} selected views", report)
        self.assertIn("Changed by less than 5% and not pictured: Browser window (`light-01-window`, ",
                      report)

    def test_pixel_changes_are_advisory_without_threshold(self):
        self.config(baseline=".fxcss/baseline")
        self.assertEqual(self.run_check("--update-baseline"), 0)
        self.colour = "red"
        self.assertEqual(self.run_check(), 0)
        reports = [p.with_name("report.md").read_text() for p in self.summaries()]
        self.assertTrue(any("Result: **passed; visual changes to review**" in r
                            and "(advisory; no configured limit was exceeded)" in r
                            for r in reports))

    def test_missing_views_fail_even_without_a_pixel_threshold(self):
        self.config(baseline=".fxcss/baseline")
        (self.theme / "optional").mkdir()
        (self.theme / "optional/optional.css").write_text(":root { color: red; }")
        self.views.append("variant-optional")
        self.assertEqual(self.run_check("--update-baseline"), 0)
        self.views.pop()
        (self.theme / "optional/optional.css").unlink()
        self.assertEqual(self.run_check(), 1)
        self.assertTrue(any("Missing views" in p.with_name("report.md").read_text()
                            for p in self.summaries()))

    def test_configured_but_missing_baseline_is_an_error(self):
        self.config(baseline=".fxcss/baseline")
        self.assertEqual(self.run_check(), 2)
        path, = self.summaries()
        self.assertIn("--update-baseline", path.with_name("report.md").read_text())
        self.assertFalse((self.theme / ".fxcss/baseline").exists())

    def test_findings_do_not_replace_baselines(self):
        self.config(baseline=".fxcss/baseline")
        self.assertEqual(self.run_check("--update-baseline"), 0)
        image = self.theme / ".fxcss/baseline/stable/light-01-window.png"
        before = image.read_bytes()
        self.colour, self.audit_status = "red", 1
        self.assertEqual(self.run_check("--update-baseline"), 1)
        self.assertEqual(image.read_bytes(), before)

    def test_unavailable_browser_is_reported_and_other_browsers_still_run(self):
        self.config(firefox=["beta", "stable"], baseline=".fxcss/baseline")
        def find(name):
            if name == "beta":
                raise SystemExit("Beta is not installed")
            return "/fake/stable"
        self.find.side_effect = find
        self.assertEqual(self.run_check("--update-baseline"), 2)
        path, = self.summaries()
        result = json.loads(path.read_text())
        self.assertEqual([r["exit_code"] for r in result["browsers"]], [2, 0])
        self.assertFalse((self.theme / ".fxcss/baseline").exists())

    def test_failed_capture_produces_error_report(self):
        with patch.object(cli, "cmd_shot", return_value=2):
            self.assertEqual(self.run_check(), 2)
        path, = self.summaries()
        self.assertIn("capture failed", path.with_name("report.md").read_text())

    def test_failed_capture_reports_cause_failed_views_and_available_images(self):
        def fail_after_first_view(args):
            args.out.mkdir()
            Image.new("RGB", (16, 8), "grey").save(args.out / "light-01-window.png")
            capture.write_coverage(args.out, {"version": "150", "os": "test"},
                                   capture.expected_views())
            print("error: Firefox lost its window", file=sys.stderr)
            return 2
        with patch.object(cli, "cmd_shot", side_effect=fail_after_first_view):
            self.assertEqual(self.run_check(), 2)
        path, = self.summaries()
        report = path.with_name("report.md").read_text()
        self.assertIn("Failed view `light-02-urlbar`", report)
        self.assertIn("Firefox lost its window", report)
        self.assertIn("Captured views (1, partial run)", report)
        self.assertIn("(stable/shots/light-01-window.png)", report)

    def test_interruption_writes_report_and_stops_other_browsers(self):
        self.config(firefox=["stable", "beta"], baseline=".fxcss/baseline")
        def interrupt_after_first_view(args):
            args.out.mkdir()
            Image.new("RGB", (16, 8), "grey").save(args.out / "light-01-window.png")
            capture.write_coverage(args.out, {"version": "150", "os": "test"},
                                   capture.expected_views(), interrupted=True)
            raise KeyboardInterrupt
        with patch.object(cli, "cmd_shot", side_effect=interrupt_after_first_view):
            self.assertEqual(self.run_check("--update-baseline"), 2)
        path, = self.summaries()
        summary = json.loads(path.read_text())
        self.assertTrue(summary["interrupted"])
        self.assertEqual([r["firefox"] for r in summary["browsers"]], ["stable"])
        report = path.with_name("report.md").read_text()
        self.assertIn("Result: **interrupted**", report)
        self.assertIn("partial (1 captured, 33 unfinished)", report)
        self.assertIn("33 views did not finish", report)
        self.assertNotIn("Failed view", report)
        self.assertIn("(stable/shots/light-01-window.png)", report)
        self.assertIn("Run `fxcss check` again", report)
        self.assertFalse((self.theme / ".fxcss/baseline").exists())

    def test_interrupted_baseline_update_reports_uncertain_state(self):
        self.config(baseline=".fxcss/baseline")
        with patch.object(check, "update_baseline", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_check("--update-baseline"), 2)
        path, = self.summaries()
        report = path.with_name("report.md").read_text()
        self.assertIn("Result: **interrupted**", report)
        self.assertIn("Inspect the baseline directory", report)
        self.assertNotIn("The baseline was left unchanged", report)

    def test_check_streams_capture_progress_and_keeps_detail_log(self):
        def capture_with_progress(args):
            result = self.capture(args)
            print("  captured light-01-window.png (1 KB)", flush=True)
            return result
        output = io.StringIO()
        with patch.object(cli, "cmd_shot", side_effect=capture_with_progress):
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(["check", "--theme", str(self.theme)]), 0)
        self.assertIn("selector audit: running", output.getvalue())
        self.assertIn("view 1/34: light-01-window captured", output.getvalue())
        self.assertIn("34/34 views accounted for; 34 captured", output.getvalue())
        path, = self.summaries()
        self.assertIn("captured light-01-window", (path.parent / "stable/capture.txt").read_text())

    def test_unsupported_views_advance_progress_and_are_counted_at_completion(self):
        def capture_with_unsupported(args):
            args.out.mkdir()
            missing = {"light-04-dialog", "dark-04-dialog"}
            for view in capture.expected_views():
                if view not in missing:
                    Image.new("RGB", (16, 8), "grey").save(args.out / f"{view}.png")
            capture.write_coverage(args.out, {"version": "150", "os": "test"},
                                   capture.expected_views(),
                                   {name: "no in-window modal prompts" for name in missing})
            print("  captured light-01-window.png (1 KB)", flush=True)
            print("  unsupported light-04-dialog: no in-window modal prompts", flush=True)
            return 0
        output = io.StringIO()
        with patch.object(cli, "cmd_shot", side_effect=capture_with_unsupported):
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(["check", "--theme", str(self.theme)]), 0)
        self.assertIn("view 2/34: light-04-dialog unsupported", output.getvalue())
        self.assertIn("34/34 views accounted for; 32 captured, 2 unsupported", output.getvalue())

    def test_invalid_config_fails_before_launching(self):
        cases = [dict(unknown=True), dict(firefox=[]), dict(firefox=["stable", "release"]),
                 dict(strict="false"), dict(max_changed_percent=True), dict(max_changed_percent=float("nan")),
                 dict(max_changed_percent=-1), dict(baseline=".fxcss/checks"), dict(out="."),
                 dict(out="chrome/results"), dict(baseline=".."), dict(variants="missing"),
                 dict(max_changed_percent=1), dict(toolbar="-"), dict(out=None),
                 dict(views="light-*,nothing-*"), dict(views=5), dict(views="variant-*"),
                 dict(min_changed_percent=-1), dict(min_changed_percent="1"),
                 dict(baseline=".fxcss/baseline", min_changed_percent=1, max_changed_percent=0.5)]
        for settings in cases:
            with self.subTest(settings=settings):
                self.config(**settings)
                self.assertEqual(self.run_check(), 2)
        self.find.assert_not_called()

    def test_explicit_missing_or_malformed_config_is_an_error(self):
        self.assertEqual(self.run_check("--config", str(self.theme / "missing.json")), 2)
        (self.theme / ".fxcss.json").write_text("{")
        self.assertEqual(self.run_check(), 2)
        self.find.assert_not_called()

    def test_update_requires_a_baseline_path(self):
        self.assertEqual(self.run_check("--update-baseline"), 2)
        self.find.assert_not_called()

    def test_browser_paths_are_resolved_from_the_theme(self):
        self.config(firefox="browsers/firefox")
        self.assertEqual(self.run_check(), 0)
        self.find.assert_called_once_with(str((self.theme / "browsers/firefox").resolve()))

    def test_cli_browser_override_uses_only_the_selected_channel(self):
        self.config(firefox=["stable", "beta"])
        self.assertEqual(self.run_check("--firefox", "beta"), 0)
        self.find.assert_called_once_with("beta")

    def test_partial_capture_cannot_create_or_replace_baseline(self):
        self.config(baseline=".fxcss/baseline")
        self.views = ["light-01-window"]
        self.assertEqual(self.run_check("--update-baseline"), 2)
        self.assertFalse((self.theme / ".fxcss/baseline").exists())
        self.views = capture.expected_views()
        self.assertEqual(self.run_check("--update-baseline"), 0)
        old = (self.theme / ".fxcss/baseline/stable/light-01-window.png").read_bytes()
        self.views, self.colour = ["light-01-window"], "red"
        self.assertEqual(self.run_check("--update-baseline"), 2)
        self.assertEqual((self.theme / ".fxcss/baseline/stable/light-01-window.png").read_bytes(), old)

    def test_empty_capture_does_not_pass_or_write_a_baseline(self):
        self.config(baseline=".fxcss/baseline")
        self.views = []
        self.assertEqual(self.run_check("--update-baseline"), 2)
        self.assertFalse((self.theme / ".fxcss/baseline").exists())

    def test_new_command_explains_missing_pillow(self):
        import subprocess, sys
        result = subprocess.run([sys.executable, "-c",
            "import sys; sys.modules['PIL'] = None; from fxcss.cli import main; "
            "sys.exit(main(['check']))"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("needs Pillow", result.stderr)


class BaselineUpdateTests(unittest.TestCase):
    def test_unmanaged_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "notes").write_text("keep")
            with self.assertRaises(ValueError):
                check.update_baseline(root, {})
            self.assertEqual((root / "notes").read_text(), "keep")

    def test_failed_activation_restores_previous_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            baseline, shots = root / "baseline", root / "shots"
            shots.mkdir()
            (shots / "view.png").write_bytes(b"old")
            check.update_baseline(baseline, {"stable": shots, "beta": shots})
            (shots / "view.png").write_bytes(b"new")
            rename = Path.rename
            def fail_next(path, target):
                if path.name == "next":
                    raise OSError("activation failed")
                return rename(path, target)
            with patch.object(Path, "rename", fail_next), self.assertRaises(OSError):
                check.update_baseline(baseline, {"stable": shots})
            self.assertEqual((baseline / "stable/view.png").read_bytes(), b"old")
            check.update_baseline(baseline, {"stable": shots})
            self.assertEqual((baseline / "stable/view.png").read_bytes(), b"new")
            self.assertEqual((baseline / "beta/view.png").read_bytes(), b"old")

    def test_failed_restore_keeps_recoverable_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            baseline, shots = root / "baseline", root / "shots"
            shots.mkdir()
            (shots / "view.png").write_bytes(b"old")
            check.update_baseline(baseline, {"stable": shots})
            rename = Path.rename
            def fail(path, target):
                if path.name in ("next", "previous"):
                    raise OSError("disk unavailable")
                return rename(path, target)
            with patch.object(Path, "rename", fail), self.assertRaisesRegex(RuntimeError, "kept at"):
                check.update_baseline(baseline, {"stable": shots})
            recovery, = root.glob(".fxcss-baseline-*/previous/stable/view.png")
            self.assertEqual(recovery.read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
