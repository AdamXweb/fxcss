"""Layout plans, provenance and setup checklists through the command line.

Offline reproductions of the 132-theme evaluation's findings: flat
repositories install and upgrade; a user-added file survives an upgrade;
an unchanged local source is "up to date"; optional sheets keep their
relative-path identity; branch URLs, renames and other hosts parse; a
guessed repository with a weak match is not recorded; shipped preference
files are previewed, applied in the managed block and carried through.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fxcss import checklist, cli, fetch, install


def _write(root, files):
    for relative, text in files.items():
        path = Path(root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
    return Path(root)


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.dict(os.environ, {"CI": "1"}):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class FlatThemeLifecycleTests(unittest.TestCase):
    """A repository with userChrome.css at its root, like ArcWTF or Foxlim."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = _write(self.root / "repo", {
            "userChrome.css": '@import "theme/base.css";\n#nav-bar { background: url(theme/i.svg) }\n',
            "theme/base.css": ":root { --v: 1 }\n",
            "theme/i.svg": "<svg/>",
            "tweaks/hide-tabs.css": "#TabsToolbar { display: none }\n",
            "README.md": "# Theme\nCopy user.js into your profile folder.\n",
            "user.js": 'user_pref("browser.tabs.a", true);\nuser_pref("ui.b", 1);\n',
            "LICENSE": "MIT", "screenshots/a.png": b"png",
        })
        self.profile = _write(self.root / "profile", {
            "prefs.js": "", "user.js": b'user_pref("mine", 1);\r\n',
            "chrome/userChrome.css": "/* mine */\n"})

    def test_install_upgrade_and_uninstall_a_flat_repository(self):
        code, out, err = run_cli("install", str(self.repo), "--profile", str(self.profile),
                                 "--yes", "--with", "hide-tabs", "--prefs", "user.js")
        self.assertEqual(code, 0, err)
        chrome = self.profile / "chrome"
        self.assertTrue((chrome / "userChrome.css").is_file())
        self.assertTrue((chrome / "theme" / "i.svg").is_file())
        self.assertFalse((chrome / "LICENSE").exists())
        self.assertFalse((chrome / "screenshots").exists())
        manifest = install.read_manifest(self.profile)
        self.assertEqual(manifest["layout"], {"kind": "flat", "subpath": None, "entry": None})
        self.assertEqual(manifest["pref_files"], ["user.js"])
        self.assertEqual(manifest["sheet_paths"], {"hide-tabs": "tweaks/hide-tabs.css"})
        self.assertEqual(manifest["source"]["path"], str(self.repo.resolve()))
        prefs = (self.profile / "user.js").read_text()
        self.assertIn('user_pref("browser.tabs.a", true);', prefs)
        self.assertIn("applied   preferences in user.js", out)

        # Unchanged source: no update announced.
        code, out, _ = run_cli("upgrade", "--profile", str(self.profile), "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("Up to date", out)

        # A user-added file, then a real change upstream.
        (chrome / "tweaks" / "mine.css").write_text("#mine { }\n")
        (self.repo / "theme" / "base.css").write_text(":root { --v: 2 }\n")
        code, out, _ = run_cli("upgrade", "--profile", str(self.profile), "--check")
        self.assertEqual(code, 1, out)
        self.assertIn("chrome/theme/base.css", out)
        code, out, err = run_cli("upgrade", "--profile", str(self.profile), "--yes")
        self.assertEqual(code, 0, err)
        self.assertTrue((chrome / "tweaks" / "mine.css").is_file())
        self.assertIn("keeping optional sheets: hide-tabs", out)
        self.assertIn("keeping preference file(s): user.js", out)
        manifest = install.read_manifest(self.profile)
        self.assertEqual(manifest["user_files"], ["chrome/tweaks/mine.css"])
        self.assertEqual((chrome / "theme" / "base.css").read_text(), ":root { --v: 2 }\n")

        # The chosen pref file disappears upstream: that is a stop, not silence.
        (self.repo / "user.js").unlink()
        code, _, err = run_cli("upgrade", "--profile", str(self.profile), "--yes")
        self.assertEqual(code, 2)
        self.assertIn("no preference file at user.js", err)
        code, _, err = run_cli("upgrade", "--profile", str(self.profile), "--yes", "--prefs", "")
        self.assertEqual(code, 0, err)
        self.assertNotIn("browser.tabs.a", (self.profile / "user.js").read_text())

        code, out, _ = run_cli("uninstall", "--profile", str(self.profile), "--yes")
        self.assertEqual(code, 0)
        self.assertTrue((chrome / "tweaks" / "mine.css").is_file())
        self.assertEqual((self.profile / "user.js").read_bytes(), b'user_pref("mine", 1);\r\n')

    def test_preference_only_local_updates_are_detected_and_applied(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                relative = "config/user.js" if explicit else "configuration/user.js"
                repo = _write(root / "repo", {
                    "chrome/userChrome.css": "#nav-bar {}\n",
                    relative: 'user_pref("theme.setting", 1);\n',
                })
                profile = _write(root / "profile", {"prefs.js": ""})
                selection = ["--prefs", relative] if explicit else []
                code, _, err = run_cli("install", str(repo), "--profile", str(profile),
                                      "--yes", *selection)
                self.assertEqual(code, 0, err)
                (repo / relative).write_text('user_pref("theme.setting", 2);\n')
                code, out, err = run_cli("upgrade", "--profile", str(profile), "--check")
                self.assertEqual(code, 1, out + err)
                self.assertIn("preference block", out)
                code, out, err = run_cli("upgrade", "--profile", str(profile), "--yes")
                self.assertEqual(code, 0, out + err)
                self.assertIn('user_pref("theme.setting", 2);',
                              (profile / "user.js").read_text())
                self.assertEqual(run_cli("upgrade", "--profile", str(profile), "--check")[0], 0)

    def test_sheet_continuity_matches_the_relative_path_first(self):
        _write(self.repo, {"optional/hide-tabs.css": "/* a second sheet with the same stem */\n"})
        code, _, err = run_cli("install", str(self.repo), "--profile", str(self.profile),
                               "--yes", "--with", "tweaks/hide-tabs.css")
        self.assertEqual(code, 0, err)
        manifest = install.read_manifest(self.profile)
        self.assertEqual(manifest["sheet_paths"], {"hide-tabs": "tweaks/hide-tabs.css"})
        (self.repo / "theme" / "base.css").write_text("changed\n")
        code, out, err = run_cli("upgrade", "--profile", str(self.profile), "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(install.read_manifest(self.profile)["sheet_paths"],
                         {"hide-tabs": "tweaks/hide-tabs.css"})

    def test_unknown_pref_file_is_refused_with_the_shipped_list(self):
        code, _, err = run_cli("install", str(self.repo), "--profile", str(self.profile),
                               "--yes", "--prefs", "nope.js")
        self.assertEqual(code, 2)
        self.assertIn("nope.js", err)
        self.assertIn("user.js", err)
        self.assertFalse(install.read_manifest(self.profile))


class EditionSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = _write(self.root / "repo", {
            "1.Normal/chrome/userChrome.css": "#a { }\n",
            "2.WithTST/chrome/userChrome.css": "#b { }\n",
            "3.Old Versions/1.0/userChrome.css": "#c { }\n",
        })
        self.profile = _write(self.root / "profile", {"prefs.js": ""})

    def test_ambiguous_editions_stop_without_a_terminal(self):
        code, _, err = run_cli("install", str(self.repo), "--profile", str(self.profile), "--yes")
        self.assertEqual(code, 2)
        self.assertIn("--subpath", err)
        self.assertIn("1.Normal", err)
        self.assertFalse((self.profile / "chrome").exists())

    def test_subpath_is_recorded_and_reused_by_upgrade(self):
        code, _, err = run_cli("install", str(self.repo), "--profile", str(self.profile),
                               "--yes", "--subpath", "2.WithTST")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.profile / "chrome" / "userChrome.css").read_text(), "#b { }\n")
        self.assertEqual(install.read_manifest(self.profile)["layout"]["subpath"], "2.WithTST")
        (self.repo / "2.WithTST" / "chrome" / "userChrome.css").write_text("#b2 { }\n")
        (self.repo / "1.Normal" / "chrome" / "userChrome.css").write_text("#a2 { }\n")
        code, _, err = run_cli("upgrade", "--profile", str(self.profile), "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.profile / "chrome" / "userChrome.css").read_text(), "#b2 { }\n")

    def test_incomplete_entry_imports_need_force(self):
        repo = _write(self.root / "built", {
            "chrome/userChrome.css": '@import "user-chrome.css";\n@import "custom.css";\n',
            "chrome/theme.css": ""})
        code, _, err = run_cli("install", str(repo), "--profile", str(self.profile), "--yes")
        self.assertEqual(code, 2)
        self.assertIn("user-chrome.css", err)
        self.assertIn("needs building", err)
        self.assertFalse((self.profile / "chrome").exists())
        code, _, err = run_cli("install", str(repo), "--profile", str(self.profile), "--yes", "--force")
        self.assertEqual(code, 0, err)

    def test_try_info_reports_plan_and_checklist(self):
        code, out, _ = run_cli("try", str(self.repo), "--info", "--subpath", "1.Normal")
        self.assertEqual(code, 0)
        self.assertIn("theme: 1.Normal", out)
        self.assertIn("other editions here", out)


class ProvenanceTests(unittest.TestCase):
    def test_branch_and_subpath_urls_parse(self):
        parsed = fetch.parse_repo_spec("https://github.com/black7375/Firefox-UI-Fix/tree/photon-style")
        self.assertEqual((parsed["owner"], parsed["name"], parsed["ref"], parsed["subpath"]),
                         ("black7375", "Firefox-UI-Fix", "photon-style", None))
        parsed = fetch.parse_repo_spec("https://github.com/o/n/tree/main/themes/one")
        self.assertEqual((parsed["ref"], parsed["subpath"]), ("main", "themes/one"))
        self.assertEqual(fetch.parse_repo("https://github.com/o/n/tree/main/themes/one"), ("o", "n"))
        for url, host, kind in (("https://codeberg.org/a/b", "codeberg.org", "forgejo"),
                                ("https://git.gay/freeplay/Firefox-Onebar", "git.gay", "forgejo"),
                                ("https://gitlab.com/ivelieu/dark-star-firefox-skin", "gitlab.com", "gitlab"),
                                ("https://gitlab.com/o/n/-/tree/dev/sub", "gitlab.com", "gitlab")):
            parsed = fetch.parse_repo_spec(url)
            self.assertEqual((parsed["host"], parsed["kind"]), (host, kind), url)
        with self.assertRaises(ValueError):
            fetch.parse_repo("https://codeberg.org/a/b")
        with self.assertRaises(ValueError):
            fetch.parse_repo_spec("https://example.com/a/b")

    def test_archive_urls_per_host(self):
        self.assertEqual(fetch.archive_url("o", "n", "v1", "codeberg.org"),
                         "https://codeberg.org/o/n/archive/v1.tar.gz")
        self.assertEqual(fetch.archive_url("o", "n", "main", "gitlab.com"),
                         "https://gitlab.com/o/n/-/archive/main/n-main.tar.gz")
        self.assertEqual(fetch.archive_url("o", "n", "main"),
                         "https://codeload.github.com/o/n/tar.gz/main")

    def test_tokens_only_travel_to_github(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_secret"}):
            self.assertIn("Authorization", fetch._request("https://api.github.com/x").headers)
            self.assertNotIn("Authorization",
                             fetch._request("https://codeberg.org/api/v1/x").headers)
            handler = fetch._NoLeakRedirects()
            request = fetch._request("https://codeload.github.com/o/n/tar.gz/main")
            moved = handler.redirect_request(request, None, 302, "Found", {},
                                             "https://evil.example/x")
            self.assertNotIn("Authorization", moved.headers)
            kept = handler.redirect_request(request, None, 302, "Found", {},
                                            "https://objects.githubusercontent.com/x")
            self.assertIn("Authorization", kept.headers)

    def test_branch_install_records_the_full_commit_and_is_current(self):
        info = {"release": None, "default_branch": "main",
                "commit": {"sha": "abc1234", "full_sha": "abc1234" + "0" * 33,
                           "date": "2026-10-01", "message": "m"},
                "tracked": {"ref": "photon-style", "sha": "8858a51",
                            "full_sha": "8858a51" + "f" * 33, "date": "2026-10-10", "message": "x"}}
        source = cli._install_source("o", "n", "main", info, explicit=False)
        self.assertEqual(source["ref_kind"], "branch")
        self.assertEqual(source["resolved"], "abc1234" + "0" * 33)
        self.assertEqual(fetch.update_state(source, info)["state"], "current")
        tracked = cli._install_source("o", "n", "photon-style", info, explicit=False,
                                      branch="photon-style")
        self.assertEqual((tracked["ref_kind"], tracked["resolved"]),
                         ("branch", "8858a51" + "f" * 33))
        self.assertEqual(fetch.update_state(tracked, info)["state"], "current")
        moved = dict(info, tracked=dict(info["tracked"], full_sha="1" * 40, sha="1111111"))
        self.assertEqual(fetch.update_state(tracked, moved)["state"], "available")
        # A seven-character record from an older manifest still matches.
        old = dict(source, resolved="abc1234")
        self.assertEqual(fetch.update_state(old, info)["state"], "current")
        self.assertTrue(fetch.same_commit("abc1234", "abc1234" + "0" * 33))
        self.assertFalse(fetch.same_commit("abc1234", "abd1234" + "0" * 33))
        self.assertFalse(fetch.same_commit("", "abc"))

    def test_release_tracking_reports_the_branch_without_switching(self):
        info = {"release": {"tag": "v2", "date": "2026-01-01T00:00:00Z"},
                "default_branch": "main",
                "commit": {"sha": "abc1234", "full_sha": "abc1234", "date": "2026-02-01T00:00:00Z",
                           "message": "later"}}
        got = fetch.update_state({"kind": "github", "ref_kind": "release", "ref": "v2"}, info)
        self.assertEqual(got["state"], "current")
        self.assertIn("main has moved on", got["also"])
        self.assertEqual(got["ref"], "v2")

    def test_renamed_repository_resolves_to_its_canonical_name(self):
        def api(path):
            if path == "/repos/old/name":
                return {"full_name": "new/name", "default_branch": "main"}
            if path == "/repos/new/name/commits/main":
                return {"sha": "f" * 40, "commit": {"author": {"date": "2026"}, "message": "m"}}
            return None
        with mock.patch.object(fetch, "_api", side_effect=api):
            info = fetch.resolve("old", "name")
        self.assertEqual((info["owner"], info["name"], info["renamed_from"]),
                         ("new", "name", "old/name"))
        source = cli._install_source(info["owner"], info["name"], "main", info, explicit=False)
        self.assertEqual((source["owner"], source["renamed_from"]), ("new", "old/name"))

    def test_rate_limit_retries_with_the_cli_login_once(self):
        import urllib.error
        calls = []

        def urlopen(request, timeout=None):
            calls.append(request.get_header("Authorization"))
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 403, "rate limited",
                                             {"X-RateLimit-Remaining": "0"}, io.BytesIO(b""))
            return contextlib.closing(io.BytesIO(b'{"default_branch": "main"}'))

        done = mock.Mock(returncode=0, stdout="gho_cli\n")
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.dict(fetch._cli_login, {"token": None, "tried": False}), \
                mock.patch.object(fetch.urllib.request, "urlopen", side_effect=urlopen), \
                mock.patch.object(fetch.shutil, "which", return_value="/usr/bin/gh"), \
                mock.patch.object(fetch.subprocess, "run", return_value=done):
            os.environ.pop("GITHUB_TOKEN", None)
            os.environ.pop("GH_TOKEN", None)
            self.assertEqual(fetch._api("/repos/o/n"), {"default_branch": "main"})
        self.assertEqual(calls, [None, "Bearer gho_cli"])

    def test_rate_limit_message_never_shows_the_token(self):
        import urllib.error

        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "rate limited",
                                         {"X-RateLimit-Remaining": "0"}, io.BytesIO(b""))
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_supersecret"}), \
                mock.patch.object(fetch.urllib.request, "urlopen", side_effect=urlopen):
            with self.assertRaises(fetch.FetchError) as caught:
                fetch._api("/repos/o/n")
        self.assertNotIn("ghp_supersecret", str(caught.exception))
        self.assertIn("GITHUB_TOKEN", str(caught.exception))


