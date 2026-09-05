"""Exercise generated workflow behavior without publishing to GitHub."""

import ast
import base64
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from fxcss import core, scaffold
from fxcss.fetch import VARIANT_DIRS


def step(text, name):
    pattern = r"^      - name: " + re.escape(name) + r"\n(.*?)(?=^ {0,6}\S|\Z)"
    return re.search(pattern, text, re.M | re.S)[1]


def script(text, name, key="run"):
    block = step(text, name)
    return textwrap.dedent(re.split(r"^\s+" + key + r": \|\n", block, maxsplit=1, flags=re.M)[1])


def condition(expression, values):
    """Evaluate the comparison/boolean subset used by the watch's step guards."""
    expression = expression.strip().removeprefix("${{").removesuffix("}}")
    expression = re.sub(r"\b(?:github|matrix|steps)(?:\.[a-zA-Z_]+)+",
                        lambda m: repr(values.get(m[0], "")), expression)
    expression = expression.replace("&&", " and ").replace("||", " or ").strip()
    tree = ast.parse(expression, mode="eval")
    allowed = (ast.Expression, ast.Compare, ast.BoolOp, ast.Constant, ast.And, ast.Or,
               ast.Eq, ast.NotEq, ast.Load)
    if not all(isinstance(node, allowed) for node in ast.walk(tree)):
        raise AssertionError(f"Unsupported workflow expression: {expression}")
    return eval(compile(tree, "<workflow condition>", "eval"), {"__builtins__": {}})


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9p8AAAAASUVORK5CYII=")


class WorkflowBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.theme = self.root / "theme"
        (self.theme / "chrome").mkdir(parents=True)
        (self.theme / "chrome/userChrome.css").write_text(":root { color: red; }")
        for folder in VARIANT_DIRS:
            (self.theme / folder).mkdir()
            (self.theme / folder / f"{folder}-option.css").write_text(":root { color: blue; }")
        scaffold.write_workflows(self.theme, list(core.find_variant_sheets(self.theme)),
                                 watch=True, previews=True, showcase=True)

    def workflow(self, name):
        return (self.theme / ".github/workflows" / name).read_text()

    def test_supported_options_and_preferences_trigger_both_preview_workflows(self):
        paths = [f"{name}/{name}-option.css" for name in VARIANT_DIRS]
        paths += ["chrome/parts/toolbar.css", "configuration/user.js"]
        for name in ("pr-preview.yml", "readme-previews.yml"):
            text = self.workflow(name)
            patterns = re.findall(r"^      - '([^']+)'$", text, re.M)
            self.assertNotIn("__FXCSS_", text)
            for path in paths:
                with self.subTest(workflow=name, path=path):
                    self.assertTrue(any(fnmatch.fnmatch(path, pattern) for pattern in patterns))

    def test_sensitivity_check_requires_pixels_not_just_a_missing_view(self):
        ci = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        body = script(ci, "Detects a real change")
        code = body.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        output = self.root / "out/changed"
        output.mkdir(parents=True)
        (output / "summary.json").write_text(json.dumps({
            "any_change": True, "changed_views": [], "views": [], "only_in_base": ["variant-old"]}))
        result = subprocess.run([sys.executable, "-c", code], cwd=self.root,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("no pixel difference", result.stdout)

    @unittest.skipUnless(shutil.which("bash"), "publishing requires bash")
    def test_publishing_keeps_crops_and_full_images(self):
        bindir = self.root / "bin"
        bindir.mkdir()
        fake_git = bindir / "git"
        fake_git.write_text("#!/bin/sh\nexit 0\n")
        fake_git.chmod(0o755)
        for with_crops in (True, False):
            with self.subTest(with_crops=with_crops):
                work = self.root / str(with_crops)
                (work / "shots").mkdir(parents=True)
                (work / "crops").mkdir()
                (work / "shots/light-01-window.png").write_bytes(b"full capture")
                if with_crops:
                    (work / "crops/compact-diff.png").write_bytes(b"option crop")
                env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"],
                           GITHUB_SHA="local-test")
                result = subprocess.run([shutil.which("bash"), "-c", script(
                    self.workflow("readme-previews.yml"), "Publish to previews branch")],
                    cwd=work, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((work / "light-01-window.png").read_bytes(), b"full capture")
                if with_crops:
                    self.assertEqual((work / "compact-diff.png").read_bytes(), b"option crop")
                self.assertFalse((work / "crops").exists())

    def test_single_channel_watch_never_handles_results_for_skipped_channels(self):
        text = self.workflow("firefox-watch.yml")
        actions = ["Open or update the capture-pipeline issue",
                   "Close the capture-pipeline issue if this channel is clean again",
                   "Open a pull request with the confident fixes", "Open or update the heads-up issue",
                   "Close the issue if this channel is clean again"]
        for selected in ("", "all", "release", "beta", "nightly"):
            for channel in ("release", "beta", "nightly"):
                for status, patch in (("0", ""), ("1", "yes"), ("1", "")):
                    values = {"github.event.inputs.channel": selected, "matrix.channel": channel}
                    for name, key in (("Audit", "audit"), ("Capture smoke", "smoke")):
                        guard = re.search(r"^        if: (.+)$", step(text, name), re.M)[1]
                        ran = condition(guard, values)
                        values[f"steps.{key}.outcome"] = "success" if ran else "skipped"
                        if ran:
                            values[f"steps.{key}.outputs.status"] = status
                            values[f"steps.{key}.outputs.patch"] = patch
                    triggered = [name for name in actions if condition(re.search(
                        r"^        if: (.+)$", step(text, name), re.M)[1], values)]
                    with self.subTest(selected=selected, channel=channel, status=status, patch=patch):
                        if selected not in ("", "all", channel):
                            self.assertEqual(triggered, [])
                        else:
                            self.assertEqual(len(triggered), 2)
                            self.assertEqual("Open a pull request with the confident fixes" in triggered,
                                             status == "1" and patch == "yes")

    @unittest.skipUnless(shutil.which("node"), "publisher scripts require Node")
    def test_removed_views_survive_validation_and_appear_in_preview_comment(self):
        sha = "a" * 40
        artifact = self.root / "artifacts/preview-macos"
        captures = artifact / "macos"
        (captures / "full").mkdir(parents=True)
        (artifact / "pr-number.txt").write_text("7")
        (artifact / "head-sha.txt").write_text(sha)
        (captures / "full/light-01-window.png").write_bytes(PNG)
        (captures / "summary.json").write_text(json.dumps({
            "views": [], "only_in_head": [],
            "only_in_base": ["variant-custom-option", "../../unsafe", "<script>"]}))
        data = self.publish()
        self.assertEqual(data["errors"], [])
        self.assertIn("1 missing view", data["body"])
        self.assertIn("custom-option", data["body"])
        self.assertNotIn("no visual change", data["body"])
        self.assertNotIn("No pixel differences", data["body"])
        self.assertNotIn("unsafe", data["body"])
        self.assertNotIn("<script>", data["body"])

    @unittest.skipUnless(shutil.which("node"), "publisher scripts require Node")
    def test_new_variants_and_rejected_images_never_claim_no_change(self):
        artifact = self.root / "artifacts/preview-macos"
        captures = artifact / "macos"
        (captures / "full").mkdir(parents=True)
        (artifact / "pr-number.txt").write_text("7")
        (artifact / "head-sha.txt").write_text("a" * 40)
        (captures / "full/light-01-window.png").write_bytes(PNG)
        new = captures / "full/variant-brand-new+compact.png"
        new.write_bytes(PNG)
        (captures / "summary.json").write_text(json.dumps({
            "views": [], "only_in_head": [new.stem], "only_in_base": []}))
        data = self.publish()
        self.assertEqual(data["errors"], [])
        self.assertIn("1 new view", data["body"])
        self.assertIn("brand-new+compact", data["body"])
        self.assertNotIn("no visual change", data["body"])
        new.write_bytes(b"not a PNG")
        data = self.publish()
        self.assertEqual(data["errors"], [])
        self.assertIn("unreviewed", data["body"])
        self.assertNotIn("No pixel differences", data["body"])
        self.assertNotIn("no visual change", data["body"])
        self.assertNotIn("brand-new+compact", data["body"])


    def publish(self):
        text = self.workflow("pr-preview-publish.yml")
        bodies = [script(text, "Validate artifact and resolve pull request", "script"),
                  script(text, "Upsert pull request comment", "script")]
        harness = r'''
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
const scripts = JSON.parse(process.argv[1]);
const outputs = {};
const result = { body: null, errors: [] };
const core = {setOutput: (k, v) => outputs[k] = v,
              setFailed: v => result.errors.push(v), notice: () => {}, warning: () => {}, info: () => {}};
const context = {repo: {owner: 'test', repo: 'theme'}, eventName: 'workflow_run',
                 payload: {workflow_run: {head_sha: 'a'.repeat(40)}}};
const github = {rest: {
  pulls: {get: async () => ({data: {head: {sha: 'a'.repeat(40)}, state: 'open'}})},
  issues: {listComments: async () => ({data: []}),
           createComment: async data => {result.body = data.body;}}
}};
(async () => {
  await new AsyncFunction('require', 'core', 'github', 'context', scripts[0])(require, core, github, context);
  process.env.PR = '7'; process.env.SHA = 'a'.repeat(40); process.env.RUN_ID = '1';
  process.env.REPORT = outputs.report;
  if (!result.errors.length) {
    await new AsyncFunction('require', 'core', 'github', 'context', scripts[1])(require, core, github, context);
  }
  process.stdout.write(JSON.stringify(result));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run([shutil.which("node"), "-e", harness, json.dumps(bodies)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        return data


if __name__ == "__main__":
    unittest.main()
