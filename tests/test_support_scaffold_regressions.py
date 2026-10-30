"""Offline regressions for support failures and public workflow defaults."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fxcss import cli, scaffold, support


class PublicRepoTests(unittest.TestCase):
    def test_transport_credentials_and_metadata_are_not_public_urls(self):
        cases = {
            "https://invented-user:invented-password@code.example:8443/o/r.git?token=invented#private":
                "https://code.example:8443/o/r",
            "http://invented-token@code.example/o/r.git/": "http://code.example/o/r",
            "https://invented:password@[2001:db8::1]:8443/o/r.git":
                "https://[2001:db8::1]:8443/o/r",
            "ssh://invented@code.example:2222/o/r.git": "https://code.example/o/r",
            "git@code.example:o/r.git": "https://code.example/o/r",
        }
        for remote, expected in cases.items():
            with self.subTest(remote=remote):
                self.assertEqual(scaffold.https_repo_url(remote), expected)
        for remote in ("https:///o/r", "https://code.example:bad/o/r", "https://[",
                       "https://code.example/o/r\nother", "https://code.example"):
            with self.subTest(remote=remote):
                self.assertIsNone(scaffold.https_repo_url(remote))

    def test_generated_showcase_defaults_strip_secrets_and_quote_apostrophes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            remote = "https://invented-user:invented-password@code.example/o/it's.git?invented-query#private"
            with patch.object(scaffold.subprocess, "run") as git:
                git.return_value.stdout = remote
                scaffold.write_workflows(root, [], showcase=True)
            text = (root / ".github/workflows/showcase.yml").read_text()
        self.assertIn("default: 'https://code.example/o/it''s'", text)
        self.assertIn("DEFAULT_URLS: >-\n    https://code.example/o/it's\n", text)
        self.assertNotIn("invented", text)
        self.assertNotIn("#private", text)
        self.assertIn("INPUT_URLS: ${{ github.event.inputs.urls }}", text)
        self.assertIn('urls="$INPUT_URLS"', text)


class SupportReportTests(unittest.TestCase):
    def test_invalid_oldest_is_reported_before_build_or_network_access(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(support, "build_version", return_value="158.0") as version, \
                    patch.object(support, "release_info") as releases, \
                    patch.object(support, "_open", side_effect=AssertionError("network")):
                self.assert_unavailable(root, "foo", 0, "oldest Firefox must be")
            version.assert_not_called()
            releases.assert_not_called()

    def assert_unavailable(self, root, oldest, budget, reason):
        out = io.StringIO()
        json_path, md_path = root / "reports/support.json", root / "reports/support.md"
        with contextlib.redirect_stdout(out):
            code = cli.main(["support", "--base", str(root / "base"), "--head", str(root / "head"),
                             "--firefox", str(root / "firefox"), "--oldest", oldest,
                             "--max-downloads", str(budget), "--cache", str(root / "cache"),
                             "--json", str(json_path), "--markdown", str(md_path)])
        self.assertEqual(code, 2)
        report = json.loads(json_path.read_text())
        self.assertEqual(report["status"], "unavailable")
        self.assertIn(reason, report["reason"])
        self.assertIn(reason, md_path.read_text())
        self.assertIn("could not be checked", out.getvalue())

    def test_budget_exhaustion_is_reported_at_every_initial_build_load(self):
        # Newest download, oldest after newest, oldest with a local newest,
        # and the unchanged-theme branch must all produce reports.
        for changed, local, budget, exhausted in ((True, False, 0, "158.0"),
                                                  (True, False, 1, "140.0"),
                                                  (True, True, 0, "140.0"),
                                                  (False, False, 0, "140.0")):
            with self.subTest(changed=changed, local=local, budget=budget), \
                    tempfile.TemporaryDirectory() as td:
                root = Path(td)
                for name, css in (("base", "#old {}"), ("head", "#new {}" if changed else "#old {}")):
                    (root / name / "chrome").mkdir(parents=True)
                    (root / name / "chrome/userChrome.css").write_text(css)
                index = {"ids": {"new"}, "classes": set(), "properties": set()}
                with patch.object(support, "build_version", return_value="158.0"), \
                        patch.object(support, "release_info", return_value={"esr": 140}), \
                        patch.object(support.sys, "platform", "linux" if local else "darwin"), \
                        patch.object(support.omni, "pack_paths", return_value=[root / "omni.ja"] if local else []), \
                        patch.object(support.omni, "chrome_names", return_value=index), \
                        patch.object(support, "download_names", return_value=index) as download, \
                        patch.object(support, "_open", side_effect=AssertionError("network")):
                    self.assert_unavailable(root, "140", budget,
                                            f"download budget ran out at Firefox {exhausted}")
                    self.assertEqual(download.call_count, budget)
