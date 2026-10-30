"""Exercise generated workflow behavior without publishing to GitHub."""

import ast
import base64
import contextlib
import fnmatch
import io
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

from fxcss import cli, core, scaffold
from fxcss.fetch import VARIANT_DIRS


def step(text, name):
    pattern = r"^      - name: " + re.escape(name) + r"\n(.*?)(?=^ {0,6}\S|\Z)"
    return re.search(pattern, text, re.M | re.S)[1]


def script(text, name, key="run"):
    block = step(text, name)
    return textwrap.dedent(re.split(r"^\s+" + key + r": \|\n", block, maxsplit=1, flags=re.M)[1])


def workflow_env(text):
    return {k: v.replace("''", "'") for k, v in re.findall(r"^  (FXCSS_[A-Z_]+): '(.*)'$", text, re.M)}


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

    def test_every_generated_workflow_has_its_placeholders_filled(self):
        for path in (self.theme / ".github/workflows").iterdir():
            with self.subTest(workflow=path.name):
                self.assertNotIn("__FXCSS_", path.read_text())

    def test_preview_settings_default_to_everything_and_reach_fxcss_quoted(self):
        text = self.workflow("pr-preview.yml")
        self.assertEqual(workflow_env(text), {"FXCSS_VARIANTS": "all", "FXCSS_VIEWS": "",
                                              "FXCSS_MIN_CHANGED_PERCENT": "0",
                                              "FXCSS_OLDEST_FIREFOX": "esr"})
        self.assertEqual(workflow_env(self.workflow("firefox-watch.yml")),
                         {"FXCSS_OLDEST_FIREFOX": "esr"})
        for name, setting in (("Render base revision", '--variants="$FXCSS_VARIANTS"'),
                              ("Render pull request revision", '--variants="$FXCSS_VARIANTS"'),
                              ("Compare", '--views="$FXCSS_VIEWS"'),
                              ("Compare", '--min-changed-percent="$FXCSS_MIN_CHANGED_PERCENT"')):
            with self.subTest(step=name, setting=setting):
                block = step(text, name)
                self.assertIn("shell: bash", block)
                self.assertIn(setting, block)

    def test_init_writes_preview_settings_and_rejects_bad_ones_before_writing(self):
        def init(*args):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return cli.main(["init", "--theme", str(self.theme), "--force", *args])
        self.assertEqual(init("--variants", "custom-option", "--views", "light-*, variant-*",
                              "--min-changed-percent", "0.25", "--oldest-firefox", "140"), 0)
        self.assertEqual(workflow_env(self.workflow("pr-preview.yml")), {
            "FXCSS_VARIANTS": "custom-option", "FXCSS_VIEWS": "light-*,variant-*",
            "FXCSS_MIN_CHANGED_PERCENT": "0.25", "FXCSS_OLDEST_FIREFOX": "140"})
        self.assertEqual(init("--watch", "--oldest-firefox", ""), 0)
        self.assertEqual(workflow_env(self.workflow("firefox-watch.yml")),
                         {"FXCSS_OLDEST_FIREFOX": ""})
        before = self.workflow("pr-preview.yml")
        for args in (("--views", "light-*,dark-02-urlbr"), ("--views", "it's"),
                     ("--variants", "missing"), ("--min-changed-percent", "101"),
                     ("--min-changed-percent", "nan"), ("--oldest-firefox", "latest")):
            with self.subTest(args=args):
                self.assertEqual(init(*args), 2)
                self.assertEqual(self.workflow("pr-preview.yml"), before)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("node"),
                         "the workflow steps need bash and Node")
    def test_selected_views_and_small_changes_reach_the_comment(self):
        from PIL import Image
        scaffold.write_workflows(self.theme, [], force=True, preview=scaffold.preview_settings(
            self.theme, views="light-*", min_changed_percent=5))
        text = self.workflow("pr-preview.yml")
        for side in ("base", "head"):
            (self.root / "shots" / side).mkdir(parents=True)
        for name, head in (("light-01-window", "red"), ("light-03-findbar", "dot"),
                           ("dark-01-window", "red")):
            Image.new("RGB", (16, 8), "grey").save(self.root / f"shots/base/{name}.png")
            image = Image.new("RGB", (16, 8), "red" if head == "red" else "grey")
            if head == "dot":
                image.putpixel((0, 0), (255, 0, 0))
            image.save(self.root / f"shots/head/{name}.png")
        command = " ".join(textwrap.dedent(step(text, "Compare").split("run: >-\n", 1)[1]).split())
        command = command.replace("${{ matrix.label }}", "macos")
        env = dict(os.environ, **workflow_env(text), PYTHON=sys.executable,
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        result = subprocess.run([shutil.which("bash"), "-c",
                                 'fxcss() { "$PYTHON" -m fxcss "$@"; }\n' + command],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        artifact = self.root / "artifacts/preview-macos"
        artifact.mkdir(parents=True)
        shutil.move(self.root / "out/macos", artifact / "macos")
        (artifact / "pr-number.txt").write_text("7")
        (artifact / "head-sha.txt").write_text("a" * 40)
        data = self.publish()
        self.assertEqual(data["errors"], [])
        body = data["body"]
        self.assertIn("1 view changed · 1 smaller change", body)
        self.assertIn("**1. Browser window — light** · macOS", body)
        self.assertIn("/macos/change-01.png", body)
        self.assertIn("[Browser window — light](", body)
        self.assertIn("Changed by less than 5% of pixels, not pictured: Find bar — light (0.78", body)
        self.assertIn("Only the views selected by `FXCSS_VIEWS`", body)
        self.assertNotIn("dark", body.split("<sub>")[0])
        self.assertNotIn("No pixel differences", body)

        summary = artifact / "macos/summary.json"
        data = json.loads(summary.read_text())
        data["below_threshold"].append({"view": "constructor"})
        summary.write_text(json.dumps(data))
        body = self.publish()["body"]
        self.assertIn("1 unreviewed screenshot", body)
        self.assertNotIn("constructor", body)

    @unittest.skipUnless(shutil.which("node"), "publisher scripts require Node")
    def test_changes_all_under_the_minimum_are_not_called_no_change(self):
        artifact = self.root / "artifacts/preview-macos"
        captures = artifact / "macos"
        (captures / "full").mkdir(parents=True)
        (artifact / "pr-number.txt").write_text("7")
        (artifact / "head-sha.txt").write_text("a" * 40)
        (captures / "full/light-01-window.png").write_bytes(PNG)
        view = {"view": "light-01-window", "percent": 0.0123, "changed_pixels": 3, "image": None}
        (captures / "summary.json").write_text(json.dumps({
            "views": [view], "below_threshold": [view], "min_changed_percent": 0.5,
            "only_in_head": [], "only_in_base": []}))
        body = self.publish()["body"]
        self.assertIn("no change of 0.5% or more · 1 smaller change", body)
        self.assertIn("Browser window — light (0.0123%)", body)
        self.assertIn("Nothing changed by 0.5% or more", body)
        self.assertIn("No change on any platform reaches the threshold", body)
        self.assertNotIn("No pixel differences", body)
        self.assertNotIn("Only the views selected", body)

    def test_support_check_runs_once_and_can_never_fail_the_preview(self):
        text = self.workflow("pr-preview.yml")
        guard = "if: runner.os == 'Linux' && env.FXCSS_OLDEST_FIREFOX != ''"
        for name in ("Restore Firefox build indexes", "Firefox support",
                     "Save Firefox build indexes"):
            with self.subTest(step=name):
                self.assertIn(guard, step(text, name))
        body = script(text, "Firefox support")
        self.assertIn('--oldest="$FXCSS_OLDEST_FIREFOX" --json out/support.json', body)
        self.assertIn('|| echo "::warning::', body)
        self.assertIn("shell: bash", step(text, "Firefox support"))

    @unittest.skipUnless(shutil.which("bash"), "the watch step needs bash")
    def test_watch_fix_pull_requests_open_with_the_support_check(self):
        bindir, temp, repo = self.root / "bin", self.root / "runner", self.root / "repo"
        for folder in (bindir, temp, repo / "chrome"):
            folder.mkdir(parents=True)
        (repo / "chrome/userChrome.css").write_text("#sidebar-box {}\n")
        (repo / "fxcss-fix.patch").write_text("--- a\n+++ b\n")
        (repo / "audit.txt").write_text("audit report\n")
        log = self.root / "calls.log"
        tools = {
            "git": 'echo "git $*" >> "$LOG"\n'
                   'case "$1" in ls-remote) exit 2 ;; archive) tar -cf - -C "$SRC" . ;; esac',
            "fxcss": 'echo "fxcss $*" >> "$LOG"\n'
                     'while [ $# -gt 0 ]; do [ "$1" = --markdown ] && out=$2; shift; done\n'
                     'printf "> [!WARNING]\\n> **Merging this drops styling for Firefox 159 '
                     'and earlier.**\\n" > "$out"',
            "gh": 'echo "gh $*" >> "$LOG"\n'
                  'while [ $# -gt 0 ]; do [ "$1" = --body-file ] && cp "$2" "$BODY"; shift; done',
        }
        for name, body in tools.items():
            (bindir / name).write_text("#!/bin/sh\n" + body + "\n")
            (bindir / name).chmod(0o755)
        env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"], LOG=str(log),
                   SRC=str(repo), BODY=str(self.root / "body.md"), RUNNER_TEMP=str(temp),
                   CHANNEL="beta", VERSION="160.0b1", FIREFOX_BIN="/ff/firefox",
                   FXCSS_OLDEST_FIREFOX="esr", GITHUB_REF_NAME="main", GH_TOKEN="x")
        text = self.workflow("firefox-watch.yml")
        result = subprocess.run([shutil.which("bash"), "-c", script(
            text, "Open a pull request with the confident fixes")],
            cwd=repo, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text()
        self.assertIn(f"fxcss support --base {temp}/before-fix --head . --firefox /ff/firefox "
                      "--oldest=esr --channel beta --markdown", calls)
        self.assertTrue((temp / "before-fix/chrome/userChrome.css").is_file())
        body = (self.root / "body.md").read_text()
        self.assertTrue(body.startswith("> [!WARNING]\n> **Merging this drops styling"), body)
        self.assertIn("Firefox **beta** is now **160.0b1**", body)
        self.assertFalse((repo / "support.md").exists())

        env["FXCSS_OLDEST_FIREFOX"] = ""
        log.write_text("")
        subprocess.run([shutil.which("bash"), "-c", script(
            text, "Open a pull request with the confident fixes")],
            cwd=repo, env=env, capture_output=True, text=True, check=True)
        self.assertNotIn("fxcss support", log.read_text())
        self.assertTrue((self.root / "body.md").read_text().startswith("Firefox **beta**"))

    def support_artifact(self, report):
        artifact = self.root / "artifacts/preview-linux"
        captures = artifact / "linux"
        (captures / "full").mkdir(parents=True, exist_ok=True)
        (artifact / "pr-number.txt").write_text("7")
        (artifact / "head-sha.txt").write_text("a" * 40)
        (captures / "full/light-01-window.png").write_bytes(PNG)
        (captures / "summary.json").write_text(json.dumps(
            {"views": [], "only_in_head": [], "only_in_base": []}))
        (artifact / "support.json").write_text(json.dumps(report))
        data = self.publish()
        self.assertEqual(data["errors"], [])
        return data["body"]

    @unittest.skipUnless(shutil.which("node"), "publisher scripts require Node")
    def test_the_comment_opens_with_what_merging_does_to_older_firefox(self):
        from fxcss import support
        report = {"format": 1, "status": "checked", "firefox": "158.0.1", "current": 158,
                  "oldest": 153, "releases": {"release": 158, "esr": 153}, "candidates": 2,
                  "breaks_below": 158, "new_only_from": 158, "exact": True,
                  "dropped": [{"kind": "selector", "selector": "#sidebar-box", "names": ["#sidebar-box"],
                               "file": "chrome/sidebar.css", "line": 12, "since": 158}],
                  "added": [{"kind": "selector", "selector": ".sidebar-box", "names": [".sidebar-box"],
                             "file": "chrome/sidebar.css", "line": 12, "since": 158}]}
        body = self.support_artifact(report)
        top = body.split("<details", 1)[0]
        self.assertIn("> [!WARNING]\n> **Merging this drops styling for Firefox 157 and earlier, "
                      "ESR 153 included.**", top)
        self.assertIn("only match from Firefox 158", top)
        self.assertIn("- `chrome/sidebar.css:12` `#sidebar-box`: no longer matches from Firefox 158", body)
        # The comment and `fxcss support` say the same thing.
        self.assertIn(support.markdown(report).split("\n\n")[0].replace("this change", "this PR"),
                      body)

        report.update(dropped=[], breaks_below=None)
        body = self.support_artifact(report)
        self.assertIn("> [!NOTE]\n> New rules for `.sidebar-box` only match from Firefox 158", body)
        self.assertNotIn("WARNING", body)

        report.update(added=[], new_only_from=None)
        self.assertIn("Firefox support is unchanged: nothing this PR removes or adds matches "
                      "differently across Firefox 153–158.", self.support_artifact(report))

        self.assertIn("Firefox support could not be checked in this run",
                      self.support_artifact({"format": 1, "status": "unavailable", "reason": "x"}))

    @unittest.skipUnless(shutil.which("node"), "publisher scripts require Node")
    def test_support_findings_from_the_artifact_are_revalidated(self):
        hostile = {"format": 1, "status": "checked", "current": 158, "oldest": 153,
                   "releases": {"esr": "<b>153</b>"},
                   "dropped": [{"names": ["<img src=x onerror=alert(1)>", "#ok-name"],
                                "file": "../../etc/passwd.css", "line": 3, "since": 158},
                               {"names": ["#no-version"], "since": "158"},
                               {"names": ["#too-new"], "since": 5000}],
                   "added": "not a list"}
        body = self.support_artifact(hostile)
        block = body.split("<details><summary><b>Linux", 1)[0]
        self.assertIn("`#ok-name`", block)
        for text in ("<img", "onerror", "passwd", "no-version", "too-new", "<b>", "ESR"):
            self.assertNotIn(text, block)
        self.assertNotIn("Where these rules are", body)
        self.assertIn("could not be checked", self.support_artifact({"format": 2, "status": "checked"}))
        self.assertIn("could not be checked", self.support_artifact(
            {"format": 1, "status": "checked", "current": "158", "oldest": 153}))

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

    def test_readme_changes_skip_expensive_ci_for_pushes_and_pull_requests(self):
        ci = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        body = script(ci, "Identify README-only changes")
        repo = self.root / "scope"
        repo.mkdir()

        def git(*args):
            result = subprocess.run(["git", *args], cwd=repo, check=True,
                                    capture_output=True, text=True)
            return result.stdout.strip()

        git("init")
        git("config", "user.name", "CI test")
        git("config", "user.email", "ci@example.invalid")
        (repo / "fxcss").mkdir()
        (repo / "README.md").write_text("before")
        (repo / "fxcss/core.py").write_text("before")
        git("add", ".")
        git("commit", "-m", "base")
        base = git("rev-parse", "HEAD")

        (repo / "README.md").write_text("documentation update")
        git("add", ".")
        git("commit", "-m", "readme")
        site = git("rev-parse", "HEAD")

        (repo / "fxcss/core.py").write_text("toolkit update")
        git("add", ".")
        git("commit", "-m", "toolkit")
        toolkit = git("rev-parse", "HEAD")

        output = self.root / "ci-scope-output"

        def scope(event, before, head):
            output.write_text("")
            env = dict(os.environ, EVENT_NAME=event, PR_BASE=before,
                       PR_HEAD=head, PUSH_BASE=before, PUSH_HEAD=head,
                       RUNNER_TEMP=str(self.root), GITHUB_OUTPUT=str(output))
            result = subprocess.run(["bash", "-c", body], cwd=repo,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return output.read_text().strip()

        self.assertEqual(scope("pull_request", base, site), "full=false")
        self.assertEqual(scope("push", base, site), "full=false")
        self.assertEqual(scope("pull_request", base, toolkit), "full=true")
        self.assertEqual(scope("push", site, toolkit), "full=true")
        self.assertEqual(scope("workflow_call", base, site), "full=true")

    def test_publisher_scripts_take_the_run_id_from_the_environment(self):
        # The manual run ID is typed by a person. Interpolating it into a
        # shell script with ${{ inputs.run_id }} would execute whatever they
        # typed; the step reads it from env and the shell only ever quotes it.
        text = self.workflow("pr-preview-publish.yml")
        resolve = step(text, "Resolve source run")
        self.assertIn("DISPATCH_RUN_ID: ${{ inputs.run_id }}", resolve)
        self.assertIn("WORKFLOW_RUN_ID: ${{ github.event.workflow_run.id }}", resolve)
        self.assertNotIn("${{", script(text, "Resolve source run"))
        # And no other shell step in the publisher splices an expression in.
        for name in re.findall(r"^      - name: (.+)$", text, re.M):
            block = step(text, name)
            if re.search(r"^\s+run: \|\n", block, re.M):
                with self.subTest(step=name):
                    self.assertNotIn("${{", script(text, name))

    @unittest.skipUnless(shutil.which("bash"), "resolving the run needs bash")
    def test_manual_run_ids_are_numbers_or_nothing_is_downloaded(self):
        body = script(self.workflow("pr-preview-publish.yml"), "Resolve source run")
        output = self.root / "github-output"
        canary = self.root / "pwned"

        def resolve(event, dispatch="", run=""):
            output.write_text("")
            env = dict(os.environ, EVENT_NAME=event, DISPATCH_RUN_ID=dispatch,
                       WORKFLOW_RUN_ID=run, GITHUB_OUTPUT=str(output))
            result = subprocess.run([shutil.which("bash"), "-c", body], cwd=self.root,
                                    env=env, capture_output=True, text=True)
            return result.returncode, output.read_text().strip(), result.stdout + result.stderr

        self.assertEqual(resolve("workflow_dispatch", dispatch="123456789")[:2],
                         (0, "run_id=123456789"))
        self.assertEqual(resolve("workflow_run", dispatch="999", run="42")[:2],
                         (0, "run_id=42"))

        hostile = f"1; touch {canary}", f"$(touch {canary})", f"`touch {canary}`",
        for value in hostile + ("", " 12", "12 ", "12\n13", "-1", "1e3", "abc"):
            with self.subTest(run_id=value):
                code, out, log = resolve("workflow_dispatch", dispatch=value)
                self.assertNotEqual(code, 0, log)
                self.assertEqual(out, "")
                self.assertIn("run_id must be the numeric ID", log)
                if value.strip():
                    # The log carries no copy of the value: a workflow-command
                    # line typed into the input must not reach the annotations.
                    self.assertNotIn(value.strip(), log)
                self.assertFalse(canary.exists(), "the run ID reached the shell")
        # A workflow_run event never consults the manual input.
        code, out, _ = resolve("workflow_run", dispatch=hostile[0], run="7")
        self.assertEqual((code, out), (0, "run_id=7"))
        self.assertFalse(canary.exists())

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

    def test_startup_failure_does_not_open_a_selector_issue_or_patch(self):
        text = self.workflow("firefox-watch.yml")
        values = {"steps.audit.outcome": "success", "steps.audit.outputs.status": "2",
                  "steps.audit.outputs.patch": "yes", "steps.smoke.outcome": "success",
                  "steps.smoke.outputs.status": "2"}
        for name in ("Open or update the heads-up issue", "Open a pull request with the confident fixes"):
            guard = re.search(r"^        if: (.+)$", step(text, name), re.M)[1]
            self.assertFalse(condition(guard, values), name)
        for name in ("Open or update the capture-pipeline issue", "Fail when Firefox could not be checked"):
            guard = re.search(r"^        if: (.+)$", step(text, name), re.M)[1]
            self.assertTrue(condition(guard, values), name)
        values["steps.smoke.outputs.status"] = "0"
        guard = re.search(r"^        if: (.+)$", step(text, "Fail when Firefox could not be checked"), re.M)[1]
        self.assertTrue(condition(guard, values))
        values["steps.audit.outputs.status"] = "1"
        self.assertFalse(condition(guard, values))

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


    def platform_artifact(self, label, changes, changed=("light-01-window", "dark-01-window")):
        artifact = self.root / f"artifacts/preview-{label}"
        captures = artifact / label
        (captures / "full").mkdir(parents=True)
        (artifact / "pr-number.txt").write_text("7")
        (artifact / "head-sha.txt").write_text("a" * 40)
        views = []
        for name in changed:
            (captures / f"{name}.png").write_bytes(PNG)
            (captures / f"full/{name}.png").write_bytes(PNG)
            views.append({"view": name, "percent": 0.62, "changed_pixels": 9, "image": f"{name}.png"})
        for change in changes:
            if isinstance(change.get("image"), str) and "/" not in change["image"]:
                (captures / change["image"]).write_bytes(PNG)
        (captures / "summary.json").write_text(json.dumps({
            "views": views, "changes": changes, "only_in_head": [], "only_in_base": [],
            "unsupported": ["light-08-app-menu"]}))

    @unittest.skipUnless(shutil.which("node"), "publisher scripts require Node")
    def test_a_change_on_every_platform_is_pictured_once_under_its_category(self):
        tabs = {"image": "change-01.png", "category": "Tabs", "views": ["light-01-window", "dark-01-window"],
                "box": [0, 0, 960, 40], "frame": [960, 360], "width": 960, "height": 140}
        self.platform_artifact("macos", [tabs])
        self.platform_artifact("windows", [dict(tabs, box=[0, 2, 960, 44])])
        self.platform_artifact("linux", [dict(tabs, image="change-01.png", category="Panels",
                                              views=["light-01-window"], box=[10, 10, 50, 50])],
                               changed=("light-01-window",))
        data = self.publish()
        self.assertEqual(data["errors"], [])
        body = data["body"]
        self.assertEqual(body.count("<img"), 2, body)
        self.assertIn("**2 distinct changes** across 5 changed views on macOS, Windows and Linux", body)
        self.assertLess(body.index("#### Tabs"), body.index("#### Panels"))
        self.assertIn("**1. Browser window — light** and 1 more view · only on macOS and Windows", body)
        self.assertIn("/macos/change-01.png", body)
        self.assertNotIn("/windows/change-01.png", body)
        self.assertIn("2 views on macOS · 2 views on Windows", body)
        self.assertIn("**2. Browser window — light** · only on Linux", body)
        self.assertIn("[Browser window — dark](https://raw.githubusercontent.com/test/theme/"
                      "ci-previews/pr-7/" + "a" * 40 + "/windows/dark-01-window.png) (0.62%)", body)
        self.assertIn("Not captured on this runner, so not compared: App menu — light.", body)

    @unittest.skipUnless(shutil.which("node"), "publisher scripts require Node")
    def test_a_huge_run_still_fits_in_one_comment(self):
        names = [f"variant-option-{i:03d}" for i in range(250)]
        change = {"image": "change-01.png", "category": "Optional stylesheets", "views": names,
                  "box": [0, 0, 960, 40], "frame": [960, 360], "width": 960}
        for label in ("macos", "windows", "linux"):
            self.platform_artifact(label, [change], changed=names)
        data = self.publish()
        self.assertEqual(data["errors"], [])
        self.assertLess(len(data["body"]), 65536)
        self.assertIn("Views are listed without links to fit GitHub's comment size", data["body"])
        self.assertIn("Variant: option-249 (0.62%)", data["body"])

    @unittest.skipUnless(shutil.which("node"), "publisher scripts require Node")
    def test_change_records_from_the_artifact_are_revalidated(self):
        good = {"image": "change-01.png", "category": "Tabs", "views": ["light-01-window"],
                "box": [0, 0, 10, 10], "frame": [960, 360], "width": 960}
        hostile = [dict(good, image="change-02.png", category="<script>"),
                   dict(good, image="../../change-03.png"),
                   dict(good, image="change-04.png", views=["<img src=x>", "constructor"]),
                   dict(good, image="change-05.png", box="0,0,1,1")]
        self.platform_artifact("macos", [good] + hostile)
        data = self.publish()
        self.assertEqual(data["errors"], [])
        body = data["body"]
        self.assertIn("4 unreviewed screenshot(s)", body)
        for text in ("<script>", "change-02", "change-03", "change-04", "change-05",
                     "<img src=x>", "constructor"):
            self.assertNotIn(text, body)
        self.assertIn("/macos/change-01.png", body)
        # A changed view no valid record accounts for is still pictured.
        self.assertIn("/macos/dark-01-window.png\" width=", body)

    def publish(self):
        text = self.workflow("pr-preview-publish.yml")
        # The harness passes these outputs on the way the comment step's env does.
        for key, output in (("REPORT", "report"), ("SUPPORT", "support")):
            self.assertIn(f"{key}: ${{{{ steps.check.outputs.{output} }}}}",
                          step(text, "Upsert pull request comment"))
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
  process.env.SUPPORT = outputs.support || '';
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
