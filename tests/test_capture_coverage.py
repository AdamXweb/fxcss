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

    def test_interruption_still_records_failed_views(self):
        with patch.object(core, "_capture_views", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            core.capture_views(None, self.root)
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
