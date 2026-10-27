"""Coverage must prove a complete run, including when old images exist."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from fxcss import capture, core


class CaptureCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def png(self, name):
        Image.new("RGB", (4, 4)).save(self.root / f"{name}.png")

    def test_stale_images_cannot_hide_missing_capture(self):
        for name in capture.expected_views():
            self.png(name)
        def partial(session, out, modes, variants, toolbar, coverage):
            self.png("light-01-window")
            return {}
        with patch.object(core, "_capture_views", partial), self.assertRaisesRegex(ValueError, "incomplete capture"):
            core.capture_views(None, self.root)
        self.assertFalse((self.root / "dark-01-window.png").exists())
        self.assertTrue((self.root / capture.REPORT).is_file())

    def test_interruption_records_unfinished_views_without_accepting_the_capture(self):
        def interrupt(session, out, modes, variants, toolbar, coverage):
            self.png("light-01-window")
            raise KeyboardInterrupt
        with patch.object(core, "_capture_views", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            core.capture_views(None, self.root)
        views = json.loads((self.root / capture.REPORT).read_text())["views"]
        self.assertEqual(views["light-01-window"]["status"], "captured")
        self.assertEqual(views["light-02-urlbar"]["status"], "not_completed")
        self.assertNotIn("failed", {view["status"] for view in views.values()})
        with self.assertRaisesRegex(ValueError, "incomplete capture"):
            capture.validate_coverage(self.root)

    def test_only_explicitly_unsupported_capabilities_are_accepted(self):
        names = capture.expected_views()
        for name in names:
            if name != "extra-14-vertical-tabs":
                self.png(name)
        capture.write_coverage(self.root, {"version": "128.10"}, names,
                               {"extra-14-vertical-tabs": "Firefox before 133"})
        capture.validate_coverage(self.root)
        capture.write_coverage(self.root, {"version": "150"}, names,
                               {"extra-14-vertical-tabs": "Firefox before 133"})
        with self.assertRaisesRegex(ValueError, "incomplete capture"):
            capture.validate_coverage(self.root)

    def test_a_baseline_from_an_older_fxcss_may_lack_newer_views(self):
        old = [n for n in capture.expected_views() if n not in capture.new_since("0.24.0")]
        for name in old:
            self.png(name)
        capture.write_coverage(self.root, {"version": "158.0"}, old)
        path = self.root / capture.REPORT
        data = json.loads(path.read_text())
        data["fxcss"] = "0.24.0"
        path.write_text(json.dumps(data))
        capture.validate_coverage(self.root)
        # A capture this fxcss makes must have every view, and so must a
        # baseline that records a version which already captured them.
        with self.assertRaisesRegex(ValueError, "inventory"):
            capture.validate_coverage(self.root, capture.expected_views())
        del data["fxcss"]
        path.write_text(json.dumps(data))
        capture.validate_coverage(self.root)
        data["fxcss"] = "0.25.0"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "inventory"):
            capture.validate_coverage(self.root)

    def test_screen_capture_reasons_only_excuse_views_that_need_one(self):
        names = capture.expected_views()
        for name in names:
            self.png(name)
        reason = "screen capture cannot see the Firefox window"
        skipped = {"light-08-app-menu": reason, "dark-07-search-menu": reason}
        for name in skipped:
            (self.root / f"{name}.png").unlink()
        capture.write_coverage(self.root, {"version": "158.0"}, names, skipped)
        capture.validate_coverage(self.root)
        (self.root / "light-11-settings.png").unlink()
        capture.write_coverage(self.root, {"version": "158.0"}, names,
                               {**skipped, "light-11-settings": reason})
        with self.assertRaisesRegex(ValueError, "light-11-settings"):
            capture.validate_coverage(self.root)

    def test_every_view_has_a_known_category(self):
        for name in capture.expected_views(["x"]):
            with self.subTest(name=name):
                self.assertIn(capture.category_for(name), capture.CATEGORIES)
        for category, _ in capture.LAYOUT_LANDMARKS:
            self.assertIn(category, capture.CATEGORIES)
        from fxcss import catalogue
        for area, slug, *_ in catalogue.LANDMARKS:
            with self.subTest(landmark=slug):
                self.assertIn(area, capture.CATEGORIES)

    def test_audio_capture_requires_the_actual_indicator_attributes(self):
        m = Mock()
        for states in (({},), ({"playing": True, "muted": False}, {"playing": False, "muted": True})):
            with self.subTest(states=states):
                remaining = iter(states)
                m.script.side_effect = lambda code, *args: next(remaining) if code == core.AUDIO_STATE else True
                with patch.object(core.time, "sleep"), patch.object(core, "_shot") as shot:
                    with self.assertRaisesRegex(RuntimeError, "audio indicator state"):
                        core._capture_audio_views(m, self.root)
                    self.assertEqual(shot.call_count, len(states) - 1)
                    m.script.assert_any_call(core.SET_AUDIO_STATE, [None])

    def test_audio_capture_records_both_distinct_states(self):
        m = Mock()
        states = iter(({"playing": True, "muted": False}, {"playing": True, "muted": True}))
        m.script.side_effect = lambda code, *args: next(states) if code == core.AUDIO_STATE else True
        with patch.object(core.time, "sleep"), patch.object(core, "_shot") as shot:
            core._capture_audio_views(m, self.root)
        self.assertEqual([call.args[2] for call in shot.call_args_list], ["extra-04-audio", "extra-05-muted"])

    def test_malformed_browser_metadata_reports_a_validation_error(self):
        capture.write_coverage(self.root, {}, capture.expected_views())
        path = self.root / capture.REPORT
        data = json.loads(path.read_text())
        data["browser"] = []
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "browser metadata"):
            capture.validate_coverage(self.root)

    def test_incomplete_inventory_and_failed_states_are_rejected(self):
        self.png("light-01-window")
        capture.write_coverage(self.root, {}, ["light-01-window"])
        with self.assertRaisesRegex(ValueError, "inventory"):
            capture.validate_coverage(self.root)
        for name in capture.expected_views():
            self.png(name)
        capture.write_coverage(self.root, {}, capture.expected_views(),
                               failed={"extra-04-audio": "not playing"})
        with self.assertRaisesRegex(ValueError, "extra-04-audio"):
            capture.validate_coverage(self.root)


if __name__ == "__main__":
    unittest.main()