class AdoptGuessTests(unittest.TestCase):
    def test_a_weak_guessed_match_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _write(Path(td) / "p", {
                "prefs.js": "",
                "chrome/userChrome.css": "/* palette ideas from https://github.com/akshat46/FlyingFox */\n",
                "chrome/a.css": "a", "chrome/b.css": "b", "chrome/c.css": "c"})
            tree = {"chrome/userChrome.css": "0" * 40, "chrome/a.css": "1" * 40,
                    "chrome/b.css": "2" * 40, "chrome/c.css": "3" * 40,
                    "chrome/d.css": "4" * 40, "chrome/e.css": "5" * 40}
            from fxcss import adopt
            tree["chrome/a.css"] = adopt.blob_sha(b"a")
            with mock.patch.object(fetch, "known_refs", return_value=[{"ref": "v1", "kind": "release", "sha": None}]), \
                    mock.patch.object(fetch, "tree", return_value=tree):
                code, out, err = run_cli("adopt", "--profile", str(profile), "--yes")
            self.assertEqual(code, 2, out + err)
            self.assertIn("guessed from a mention", err)
            self.assertFalse(install.read_manifest(profile))
            # Named explicitly, the same weak match is the person's call.
            with mock.patch.object(fetch, "known_refs", return_value=[{"ref": "v1", "kind": "release", "sha": None}]), \
                    mock.patch.object(fetch, "tree", return_value=tree):
                code, out, err = run_cli("adopt", "akshat46/FlyingFox", "--profile", str(profile), "--yes")
            self.assertEqual(code, 0, out + err)

    def test_adopting_a_branch_records_its_commit(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _write(Path(td) / "p", {"prefs.js": "", "chrome/userChrome.css": "x"})
            from fxcss import adopt
            tree = {"chrome/userChrome.css": adopt.blob_sha(b"x")}
            refs = [{"ref": "main", "kind": "branch", "sha": "d" * 40}]
            with mock.patch.object(fetch, "known_refs", return_value=refs), \
                    mock.patch.object(fetch, "tree", return_value=tree):
                code, out, err = run_cli("adopt", "o/n", "--profile", str(profile), "--yes")
            self.assertEqual(code, 0, out + err)
            source = install.read_manifest(profile)["source"]
            self.assertEqual((source["ref_kind"], source["resolved"]), ("branch", "d" * 40))
            info = {"release": None, "default_branch": "main",
                    "commit": {"sha": "ddddddd", "full_sha": "d" * 40}}
            self.assertEqual(fetch.update_state(source, info)["state"], "current")


class ChecklistTests(unittest.TestCase):
    def test_checklist_states_and_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _write(Path(td), {
                "chrome/userChrome.css": '#a { } @media (-moz-bool-pref("uc.x")) { #b { } }\n',
                "chrome/script.uc.js": "// loader needed",
                "configuration/user.js": 'user_pref("applied.one", 1);\n',
                "user.js": 'user_pref("root.one", 1);\nuser_pref("root.two", 2);\n',
                "prefs/recommended.js": 'user_pref("rec.one", 1);\n',
                "sidebery-settings.json": "{}",
                "README.md": "Install fx-autoconfig (required).\nCopy user.js to your profile.\n"
                             "Set browser.tabs.allow_transparent_browser to true.\n",
            })
            items = checklist.build(repo, repo)

            def state_of(prefix):
                found = [i for i in items if i["item"].startswith(prefix)]
                self.assertEqual(len(found), 1, prefix)
                return found[0]["state"]

            self.assertEqual(state_of("preferences in configuration/user.js"), "applied")
            self.assertEqual(state_of("preferences in user.js"), "required")
            self.assertEqual(state_of("preferences in prefs/recommended.js"), "optional")
            self.assertEqual(state_of("a userChrome.js loader for 1 script"), "required")
            self.assertEqual(state_of("a userChrome.js loader (fx-autoconfig"), "required")
            self.assertEqual(state_of("extension settings the theme ships"), "unknown")
            self.assertEqual(state_of("1 -moz-pref() toggle"), "optional")
            mentioned = [i for i in items if "mentioned in the README" in i["item"]][0]
            self.assertIn("browser.tabs.allow_transparent_browser", mentioned["evidence"])
            self.assertFalse(checklist.complete(items))
            lines = checklist.render(items)
            self.assertTrue(lines[0].strip().startswith("setup beyond the files"))
            applied = checklist.build(repo, repo, applied_pref_files=["user.js"])
            self.assertEqual([i["state"] for i in applied if i["item"].startswith("preferences in user.js")],
                             ["applied"])

    def test_nothing_to_say_for_a_plain_theme(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _write(Path(td), {"chrome/userChrome.css": "#a { }\n", "README.md": "Hi.\n"})
            self.assertEqual(checklist.build(repo, repo), [])
            self.assertEqual(checklist.render([]), [])
            self.assertTrue(checklist.complete([]))


class InitLayoutTests(unittest.TestCase):
    def assert_preview_triggers_cover_all_dependencies(self, workflows):
        for name in ("pr-preview.yml", "readme-previews.yml"):
            with self.subTest(workflow=name):
                text = (workflows / name).read_text()
                triggers = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
                self.assertNotRegex(triggers, r"(?m)^\s+paths(?:-ignore)?:")
                self.assertNotIn("__FXCSS_", text)
                self.assertIn("all repository changes", text)

    def test_nested_theme_gets_workflows_at_the_repository_root(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            repo = _write(Path(td) / "repo", {
                "firefox/chrome/userChrome.css": "#a { }\n", "README.md": "x"})
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            code, out, err = run_cli("init", "--theme", str(repo), "--watch", "--previews")
            self.assertEqual(code, 0, err)
            workflows = repo / ".github" / "workflows"
            self.assertTrue((workflows / "pr-preview.yml").is_file())
            self.assertFalse((repo / "firefox" / ".github").exists())
            text = (workflows / "pr-preview.yml").read_text()
            self.assertIn("--theme 'head/firefox'", text)
            self.assertIn("--theme 'base/firefox'", text)
            self.assert_preview_triggers_cover_all_dependencies(workflows)
            previews = (workflows / "readme-previews.yml").read_text()
            self.assertIn("--theme 'firefox'", previews)
            watch = (workflows / "firefox-watch.yml").read_text()
            self.assertIn("fxcss audit --theme 'firefox'", watch)
            self.assertIn("--head 'firefox'", watch)
            self.assertIn("--base \"$before\"/'firefox'", watch)

    def test_flat_theme_previews_cover_all_repository_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _write(Path(td) / "repo", {"userChrome.css": "#a { }\n"})
            code, out, err = run_cli("init", "--theme", str(repo), "--previews")
            self.assertEqual(code, 0, err)
            text = (repo / ".github" / "workflows" / "pr-preview.yml").read_text()
            self.assert_preview_triggers_cover_all_dependencies(repo / ".github" / "workflows")
            self.assertIn("--theme head ", text)
            self.assertIn("must be published on PyPI", out)

    def test_several_editions_need_a_subpath(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _write(Path(td) / "repo", {"a/chrome/userChrome.css": "", "b/chrome/userChrome.css": ""})
            code, _, err = run_cli("init", "--theme", str(repo))
            self.assertEqual(code, 2)
            self.assertIn("--subpath", err)
            code, _, err = run_cli("init", "--theme", str(repo), "--subpath", "b")
            self.assertEqual(code, 0, err)
            text = (repo / ".github" / "workflows" / "pr-preview.yml").read_text()
            self.assertIn("--theme 'head/b'", text)


if __name__ == "__main__":
    unittest.main()
