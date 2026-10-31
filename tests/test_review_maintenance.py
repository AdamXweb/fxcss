"""Regressions found when reviewing the 132-theme maintenance changes."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fxcss import audit, cli, fetch, install, inventory, layout, scaffold


def write(root, files):
    root = Path(root)
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return root


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.dict(os.environ, {"CI": "1"}):
        status = cli.main(list(argv))
    return status, out.getvalue() + err.getvalue()


class ReviewMaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.theme = write(self.root / "theme", {"chrome/userChrome.css": "#base {}\n"})
        self.profile = write(self.root / "profile", {"prefs.js": "", "user.js": b'// original\r\n\xff'})

    def test_repeat_uninstall_never_reactivates_an_older_managed_backup(self):
        for original in (False, True):
            with self.subTest(original=original):
                p = write(self.root / str(original), {"prefs.js": ""})
                if original:
                    write(p, {"chrome/mine.css": b"original\xff"})
                first = install.install_theme(self.theme, p, "o/n@a", stamp="20260101000000")
                install.install_theme(self.theme, p, "o/n@b", stamp="20260102000000",
                                      origin_backup=first["origin_backup"])
                install.uninstall_theme(p)
                for _ in range(2):
                    result = install.uninstall_theme(p)
                    self.assertTrue(result["noop"])
                    self.assertIsNone(result["restored"])
                    self.assertIsNone(install.read_manifest(p))
                    self.assertEqual((p / "chrome").exists(), original)
                    if original:
                        self.assertEqual((p / "chrome/mine.css").read_bytes(), b"original\xff")

    def test_same_filename_options_keep_both_bodies_assets_and_ids_after_upgrade(self):
        write(self.theme, {
            "custom/tabs.css": '#a { background: url(icon.svg) }\n', "custom/icon.svg": "red",
            "optional/tabs.css": '#b { background: url(icon.svg) }\n', "optional/icon.svg": "blue",
        })
        status, text = run("install", str(self.theme), "--profile", str(self.profile), "--yes",
                           "--with", "custom/tabs.css,optional/tabs.css")
        self.assertEqual(status, 0, text)
        manifest = install.read_manifest(self.profile)
        self.assertEqual(manifest["sheet_ids"], ["custom/tabs.css", "optional/tabs.css"])
        for changes in (False, True):
            if changes:
                write(self.theme, {"chrome/userChrome.css": "#changed {}\n"})
                status, text = run("upgrade", "--profile", str(self.profile), "--yes")
                self.assertEqual(status, 0, text)
            self.assertEqual(run("upgrade", "--profile", str(self.profile), "--check")[0], 0)
            sheets = list((self.profile / "chrome").rglob("tabs.css"))
            self.assertEqual(len(sheets), 2)
            self.assertEqual({p.read_text() for p in sheets},
                             {'#a { background: url(icon.svg) }\n', '#b { background: url(icon.svg) }\n'})
            for p in sheets:
                expected = "red" if "#a" in p.read_text() else "blue"
                self.assertEqual((p.parent / "icon.svg").read_text(), expected)
            self.assertEqual(install.read_manifest(self.profile)["sheet_ids"], manifest["sheet_ids"])

    def test_ambiguous_stem_is_refused_before_install(self):
        write(self.theme, {"custom/tabs.css": "#a{}", "optional/tabs.css": "#b{}"})
        status, text = run("install", str(self.theme), "--profile", str(self.profile), "--yes", "--with", "tabs")
        self.assertEqual(status, 2, text)
        self.assertIn("ambiguous", text)
        self.assertIn("custom/tabs.css", text)
        self.assertIn("optional/tabs.css", text)
        self.assertFalse((self.profile / "chrome").exists())

    def test_two_options_cannot_replace_each_other_in_one_native_slot_even_with_force(self):
        write(self.theme, {"chrome/userChrome.css": '@import "custom/tabs.css";\n',
                           "custom/tabs.css": "#a{}", "optional/tabs.css": "#b{}"})
        before = (self.profile / "user.js").read_bytes()
        status, text = run("install", str(self.theme), "--profile", str(self.profile), "--yes",
                           "--with", "custom/tabs.css,optional/tabs.css", "--force")
        self.assertEqual(status, 2, text)
        self.assertIn("native import slot", text)
        self.assertFalse((self.profile / "chrome").exists())
        self.assertEqual((self.profile / "user.js").read_bytes(), before)
        self.assertFalse(list(self.profile.glob("chrome.backup-*")))

    def test_installer_uses_shared_escaped_import_and_asset_targets(self):
        write(self.theme, {"chrome/userChrome.css": '@import url("custom/t\\61 bs.css") screen;\n',
                           "custom/tabs.css": '@import url(parts/rules\\20 one.css) screen;\n',
                           "custom/parts/rules one.css": '#a {background:url(i\\ con.svg)}\n',
                           "custom/parts/i con.svg": "asset"})
        sheet = self.theme / "custom/tabs.css"
        install.install_theme(self.theme, self.profile, "theme", sheets=[sheet])
        self.assertEqual((self.profile / "chrome/custom/parts/i con.svg").read_text(), "asset")
        found = inventory.scan(self.profile / "chrome")
        self.assertTrue(found["complete"], found["reasons"])
        self.assertIn("custom/parts/rules one.css", found["sheets"])

    def test_directory_containing_named_editions_stays_ambiguous(self):
        named = write(self.root / "named", {"src/userChrome-A.css": "#a{}", "src/userChrome-B.css": "#b{}"})
        found = layout.discover(named, subpath="src")
        self.assertEqual(found["status"], "ambiguous")
        self.assertIsNone(found["selected"])
        status, text = run("install", str(named), "--subpath", "src", "--profile", str(self.profile), "--yes")
        self.assertEqual(status, 2, text)
        self.assertIn("src/userChrome-B.css", text)
        self.assertEqual(layout.discover(named, subpath="src/userChrome-B.css")["status"], "ok")

    def test_audit_patches_apply_to_flat_and_nested_sources_from_git_root(self):
        for folder, entry in (("", "userChrome.css"), ("themes/O'Brien $literal", "chrome/userChrome.css")):
            with self.subTest(folder=folder), tempfile.TemporaryDirectory() as td:
                repo = Path(td)
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
                source = write(repo / folder, {entry: '#old { color: red; }\n'})
                patch = repo / "fix.patch"
                result = {"findings": [{"confidence": "renamed", "token": "#old", "replacement": ".old",
                                       "uses": [{"file": "chrome/userChrome.css", "line": 1}]}]}
                def windows_text_write(path, value, encoding=None, errors=None):
                    return path.write_bytes(value.replace("\n", "\r\n").encode(encoding or "utf-8"))
                with mock.patch.object(cli, "choose_firefox", return_value="fake"), \
                        mock.patch("fxcss.omni.scan", return_value={"paths": []}), \
                        mock.patch("fxcss.core.Session"), mock.patch.object(audit, "audit", return_value=result), \
                        mock.patch.object(audit, "report"), \
                        mock.patch.object(Path, "write_text", autospec=True, side_effect=windows_text_write):
                    status, text = run("audit", "--theme", str(source), "--no-unused", "--patch", str(patch))
                self.assertEqual(status, 0, text)
                self.assertNotIn(b"\r", patch.read_bytes())
                self.assertEqual((source / entry).read_text(), '#old { color: red; }\n')
                checked = subprocess.run(["git", "-C", str(repo), "apply", "--check", str(patch)],
                                         capture_output=True, text=True)
                self.assertEqual(checked.returncode, 0, checked.stderr + patch.read_text())
                subprocess.run(["git", "-C", str(repo), "apply", str(patch)], check=True)
                self.assertEqual((source / entry).read_text(), '.old { color: red; }\n')

    def test_archive_and_assembled_patches_are_refused(self):
        plan = layout.discover(self.theme)
        for kind, packaged in (("named", None), ("fragment", None), ("standard", "chrome.zip")):
            with self.subTest(kind=kind), self.assertRaisesRegex(layout.LayoutError, "cannot produce a source patch"):
                layout.patch_source_paths(dict(plan, kind=kind, packaged=packaged), self.theme,
                                          {"chrome/userChrome.css"})

    @unittest.skipUnless(shutil.which("bash"), "bash unavailable")
    def test_generated_shell_preserves_literal_path_characters(self):
        folder = "O'Brien $literal `touch canary` $(touch canary)"
        original = 'fxcss support --base "$before" --head . --theme head\n'
        rendered = scaffold._theme_commands(original, folder)
        script = "fxcss() { printf '%s\\0' \"$@\"; }\nbefore='/tmp/before tree'\n" + rendered
        executed = subprocess.run([shutil.which("bash"), "-c", script], cwd=self.root, capture_output=True)
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertEqual(executed.stdout.decode().split('\0')[:-1],
                         ["support", "--base", "/tmp/before tree/" + folder, "--head", folder,
                          "--theme", "head/" + folder])
        self.assertFalse((self.root / "canary").exists())

    def test_unreleased_workflows_can_pin_a_full_toolkit_commit(self):
        sha = "a" * 40
        status, text = run("init", "--theme", str(self.theme), "--watch", "--toolkit-ref", sha)
        self.assertEqual(status, 0, text)
        for path in (self.theme / ".github/workflows").glob("*.yml"):
            content = path.read_text()
            if "pip install" in content:
                self.assertIn("git+https://github.com/AdamXweb/fxcss.git@" + sha, content)
                self.assertNotIn("fxcss[images]==", content)
        status, text = run("init", "--theme", str(self.theme), "--force", "--toolkit-ref", "main")
        self.assertEqual(status, 2, text)
        self.assertIn("full 40-character", text)

    def remote_info(self, sha):
        return {"owner": "o", "name": "n", "host": "github.com", "kind": "github",
                "default_branch": "main", "release": None, "tracked": None,
                "commit": {"full_sha": sha, "sha": sha[:7], "date": "", "message": ""},
                "description": "", "stars": 0, "licence": None}

    def test_install_and_upgrade_download_the_recorded_commit_when_branch_moves(self):
        first, second = "a" * 40, "b" * 40
        downloaded = []
        def download(owner, name, ref, dest, **kwargs):
            downloaded.append(ref)
            self.assertIn(ref, (first, second), "a mutable branch name was downloaded")
            return write(dest, {"chrome/userChrome.css": "/* " + ref + " */\n"})
        with mock.patch.object(fetch, "resolve", return_value=self.remote_info(first)), \
                mock.patch.object(fetch, "download", side_effect=download):
            status, text = run("install", "o/n", "--profile", str(self.profile), "--yes")
        self.assertEqual(status, 0, text)
        self.assertEqual(install.read_manifest(self.profile)["source"]["resolved"], first)
        with mock.patch.object(fetch, "resolve", return_value=self.remote_info(second)), \
                mock.patch.object(fetch, "download", side_effect=download):
            status, text = run("upgrade", "--profile", str(self.profile), "--yes")
        self.assertEqual(status, 0, text)
        self.assertEqual(downloaded, [first, second])
        manifest = install.read_manifest(self.profile)
        self.assertEqual((manifest["source"]["ref"], manifest["source"]["resolved"]), ("main", second))
        self.assertIn(second, (self.profile / "chrome/userChrome.css").read_text())

    def test_unresolved_branch_cannot_be_installed(self):
        with mock.patch.object(fetch, "resolve", return_value=self.remote_info("short")), \
                mock.patch.object(fetch, "download") as download:
            status, text = run("install", "o/n", "--profile", str(self.profile), "--yes")
        self.assertEqual(status, 2, text)
        self.assertIn("full commit SHA", text)
        download.assert_not_called()
        self.assertIsNone(install.read_manifest(self.profile))

    def test_current_remote_revision_accepts_explicit_setup_changes_and_checks_them(self):
        sha = "a" * 40
        write(self.theme, {"custom/tabs.css": "#tabs{}\n", "config/user.js": 'user_pref("theme.extra", true);\n'})
        def download(owner, name, ref, dest, **kwargs):
            self.assertEqual(ref, sha)
            shutil.copytree(self.theme, dest)
            return dest
        with mock.patch.object(fetch, "resolve", return_value=self.remote_info(sha)), \
                mock.patch.object(fetch, "download", side_effect=download):
            self.assertEqual(run("install", "o/n", "--profile", str(self.profile), "--yes")[0], 0)
            for selected in (["--with", "tabs", "--prefs", "config/user.js"],
                             ["--with", "", "--prefs", ""]):
                before = (self.profile / "user.js").read_bytes()
                status, text = run("upgrade", "--profile", str(self.profile), "--check", *selected)
                self.assertEqual(status, 1, text)
                self.assertEqual((self.profile / "user.js").read_bytes(), before)
                status, text = run("upgrade", "--profile", str(self.profile), "--yes", *selected)
                self.assertEqual(status, 0, text)
                self.assertEqual(run("upgrade", "--profile", str(self.profile), "--check", *selected)[0], 0)
                manifest = install.read_manifest(self.profile)
                self.assertEqual(manifest["pref_files"], ["config/user.js"] if selected[-1] else [])
                self.assertEqual(manifest["sheet_ids"], ["custom/tabs.css"] if selected[1] else [])
                self.assertEqual(manifest["source"]["resolved"], sha)

    def test_adoption_compares_the_immutable_branch_tree_and_never_records_unknown_sha(self):
        from fxcss import adopt
        raw = b"#base {}\n"
        write(self.profile, {"chrome/userChrome.css": raw})
        sha = "a" * 40
        entry = {"ref": "main", "kind": "branch", "sha": sha}
        listing = {"chrome/userChrome.css": adopt.blob_sha(raw)}
        with mock.patch.object(fetch, "known_refs", return_value=[entry]), \
                mock.patch.object(fetch, "tree", return_value=listing) as tree:
            status, text = run("adopt", "o/n", "--profile", str(self.profile), "--yes")
        self.assertEqual(status, 0, text)
        tree.assert_called_once_with("o", "n", sha)
        self.assertEqual(install.read_manifest(self.profile)["source"]["resolved"], sha)
        (self.profile / "chrome" / install.MANIFEST_NAME).unlink()
        with mock.patch.object(fetch, "known_refs", return_value=[dict(entry, sha=None)]), \
                mock.patch.object(fetch, "branch_head", return_value=None), mock.patch.object(fetch, "tree") as tree:
            status, text = run("adopt", "o/n", "--profile", str(self.profile), "--yes")
        self.assertEqual(status, 2, text)
        tree.assert_not_called()
        self.assertIsNone(install.read_manifest(self.profile))

    def test_setup_change_keeps_an_explicit_revision_pinned(self):
        sha = "a" * 40
        write(self.theme, {"config/user.js": 'user_pref("theme.extra", true);\n'})
        source = {"kind": "github", "host": "github.com", "owner": "o", "name": "n",
                  "ref": "main", "resolved": sha, "ref_kind": "explicit"}
        install.install_theme(self.theme, self.profile, "o/n@" + sha, source=source,
                              extra={"pref_files": [], "sheet_ids": []})
        with mock.patch.object(fetch, "resolve", return_value=self.remote_info("b" * 40)), \
                mock.patch.object(fetch, "download", return_value=self.theme) as download:
            status, text = run("upgrade", "--profile", str(self.profile), "--yes", "--prefs", "config/user.js")
        self.assertEqual(status, 0, text)
        self.assertEqual(install.read_manifest(self.profile)["source"]["ref_kind"], "explicit")
        self.assertEqual(install.read_manifest(self.profile)["source"]["resolved"], sha)
        self.assertEqual(download.call_args.args[2], sha)
