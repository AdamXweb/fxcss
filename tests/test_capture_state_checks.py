"""Exercise the CI capture-state smoke checks offline, with no Firefox."""

import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fxcss import capture
from tests import smoke_capture_states as smoke

try:
    from PIL import Image
except ImportError:  # pragma: no cover - the images extra is optional
    Image = None

REPO = Path(__file__).resolve().parents[1]
STEPS = ("capture", "determinism", "real-change", "states")
VIEWS = ("light-01-window", "dark-01-window")


def workflow(name):
    return (REPO / ".github/workflows" / name).read_text(encoding="utf-8")


def summary(views, only_in_base=(), only_in_head=()):
    """A compare summary.json for {view: changed_pixels}."""
    rows = [{"view": name, "changed_pixels": changed, "total_pixels": 1000,
             "percent": round(100.0 * changed / 1000, 4),
             "image": f"{name}.png" if changed else None}
            for name, changed in views.items()]
    return {"views": rows, "changed_views": [row for row in rows if row["image"]],
            "only_in_base": list(only_in_base), "only_in_head": list(only_in_head),
            "any_change": bool(any(views.values()) or only_in_base or only_in_head)}


def run_quietly(function, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = function(*args)
    return result, out.getvalue()


class WorkflowWiringTests(unittest.TestCase):
    def assert_runs_every_step(self, text, platform):
        commands = re.findall(r"^        run: python -m tests\.smoke_capture_states (.*)$",
                              text, re.M)
        self.assertEqual([c.split()[0] for c in commands], list(STEPS))
        for command in commands[1:]:
            self.assertIn(f"--platform {platform}", command)

    def test_pull_request_ci_runs_the_extracted_checks_in_order(self):
        ci = workflow("ci.yml")
        self.assert_runs_every_step(ci, "${{ matrix.label }}")
        smoke_job = ci.split("\n  smoke:\n", 1)[1]
        self.assertNotIn("python - <<'PY'", smoke_job.split("upgrade --compare renders", 1)[0])
        self.assertNotIn("<<'PY'", smoke_job.split("State and surface views", 1)[1])

    def test_compatibility_runs_them_on_every_channel_including_beta_and_nightly(self):
        compat = workflow("firefox-compatibility.yml")
        self.assert_runs_every_step(compat, '"Linux-$CHANNEL"')
        for product in ("firefox-latest-ssl", "firefox-esr-latest-ssl",
                        "firefox-beta-latest-ssl", "firefox-nightly-latest-ssl"):
            self.assertIn(f"product: {product}", compat)
        # The existing zero-tolerance baseline repeat stays alongside.
        self.assertIn("--max-changed-percent 0", compat)

    def test_original_shots_are_uploaded_when_a_check_fails(self):
        for name, prefix in (("ci.yml", "fxcss-${{ matrix.label }}"),
                             ("firefox-compatibility.yml", "firefox-${{ matrix.channel }}")):
            text = workflow(name)
            self.assertRegex(text, r"if: always\(\)\n\s+with:\n\s+name: " + re.escape(prefix)
                             + r"\n\s+path: out/")
            self.assertRegex(text, r"if: failure\(\)\n\s+with:\n\s+name: " + re.escape(prefix)
                             + r"-shots\n\s+path: shots/")

    def test_unit_matrix_adds_the_minimum_pillow_and_keeps_unconstrained_jobs(self):
        ci = workflow("ci.yml")
        units = ci.split("\n  units:\n", 1)[1].split("\n  build-package:\n", 1)[0]
        entries = re.findall(r"^          - \{(.*)\}$", units, re.M)
        floor = [e for e in entries if "pillow:" in e]
        self.assertEqual(len(floor), 1, entries)
        self.assertIn("python: '3.12'", floor[0])
        self.assertIn("ubuntu", floor[0])
        # Pinned to exactly the floor the images extra declares.
        dependency, = re.findall(r'"pillow>=([\d.]+)"', (REPO / "pyproject.toml").read_text())
        pinned = re.search(r"pillow: '([\d.]+)'", floor[0])[1]

        def version(text):
            parts = [int(part) for part in text.split(".")]
            return parts + [0] * (3 - len(parts))

        self.assertEqual(version(pinned), version(dependency))
        self.assertGreaterEqual(len(entries) - len(floor), 5)
        self.assertIn("if: matrix.pillow", units)
        self.assertIn('"pillow==$PILLOW"', units)
        self.assertIn("PIL.__version__ == os.environ['PILLOW']", units)
        # The whole suite runs there, not a hand-picked subset.
        self.assertIn("python -m unittest discover -s tests -v", units)


class ReportTests(unittest.TestCase):
    def test_sensitivity_check_requires_pixels_not_just_a_missing_view(self):
        passed, lines = smoke.real_change_report(summary({}, only_in_base=["variant-old"]))
        self.assertFalse(passed)
        self.assertIn("no pixel difference", lines[0])
        passed, lines = smoke.real_change_report(summary({"light-01-window": 0}))
        self.assertFalse(passed)
        passed, lines = smoke.real_change_report(summary({"light-01-window": 7,
                                                          "dark-01-window": 0}))
        self.assertTrue(passed)
        self.assertIn("light-01-window: 7/1000 pixels (0.7%)", "\n".join(lines))

    def test_every_pair_is_strict_and_reports_raw_pixel_counts(self):
        clean = summary(dict.fromkeys(VIEWS, 0))
        passed, lines = smoke.determinism_report(dict.fromkeys(smoke.PAIRS, clean))
        self.assertTrue(passed)
        self.assertEqual(len(lines), 3)
        self.assertIn("0/2000 pixels changed", lines[0])
        # One pixel in one pair, the only pair a two-pair check did not run.
        one = summary({"light-01-window": 1, "dark-01-window": 0})
        passed, lines = smoke.determinism_report(
            {("a", "b"): clean, ("a", "c"): clean, ("b", "c"): one})
        self.assertFalse(passed)
        self.assertIn("light-01-window: 1/1000 pixels (0.1%)", "\n".join(lines))

    def test_a_missing_or_added_view_is_a_determinism_failure(self):
        clean = summary(dict.fromkeys(VIEWS, 0))
        for missing in ({"only_in_base": ["light-02-urlbar"]},
                        {"only_in_head": ["light-02-urlbar"]}):
            passed, lines = smoke.determinism_report(
                {("a", "b"): clean, ("a", "c"): summary(dict.fromkeys(VIEWS, 0), **missing),
                 ("b", "c"): clean})
            self.assertFalse(passed)
            self.assertIn("light-02-urlbar", "\n".join(lines))

    def test_the_odd_run_out_is_named(self):
        ab, ac, bc = smoke.PAIRS
        self.assertIn("check-vs-shot", smoke.diagnose({ab, ac}))
        self.assertIn("first-run effects", smoke.diagnose({ab, ac}))
        self.assertIn("run b differs", smoke.diagnose({ab, bc}))
        self.assertIn("intermittent", smoke.diagnose({ab, bc}))
        self.assertIn("run c differs", smoke.diagnose({ac, bc}))
        self.assertIn("unstable", smoke.diagnose({ab, ac, bc}))
        self.assertIn("noise threshold", smoke.diagnose({bc}))

    def coverage(self, **views):
        records = {name: {"status": "captured"} for name in smoke.STATE_VIEWS}
        records.update(views)
        return {"views": records}

    def test_state_views_must_all_change(self):
        everything = summary(dict.fromkeys(smoke.STATE_VIEWS, 5))
        passed, lines = smoke.states_report(everything, self.coverage())
        self.assertTrue(passed, lines)
        self.assertIn(f"all {len(smoke.STATE_VIEWS)} state views reacted", "\n".join(lines))
        # A view that fell back to an ordinary window does not change.
        fallback = summary(dict(dict.fromkeys(smoke.STATE_VIEWS, 5), **{"extra-04-audio": 0}))
        passed, lines = smoke.states_report(fallback, self.coverage())
        self.assertFalse(passed)
        self.assertIn("extra-04-audio", lines[0])

    def test_only_search_views_may_be_skipped_and_only_for_the_missing_button(self):
        unchanged = {"light-06-search-engine", "dark-07-search-menu"}
        result = summary({name: 0 if name in unchanged else 5 for name in smoke.STATE_VIEWS})
        skip = {"status": "unsupported", "reason": capture.NO_SEARCH_BUTTON}
        passed, lines = smoke.states_report(
            result, self.coverage(**dict.fromkeys(unchanged, skip)))
        self.assertTrue(passed, lines)
        self.assertIn(f"light-06-search-engine: skipped, {capture.NO_SEARCH_BUTTON}", lines)
        # Any other reason, a non-search view, or a captured view still fails.
        for name, record in (
                ("light-06-search-engine", {"status": "unsupported", "reason": "other"}),
                ("light-06-search-engine", {"status": "captured",
                                            "reason": capture.NO_SEARCH_BUTTON}),
                ("light-06-search-engine", {"status": "failed",
                                            "reason": capture.NO_SEARCH_BUTTON}),
                ("light-05-toolbar-hover", skip)):
            with self.subTest(name=name, record=record):
                broken = summary({view: 0 if view == name else 5 for view in smoke.STATE_VIEWS})
                passed, lines = smoke.states_report(broken, self.coverage(**{name: record}))
                self.assertFalse(passed)
                self.assertNotIn(f"{name}: skipped", lines)
                self.assertIn(name, lines[0])


@unittest.skipIf(Image is None, "the orchestration tests compare real images with Pillow")
class OrchestrationTests(unittest.TestCase):
    """Run each step with `fxcss check` and `fxcss shot` replaced by files.

    Comparisons use the real `fxcss compare`, so the pixel counts, the
    strictness and the diagnosis are exactly what CI would see.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.calls = []
        self.altered = {}  # run name -> {view: (x, y)} pixel to recolour
        patcher = mock.patch.object(smoke, "fxcss", self.fake_fxcss)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict("os.environ", {"RUNNER_TEMP": str(self.root / "runner")})
        env.start()
        self.addCleanup(env.stop)

    def write_shots(self, directory, run, coverage=None):
        directory.mkdir(parents=True)
        for name in VIEWS:
            image = Image.new("RGB", (64, 32), (240, 240, 240))
            if name in self.altered.get(run, {}):
                image.putpixel(self.altered[run][name], (0, 0, 0))
            image.save(directory / f"{name}.png")
        (directory / capture.REPORT).write_text(json.dumps(coverage or {
            "format": 1, "browser": {}, "views": {n: {"status": "captured"} for n in VIEWS}}))

    def fake_fxcss(self, command, *args):
        from fxcss import compare
        self.calls.append((command, *map(str, args)))
        options = dict(zip(args[::2], args[1::2]))
        if command == "check":
            run = Path(options["--out"]) / "run-1"
            self.write_shots(run / "stable" / "shots", "a")
            run.joinpath("summary.json").write_text(json.dumps(
                {"exit_code": self.check_exit, "browsers": [{"key": "stable"}]}))
        elif command == "shot":
            out = Path(options["--out"])
            self.write_shots(out, out.name)
        elif command == "compare":
            code = compare.run(Path(options["--base"]), Path(options["--head"]),
                               Path(options["--out"]), options["--platform"])
            self.assertEqual(code, 0)
        else:
            self.fail(command)

    check_exit = 0

    def capture(self):
        return run_quietly(smoke.main, ["capture", "--firefox", "/ff", "--root", str(self.root)])

    def determinism(self):
        return run_quietly(smoke.main, ["determinism", "--platform", "Test",
                                        "--root", str(self.root)])

    def test_capture_takes_the_check_screenshots_and_two_shot_runs_of_all_variants(self):
        code, _ = self.capture()
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in self.calls], ["check", "shot", "shot"])
        self.assertIn("/ff", self.calls[0])
        for call in self.calls[1:]:
            self.assertEqual(call[-2:], ("--variants", "all"))
        for run in smoke.RUNS:
            self.assertTrue((self.root / "shots" / run / "light-01-window.png").is_file())

    def test_a_failed_check_stops_before_any_shot(self):
        self.check_exit = 1
        with self.assertRaises(SystemExit):
            self.capture()
        self.assertEqual([c[0] for c in self.calls], ["check"])

    def test_identical_runs_pass_every_pair(self):
        self.capture()
        code, output = self.determinism()
        self.assertEqual(code, 0, output)
        compared = [(Path(c[2]).name, Path(c[4]).name) for c in self.calls if c[0] == "compare"]
        self.assertEqual(compared, list(smoke.PAIRS))
        self.assertEqual(output.count("views identical (0/4096 pixels changed)"), 3)

    def test_a_repeatable_check_vs_shot_pixel_is_told_apart_from_shot_noise(self):
        # One text-edge pixel that only the check capture has.
        self.altered = {"a": {"light-01-window": (5, 5)}}
        self.capture()
        code, output = self.determinism()
        self.assertEqual(code, 1)
        self.assertIn("light-01-window: 1/2048 pixels (0.0488%)", output)
        self.assertIn("run b (fxcss shot) vs run c (fxcss shot): all 2 views identical", output)
        self.assertIn("light-01-window: run a (fxcss check) differs from runs b and c", output)
        self.assertIn("check-vs-shot", output)
        self.assertNotIn("intermittent", output)

    def test_one_shot_run_that_differs_is_reported_as_intermittent(self):
        self.altered = {"b": {"dark-01-window": (9, 3)}}
        self.capture()
        code, output = self.determinism()
        self.assertEqual(code, 1)
        self.assertIn("dark-01-window: run b differs from the other two runs", output)
        self.assertIn("intermittent", output)
        self.assertNotIn("check-vs-shot", output)

    def test_real_change_and_state_themes_are_copies_with_the_rules_added(self):
        starter = (smoke.STARTER / "chrome/userChrome.css").read_text(encoding="utf-8")
        self.capture()
        self.altered = {"changed": {"light-01-window": (1, 1)}}
        code, output = run_quietly(smoke.main, ["real-change", "--root", str(self.root)])
        self.assertEqual(code, 0, output)
        self.assertIn("1 of 2 views changed, as expected", output)
        theme = self.root / "runner/changed-theme"
        self.assertTrue((theme / "chrome/userChrome.css").read_text(
            encoding="utf-8").endswith(smoke.REAL_CHANGE_CSS))
        self.assertEqual((smoke.STARTER / "chrome/userChrome.css").read_text(encoding="utf-8"),
                         starter)

        code, output = run_quietly(smoke.main, ["states", "--root", str(self.root)])
        # The fake captures lack every state view, so this must fail loudly.
        self.assertEqual(code, 1)
        self.assertIn("not capturing the state they claim", output)
        state = self.root / "runner/state-theme/chrome"
        self.assertIn(smoke.STATE_CHROME_CSS, (state / "userChrome.css").read_text(encoding="utf-8"))
        self.assertEqual((state / "userContent.css").read_text(encoding="utf-8"),
                         smoke.STATE_CONTENT_CSS)
        self.assertNotIn("#PanelUI-fxa", starter)

    def test_an_unchanged_real_change_capture_fails(self):
        self.capture()
        code, output = run_quietly(smoke.main, ["real-change", "--root", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("no pixel difference", output)


if __name__ == "__main__":
    unittest.main()
