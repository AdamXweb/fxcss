"""Comparisons measure changes at capture resolution, not at the panel width."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops
from fxcss import compare


GREY = (128, 128, 128)


class AntialiasNoiseTests(unittest.TestCase):
    @staticmethod
    def glyph():
        # A 5x5 crop of the first letter in the Windows CI modal prompt.
        # Only its centre pixel varies between otherwise identical captures.
        ink, paper = (24, 14, 48), (247, 246, 251)
        edge, mid, pale = (72, 63, 91), (155, 151, 168), (216, 213, 222)
        base = Image.new("RGB", (5, 5))
        base.putdata([
            pale, mid, (98, 92, 116), paper, paper,
            paper, paper, ink, paper, paper,
            paper, paper, edge, mid, paper,
            ink, ink, ink, edge, paper,
            paper, paper, paper, ink, pale,
        ])
        head = base.copy()
        head.putpixel((2, 2), (98, 92, 116))
        return base, head

    def test_windows_dialog_coverage_noise_is_identical_in_both_directions(self):
        base, head = self.glyph()
        for before, after in ((base, head), (head, base)):
            changed, total, mask = compare.diff_stats(before, after)
            self.assertEqual((changed, total, mask.getbbox()), (0, 25, None))

    def test_a_single_pixel_dot_on_a_flat_background_is_a_change(self):
        base = Image.new("RGB", (5, 5), GREY)
        head = base.copy()
        head.putpixel((2, 2), (158, 158, 158))
        self.assertEqual(compare.diff_stats(base, head)[0], 1)

    def test_recoloured_or_high_contrast_edge_pixels_are_changes(self):
        for colour in ((72, 92, 91), (200, 199, 208), (24, 14, 48)):
            with self.subTest(colour=colour):
                base, head = self.glyph()
                head.putpixel((2, 2), colour)
                self.assertEqual(compare.diff_stats(base, head)[0], 1)

    def test_neighbour_changes_keep_the_edge_pixel_in_the_diff(self):
        for delta in (1, 30):
            with self.subTest(delta=delta):
                base, head = self.glyph()
                head.putpixel((1, 2), tuple(c - delta for c in base.getpixel((1, 2))))
                changed, _, mask = compare.diff_stats(base, head)
                self.assertEqual(changed, 1 if delta == 1 else 2)
                self.assertEqual(mask.getpixel((2, 2)), 255)

    def test_adding_a_solid_foreground_pixel_is_a_change(self):
        base, head = self.glyph()
        base.putpixel((2, 2), (49, 40, 70))
        head.putpixel((2, 2), (24, 14, 48))
        self.assertEqual(compare.diff_stats(base, head)[0], 1)

    def test_small_images_and_boundary_pixels_remain_changes(self):
        for size, point in (((1, 1), (0, 0)), ((2, 2), (1, 1)), ((5, 5), (0, 2))):
            with self.subTest(size=size):
                base = Image.new("RGB", size, GREY)
                head = base.copy()
                head.putpixel(point, (158, 158, 158))
                self.assertEqual(compare.diff_stats(base, head)[0], 1)


class CaptureResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base, self.head, self.out = [self.root / n for n in ("base", "head", "out")]
        self.base.mkdir()
        self.head.mkdir()

    def run_compare(self, **options):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(compare.run(self.base, self.head, self.out, "test", **options), 0)
        return json.loads((self.out / "summary.json").read_text())

    def border(self, size, top, thickness, delta, name="light-01-window", dpr=1):
        """A plain capture whose head gains a faint horizontal border."""
        Image.new("RGB", size, GREY).save(self.base / f"{name}.png")
        head = Image.new("RGB", size, GREY)
        head.paste(tuple(c + delta for c in GREY), (0, top, size[0], top + thickness))
        head.save(self.head / f"{name}.png")
        (self.head / "render-info.json").write_text(json.dumps({"dpr": dpr}))

    def assert_border_reported(self, report, size, top, thickness):
        view, = report["views"]
        self.assertEqual(view["changed_pixels"], size[0] * thickness)
        self.assertEqual(view["total_pixels"], size[0] * size[1])
        self.assertAlmostEqual(view["percent"], 100 * thickness / size[1], places=3)
        self.assertEqual(view["image"], "light-01-window.png")
        self.assertTrue(report["any_change"])
        change, = report["changes"]
        self.assertEqual(change["views"], ["light-01-window"])
        self.assertNotIn("moved", change)
        # Box and frame share the panel's coordinates, and the box holds the line.
        scale = change["frame"][0] / size[0]
        self.assertEqual(change["frame"], [960, round(size[1] * scale)])
        self.assertLessEqual(change["box"][1], top * scale)
        self.assertGreaterEqual(change["box"][3], (top + thickness) * scale)

        with Image.open(self.out / view["image"]) as picture:
            panels = self.panels(picture)
        self.assertEqual({p.size for p in panels}, {(960, round(size[1] * scale))})
        self.assertTrue(self.highlighted(panels[2]), "the changed panel must show the border")
        with Image.open(self.out / change["image"]) as picture:
            self.assertTrue(self.highlighted(self.panels(picture)[2]))

    def panels(self, picture):
        """The before, after and changed panels of a stacked comparison."""
        rgb = picture.convert("RGB")
        height = (rgb.height - compare.GAP) // 3 - compare.LABEL_HEIGHT - compare.GAP
        tops = [compare.GAP + i * (compare.LABEL_HEIGHT + height + compare.GAP) + compare.LABEL_HEIGHT
                for i in range(3)]
        return [rgb.crop((0, t, rgb.width, t + height)) for t in tops]

    def highlighted(self, panel):
        hot = Image.new("RGB", panel.size, compare.HIGHLIGHT)
        delta = ImageChops.difference(panel, hot).convert("L")
        return delta.point(lambda v: 255 if v < 8 else 0).getbbox() is not None

    def red(self, panel):
        r, g, b = panel.split()
        bright = r.point(lambda v: 255 if v > 200 else 0)
        dark = ImageChops.multiply(g.point(lambda v: 255 if v < 60 else 0),
                                   b.point(lambda v: 255 if v < 60 else 0))
        return ImageChops.multiply(bright, dark).getbbox()

    def test_a_one_pixel_border_on_a_wide_capture_is_a_change(self):
        size = (1280, 400)
        self.border(size, top=101, thickness=1, delta=30)
        self.assert_border_reported(self.run_compare(), size, 101, 1)

    def test_isolated_dialog_noise_does_not_produce_a_change_report(self):
        before, after = AntialiasNoiseTests.glyph()
        for folder, patch in ((self.base, before), (self.head, after)):
            image = Image.new("RGB", (1280, 400), GREY)
            image.paste(patch, (480, 172))
            image.save(folder / "light-04-dialog.png")
        report = self.run_compare()
        self.assertFalse(report["any_change"])
        self.assertEqual(report["changed_views"], [])
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["views"][0]["changed_pixels"], 0)

    def test_a_one_css_pixel_border_at_dpr_2_is_a_change(self):
        size = (2560, 800)
        for delta in (30, 40):
            with self.subTest(delta=delta):
                self.border(size, top=202, thickness=2, delta=delta, dpr=2)
                self.assert_border_reported(self.run_compare(), size, 202, 2)

    def test_a_faint_line_is_still_measured_against_the_minimum(self):
        size = (1280, 400)
        self.border(size, top=101, thickness=1, delta=30)
        report = self.run_compare(min_changed_percent=0.5)
        self.assertEqual([v["view"] for v in report["below_threshold"]], ["light-01-window"])
        self.assertFalse(report["any_change"])
        self.assertEqual(report["changes"], [])
        self.assertFalse((self.out / "light-01-window.png").exists())

    def test_sub_threshold_noise_is_still_identical(self):
        size = (1280, 400)
        self.border(size, top=101, thickness=1, delta=compare.NOISE_THRESHOLD - 1)
        view, = self.run_compare()["views"]
        self.assertEqual((view["changed_pixels"], view["image"]), (0, None))

    def test_differently_sized_captures_share_their_unchanged_region(self):
        content = Image.new("RGB", (1200, 600), "white")
        for x in range(0, 1200, 40):  # fine detail a rescale would smear
            content.paste("black", (x, 100, x + 3, 500))
        content.save(self.base / "light-01-window.png")
        content.crop((0, 0, 1160, 600)).save(self.head / "light-01-window.png")
        report = self.run_compare()
        view, = report["views"]
        # Only the strip the narrower head no longer covers has changed.
        self.assertEqual(view["total_pixels"], 1200 * 600)
        self.assertEqual(view["changed_pixels"], 3 * 400)
        change, = report["changes"]
        scale = 960 / 1200
        self.assertEqual(change["frame"], [960, round(600 * scale)])
        self.assertGreaterEqual(change["box"][0], 1160 * scale - compare.CELL)
        self.assertGreaterEqual(change["box"][2], 1163 * scale)

        with Image.open(self.out / view["image"]) as picture:
            before, after, changed = self.panels(picture)
        self.assertEqual(before.size, after.size)
        # Both sides are scaled alike, so their shared region still matches.
        shared = (0, 0, round(1160 * scale) - 2, before.height)
        delta = ImageChops.difference(before.crop(shared), after.crop(shared))
        self.assertLess(max(hi for _, hi in delta.getextrema()), compare.NOISE_THRESHOLD)
        self.assertFalse(self.highlighted(changed.crop((0, 0, round(1160 * scale) - 8, changed.height))))
        self.assertTrue(self.highlighted(changed))

        # The change picture differs only where the head stops.
        with Image.open(self.out / change["image"]) as picture:
            before, after, _ = self.panels(picture)
        left = ImageChops.difference(before, after).convert("L").point(
            lambda v: 255 if v >= compare.NOISE_THRESHOLD else 0).getbbox()
        self.assertIsNotNone(left)
        self.assertGreater(left[0], before.width * 0.8)

    def test_a_narrower_head_with_its_own_change_is_cropped_in_place(self):
        Image.new("RGB", (1200, 600), GREY).save(self.base / "light-01-window.png")
        head = Image.new("RGB", (1160, 600), GREY)
        head.paste("red", (500, 300, 540, 340))
        head.save(self.head / "light-01-window.png")
        report = self.run_compare()
        view, = report["views"]
        # The red block, and the strip past the narrower head's edge.
        self.assertEqual(view["changed_pixels"], 40 * 40 + 40 * 600)
        change, = report["changes"]
        scale = 960 / 1200
        self.assertLessEqual(change["box"][0], 500 * scale)
        self.assertGreaterEqual(change["box"][2], 540 * scale)
        with Image.open(self.out / change["image"]) as picture:
            before, after, _ = self.panels(picture)
        self.assertIsNone(self.red(before))
        self.assertIsNotNone(self.red(after))


if __name__ == "__main__":
    unittest.main()
