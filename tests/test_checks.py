"""Regression checks for comparison integrity and the combined project check."""

import contextlib
import io
import json
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

    def run_compare(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return compare.run(self.base, self.head, self.out, "test")

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


class ProjectCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.theme = Path(self.temp.name) / "theme"
        (self.theme / "chrome").mkdir(parents=True)
        (self.theme / "chrome/userChrome.css").write_text(":root { color: red; }")
        self.colour, self.audit_status = "grey", 0
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
            Image.new("RGB", (16, 8), self.colour).save(args.out / f"{view}.png")
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
        self.assertIn("audit.txt", report)
        self.assertIn("Browser captures", report)
        self.assertTrue(self.audit_args[0].strict)

    def test_baseline_creation_then_identical_comparison(self):
        self.config(baseline=".fxcss/baseline", max_changed_percent=0)
        self.assertEqual(self.run_check("--update-baseline"), 0)
        self.assertTrue((self.theme / ".fxcss/baseline/stable/light-01-window.png").exists())
        self.assertEqual(self.run_check(), 0)
        reports = [json.loads(p.read_text()) for p in self.summaries()]
        current, = [r for r in reports if not r["baseline_updated"]]
        self.assertFalse(current["browsers"][0]["comparison"]["any_change"])

    def test_visual_policy_and_command_line_overrides(self):
        self.config(baseline=".fxcss/baseline", max_changed_percent=0, strict_vars=True)
        self.assertEqual(self.run_check("--update-baseline"), 0)
        self.colour = "red"
        self.assertEqual(self.run_check(), 1)
        self.audit_status = 0
        self.assertEqual(self.run_check("--max-changed-percent", "100", "--no-strict-vars"), 0)
        self.assertFalse(self.audit_args[-1].strict_vars)

    def test_pixel_changes_are_advisory_without_threshold(self):
        self.config(baseline=".fxcss/baseline")
        self.assertEqual(self.run_check("--update-baseline"), 0)
        self.colour = "red"
        self.assertEqual(self.run_check(), 0)

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

    def test_invalid_config_fails_before_launching(self):
        cases = [dict(unknown=True), dict(firefox=[]), dict(firefox=["stable", "release"]),
                 dict(strict="false"), dict(max_changed_percent=True), dict(max_changed_percent=float("nan")),
                 dict(max_changed_percent=-1), dict(baseline=".fxcss/checks"), dict(out="."),
                 dict(out="chrome/results"), dict(baseline=".."), dict(variants="missing"),
                 dict(max_changed_percent=1), dict(toolbar="-"), dict(out=None)]
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
