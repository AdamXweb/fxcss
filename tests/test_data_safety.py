"""What belongs to the person stays theirs through every maintenance path.

Each class is one way a real profile used to lose something: the chrome/
from before fxcss was pruned after a second install or a pair of rollbacks;
the customChrome.css hook was treated as the theme's file; a pinned install
was re-fetched on every upgrade; a .DS_Store blocked uninstall from restoring
the original; files beside an adopted theme were dropped by the next
upgrade; a stray marker in user.js cost the lines under it; and prefs a theme
sets could be written without being named first.
"""

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fxcss import adopt, checklist, cli, fetch, install


ORIGINAL = b"/* my own chrome, from before fxcss */\r\n"


def _write(root, files):
    for relative, data in files.items():
        path = Path(root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return Path(root)


def _tree(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def _stamps():
    """Distinct, increasing backup stamps, so order never depends on the clock."""
    number = 0
    while True:
        number += 1
        yield f"202601{number:02d}000000"


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.dict(os.environ, {"CI": "1", "NO_COLOR": "1"}):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class _Profile(unittest.TestCase):
    """A profile with the person's own chrome/, and a theme to put in it."""

    theme_files = {"chrome/userChrome.css": "#nav-bar { color: red }\n"}
    profile_files = {"prefs.js": "", "chrome/userChrome.css": ORIGINAL}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.theme = _write(self.root / "theme", self.theme_files)
        self.profile = _write(self.root / "profile", self.profile_files)
        self.stamps = _stamps()

    def install(self, version="v1", **kwargs):
        return install.install_theme(self.theme, self.profile, f"o/n@{version}",
                                     stamp=next(self.stamps), **kwargs)

    def upgrade(self, version, keep=3, **kwargs):
        """What `fxcss upgrade` does with the library: install, then prune."""
        manifest = install.read_manifest(self.profile)
        result = self.install(version, origin_backup=manifest["origin_backup"], **kwargs)
        install.prune_backups(self.profile, keep,
                              protect=[manifest["origin_backup"]])
        return result

    def original_copies(self):
        return sorted(p.relative_to(self.profile).as_posix()
                      for p in self.profile.rglob("userChrome.css")
                      if p.read_bytes() == ORIGINAL)


class OriginalChromeTests(_Profile):
    """The chrome/ from before fxcss is never pruned, and uninstall finds it."""

    def test_a_second_install_keeps_the_first_origin(self):
        first = self.install()
        second = self.install("v2")
        self.assertEqual(second["origin_backup"], first["backup"])
        self.assertNotEqual(second["backup"], first["backup"])
        self.assertTrue(second["user_js_created"])
        for version in ("v3", "v4", "v5", "v6"):
            self.upgrade(version)
        self.assertEqual(self.original_copies(), [f"{first['backup']}/userChrome.css"])
        summary = install.uninstall_theme(self.profile)
        self.assertEqual(summary["restored"], first["backup"])
        self.assertEqual((self.profile / "chrome" / "userChrome.css").read_bytes(), ORIGINAL)
        # fxcss made user.js on the first install, so it leaves with the theme.
        self.assertFalse((self.profile / "user.js").exists())

    def test_install_twice_then_three_upgrades_from_the_command_line(self):
        _write(self.profile, {"user.js": b'user_pref("mine", 1);\r\n'})
        for _ in range(2):
            code, _, err = run_cli("install", str(self.theme), "--profile",
                                   str(self.profile), "--yes")
            self.assertEqual(code, 0, err)
        for number in range(3):
            _write(self.theme, {"chrome/userChrome.css": f"#nav-bar {{ order: {number} }}\n"})
            code, out, err = run_cli("upgrade", "--profile", str(self.profile), "--yes")
            self.assertEqual(code, 0, err)
        self.assertIn("pruned 1 older backup", " ".join(out.split()))
        self.assertEqual(len(self.original_copies()), 1)
        code, out, err = run_cli("uninstall", "--profile", str(self.profile), "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.profile / "chrome" / "userChrome.css").read_bytes(), ORIGINAL)
        self.assertEqual((self.profile / "user.js").read_bytes(), b'user_pref("mine", 1);\r\n')

    def test_an_origin_recorded_by_an_older_fxcss_is_followed_to_the_original(self):
        """0.25 recorded the previous *theme's* backup as the origin here."""
        first = self.install()
        second = self.install("v2")
        path = self.profile / "chrome" / install.MANIFEST_NAME
        data = json.loads(path.read_text(encoding="utf-8"))
        data["origin_backup"] = second["backup"]
        path.write_text(json.dumps(data), encoding="utf-8")

        self.assertEqual(install.read_manifest(self.profile)["origin_backup"],
                         first["backup"])
        for version in ("v3", "v4", "v5", "v6"):
            self.upgrade(version)
        summary = install.uninstall_theme(self.profile)
        self.assertEqual(summary["restored"], first["backup"])
        self.assertEqual((self.profile / "chrome" / "userChrome.css").read_bytes(), ORIGINAL)

    def test_a_profile_that_began_without_chrome_ends_without_it(self):
        import shutil
        shutil.rmtree(self.profile / "chrome")
        first = self.install()
        self.assertIsNone(first["origin_backup"])
        second = self.install("v2")
        self.assertIsNone(second["origin_backup"])
        # The same profile as 0.25 left it: the first theme named as origin.
        path = self.profile / "chrome" / install.MANIFEST_NAME
        data = json.loads(path.read_text(encoding="utf-8"))
        data["origin_backup"] = second["backup"]
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(install.read_manifest(self.profile)["origin_backup"])
        summary = install.uninstall_theme(self.profile)
        self.assertIsNone(summary["restored"])
        self.assertFalse((self.profile / "chrome").exists())

    def test_a_manifest_chain_that_loops_ends(self):
        first = self.install()
        second = self.install("v2")
        inner = self.profile / second["backup"] / install.MANIFEST_NAME
        data = json.loads(inner.read_text(encoding="utf-8"))
        data["origin_backup"] = second["backup"]
        inner.write_text(json.dumps(data), encoding="utf-8")
        active = self.profile / "chrome" / install.MANIFEST_NAME
        data = json.loads(active.read_text(encoding="utf-8"))
        data["origin_backup"] = second["backup"]
        active.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(install.read_manifest(self.profile)["origin_backup"],
                         second["backup"])
        self.assertTrue((self.profile / first["backup"]).is_dir())

    def test_rolling_back_twice_then_upgrading_keeps_the_original(self):
        first = self.install()
        install.rollback_to(self.profile, first["origin_backup"], stamp=next(self.stamps))
        self.assertEqual((self.profile / "chrome" / "userChrome.css").read_bytes(), ORIGINAL)
        theme_backup = install.list_backups(self.profile)[0]["name"]
        summary = install.rollback_to(self.profile, theme_backup, stamp=next(self.stamps))
        # The original now lives under the name the second rollback gave it,
        # and the restored manifest says so instead of naming a folder that
        # no longer exists.
        manifest = install.read_manifest(self.profile)
        self.assertEqual(manifest["origin_backup"], summary["moved_aside"])
        for version in ("v2", "v3", "v4", "v5"):
            self.upgrade(version)
        self.assertEqual(self.original_copies(),
                         [f"{summary['moved_aside']}/userChrome.css"])
        done = install.uninstall_theme(self.profile)
        self.assertEqual(done["restored"], summary["moved_aside"])
        self.assertEqual((self.profile / "chrome" / "userChrome.css").read_bytes(), ORIGINAL)

    def test_rollback_leaves_a_recorded_origin_that_still_exists_alone(self):
        first = self.install()
        second = self.upgrade("v2")
        # The person replaces chrome/ by hand, then rolls back to the theme.
        import shutil
        shutil.rmtree(self.profile / "chrome")
        _write(self.profile, {"chrome/userChrome.css": "/* by hand */\n"})
        install.rollback_to(self.profile, second["backup"], stamp=next(self.stamps))
        self.assertEqual(install.read_manifest(self.profile)["origin_backup"],
                         first["origin_backup"])

    def test_rollback_to_an_older_theme_borrows_the_origin_the_newer_one_knows(self):
        first = self.install()
        install.rollback_to(self.profile, first["origin_backup"], stamp=next(self.stamps))
        theme_backup = install.list_backups(self.profile)[0]["name"]
        # A fresh install over the restored original records it under a new name…
        again = self.install("v2")
        self.assertNotEqual(again["origin_backup"], first["origin_backup"])
        # …which the older theme's manifest, written before any of this, cannot know.
        install.rollback_to(self.profile, theme_backup, stamp=next(self.stamps))
        manifest = install.read_manifest(self.profile)
        self.assertEqual(manifest["theme"], "o/n@v1")
        self.assertEqual(manifest["origin_backup"], again["origin_backup"])
        summary = install.uninstall_theme(self.profile)
        self.assertEqual(summary["restored"], again["origin_backup"])
        self.assertEqual((self.profile / "chrome" / "userChrome.css").read_bytes(), ORIGINAL)

    def test_a_rollback_that_fails_leaves_the_backups_manifest_as_it_was(self):
        first = self.install()
        install.rollback_to(self.profile, first["origin_backup"], stamp=next(self.stamps))
        theme_backup = install.list_backups(self.profile)[0]["name"]
        before = _tree(self.profile)
        rename = Path.rename

        def fail(path, target):
            if path == self.profile / theme_backup:
                raise PermissionError("simulated: the backup cannot be activated")
            return rename(path, target)

        with mock.patch.object(Path, "rename", fail), self.assertRaises(RuntimeError):
            install.rollback_to(self.profile, theme_backup, stamp=next(self.stamps))
        self.assertEqual(_tree(self.profile), before)

    def test_a_backup_without_a_manifest_is_never_pruned(self):
        self.install()
        _write(self.profile, {"chrome.backup-20200101000000/userChrome.css": "/* hand-made */\n",
                              "chrome.backup-20200102000000/notes.css": "/* also mine */\n"})
        for version in ("v2", "v3", "v4", "v5", "v6"):
            self.upgrade(version, keep=1)
        self.assertTrue((self.profile / "chrome.backup-20200101000000").is_dir())
        self.assertTrue((self.profile / "chrome.backup-20200102000000").is_dir())
        themes = [b for b in install.list_backups(self.profile) if b["manifest"]]
        self.assertEqual(len(themes), 1)
        self.assertEqual(install.prune_backups(self.profile, 0), [themes[0]["name"]])
        self.assertEqual(len(install.list_backups(self.profile)), 3)


class CustomChromeHookTests(_Profile):
    """customChrome.css is the person's hook once they put anything in it."""

    theme_files = {"chrome/userChrome.css": '@import "customChrome.css";\n#nav-bar { }\n'}
    MINE = b"/* MY CUSTOMISATIONS */\r\n#nav-bar { color: blue }\r\n"

    def setUp(self):
        super().setUp()
        self.first = self.install()
        self.hook = self.profile / "chrome" / "customChrome.css"

    def test_an_untouched_placeholder_is_neither_edit_nor_addition(self):
        self.assertEqual(self.hook.read_text(encoding="utf-8"), install.PLACEHOLDER_TEXT)
        got = install.drift(self.profile)
        self.assertEqual((got["modified"], got["missing"], got["extra"]), ([], [], []))
        self.assertEqual(install.user_additions(self.profile)["files"], [])
        summary = install.uninstall_theme(self.profile)
        self.assertEqual(summary["restored"], self.first["origin_backup"])
        self.assertEqual(summary["kept"], [])

    def test_a_filled_in_hook_is_the_persons_file_not_an_edit(self):
        self.hook.write_bytes(self.MINE)
        got = install.drift(self.profile)
        self.assertEqual(got["modified"], [])
        self.assertEqual(got["extra"], ["chrome/customChrome.css"])
        self.assertEqual(install.local_changes(self.profile), [])
        self.assertEqual(install.user_additions(self.profile)["files"],
                         ["chrome/customChrome.css"])

    def test_a_deleted_placeholder_does_not_count_as_a_deleted_theme_file(self):
        self.hook.unlink()
        self.assertEqual(install.drift(self.profile)["missing"], [])

    def test_upgrade_keeps_the_hook_with_and_without_force(self):
        self.hook.write_bytes(self.MINE)
        for version, on_collision in (("v2", "stop"), ("v3", "theme"), ("v4", "stop")):
            with self.subTest(version=version, on_collision=on_collision):
                result = self.upgrade(version, on_collision=on_collision)
                self.assertEqual(self.hook.read_bytes(), self.MINE)
                self.assertEqual(result["user_files"], ["chrome/customChrome.css"])
                self.assertEqual(result["displaced"], [])

    def test_uninstall_leaves_a_filled_in_hook_in_place(self):
        self.hook.write_bytes(self.MINE)
        self.upgrade("v2")
        summary = install.uninstall_theme(self.profile)
        self.assertEqual(summary["kept"], ["chrome/customChrome.css"])
        self.assertEqual(self.hook.read_bytes(), self.MINE)

    def test_a_placeholder_written_with_crlf_is_still_the_placeholder(self):
        """Text mode wrote \\r\\n on Windows; the manifest recorded that digest."""
        crlf = install.PLACEHOLDER_TEXT.replace("\n", "\r\n").encode("utf-8")
        self.hook.write_bytes(crlf)
        path = self.profile / "chrome" / install.MANIFEST_NAME
        data = json.loads(path.read_text(encoding="utf-8"))
        data["digests"]["chrome/customChrome.css"] = hashlib.sha256(crlf).hexdigest()
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(install.drift(self.profile)["extra"], [])
        self.hook.write_bytes(self.MINE)
        self.assertEqual(install.drift(self.profile)["extra"], ["chrome/customChrome.css"])
        self.assertEqual(install.drift(self.profile)["modified"], [])

    def test_a_version_that_starts_shipping_the_file_is_a_real_collision(self):
        self.hook.write_bytes(self.MINE)
        _write(self.theme, {"chrome/customChrome.css": "/* the theme's own now */\n"})
        with self.assertRaises(install.CollisionError) as caught:
            self.upgrade("v2")
        self.assertEqual(caught.exception.collisions, ["chrome/customChrome.css"])
        self.assertEqual(self.hook.read_bytes(), self.MINE)
        result = self.upgrade("v2", on_collision="theme")
        self.assertEqual(result["displaced"], ["chrome/customChrome.css"])
        self.assertEqual((self.profile / result["backup"] / "customChrome.css").read_bytes(),
                         self.MINE)

    def test_fxcss_upgrade_does_not_refuse_over_a_filled_in_hook(self):
        code, _, err = run_cli("install", str(self.theme), "--profile",
                               str(self.profile), "--yes")
        self.assertEqual(code, 0, err)
        self.hook.write_bytes(self.MINE)
        _write(self.theme, {"chrome/userChrome.css":
                            '@import "customChrome.css";\n#nav-bar { order: 2 }\n'})
        code, out, err = run_cli("upgrade", "--profile", str(self.profile), "--yes")
        self.assertEqual(code, 0, err)
        self.assertNotIn("edited since install", out)
        self.assertIn("1 file you added will stay active", out)
        self.assertEqual(self.hook.read_bytes(), self.MINE)
        _write(self.theme, {"chrome/userChrome.css":
                            '@import "customChrome.css";\n#nav-bar { order: 3 }\n'})
        code, out, err = run_cli("upgrade", "--profile", str(self.profile), "--yes", "--force")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.hook.read_bytes(), self.MINE)


class PinnedUpgradeTests(unittest.TestCase):
    """A --ref install does not move, or spend a backup, unless told to."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.profile = _write(self.root / "profile", {"prefs.js": ""})
        self.downloaded = []
        info = {"owner": "o", "name": "n", "host": "github.com", "kind": "github",
                "default_branch": "main", "release": None, "tracked": None,
                "commit": {"full_sha": "b" * 40, "sha": "b" * 7,
                           "date": "2026-01-01", "message": "m"},
                "description": "", "stars": 0, "licence": None}

        def download(owner, name, revision, dest, **kwargs):
            self.downloaded.append(revision)
            return _write(dest, {"chrome/userChrome.css": f"/* {revision} */\n"})

        for target, replacement in (("resolve", lambda *a, **k: dict(info)),
                                    ("download", download)):
            patcher = mock.patch.object(fetch, target, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def pin(self, ref):
        code, _, err = run_cli("install", "o/n", "--ref", ref, "--profile",
                               str(self.profile), "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(install.read_manifest(self.profile)["source"]["ref_kind"], "explicit")

    def test_a_bare_upgrade_of_a_pinned_install_changes_nothing(self):
        for ref in ("v1.0", "main"):
            with self.subTest(ref=ref):
                self.downloaded.clear()
                self.pin(ref)
                before = _tree(self.profile)
                for _ in range(2):
                    code, out, err = run_cli("upgrade", "--profile", str(self.profile), "--yes")
                    self.assertEqual(code, 0, err)
                    self.assertIn(f"Pinned to {ref} with --ref", out)
                    self.assertNotIn("Upgraded", out)
                self.assertEqual(self.downloaded, [ref])
                self.assertEqual(_tree(self.profile), before)
                code, _, _ = run_cli("uninstall", "--profile", str(self.profile), "--yes")
                self.assertEqual(code, 0)

    def test_an_explicit_ref_still_moves_a_pinned_install(self):
        self.pin("v1.0")
        code, out, err = run_cli("upgrade", "--profile", str(self.profile), "--yes",
                                 "--ref", "v1.1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.downloaded, ["v1.0", "v1.1"])
        self.assertEqual((self.profile / "chrome" / "userChrome.css").read_text(),
                         "/* v1.1 */\n")
        manifest = install.read_manifest(self.profile)
        self.assertEqual((manifest["source"]["ref"], manifest["source"]["ref_kind"]),
                         ("v1.1", "explicit"))

    def test_commit_takes_a_pinned_install_off_its_pin(self):
        self.pin("v1.0")
        code, _, err = run_cli("upgrade", "--profile", str(self.profile), "--yes", "--commit")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.downloaded, ["v1.0", "b" * 40])
        self.assertEqual(install.read_manifest(self.profile)["source"]["ref_kind"], "branch")


class LitterTests(_Profile):
    """Files the operating system drops are not the person's work."""

    def test_litter_alone_does_not_stop_uninstall_restoring_the_original(self):
        for name in (".DS_Store", "Thumbs.db", "desktop.ini"):
            with self.subTest(name=name):
                first = self.install()
                _write(self.profile, {f"chrome/{name}": b"\x00\x01",
                                      f"chrome/icons/{name}": b"\x00\x02"})
                self.assertEqual(install.drift(self.profile)["extra"], [])
                summary = install.uninstall_theme(self.profile)
                self.assertEqual(summary["restored"], first["origin_backup"])
                self.assertEqual(summary["kept"], [])
                self.assertEqual(_tree(self.profile / "chrome"),
                                 {"userChrome.css": ORIGINAL})

    def test_litter_is_not_carried_into_an_upgrade_as_the_persons_file(self):
        self.install()
        _write(self.profile, {"chrome/.DS_Store": b"\x00"})
        result = self.upgrade("v2")
        self.assertEqual(result["user_files"], [])
        self.assertFalse((self.profile / "chrome" / ".DS_Store").exists())

    def test_litter_beside_real_work_is_left_where_it_is(self):
        self.install()
        _write(self.profile, {"chrome/.DS_Store": b"\x00", "chrome/mine.css": "#mine { }\n"})
        summary = install.uninstall_theme(self.profile)
        self.assertIsNone(summary["restored"])
        self.assertEqual(summary["kept"], ["chrome/.DS_Store", "chrome/mine.css"])

    def test_a_copy_of_an_original_file_does_not_block_restoring_it(self):
        _write(self.profile, {"chrome/mine.css": "#mine { }\n"})
        first = self.install()
        _write(self.profile, {"chrome/mine.css": "#mine { }\n"})
        summary = install.uninstall_theme(self.profile)
        self.assertEqual(summary["restored"], first["origin_backup"])
        self.assertEqual(_tree(self.profile / "chrome"),
                         {"userChrome.css": ORIGINAL, "mine.css": b"#mine { }\n"})

    def test_an_edited_copy_of_an_original_file_still_blocks(self):
        _write(self.profile, {"chrome/mine.css": "#mine { }\n"})
        self.install()
        _write(self.profile, {"chrome/mine.css": "#mine { color: red }\n"})
        summary = install.uninstall_theme(self.profile)
        self.assertIsNone(summary["restored"])
        self.assertEqual(summary["kept"], ["chrome/mine.css"])


class AdoptedExtrasTests(unittest.TestCase):
    """Files beside an adopted theme that upstream never shipped are kept."""

    UPSTREAM = b"#a { }\n"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.profile = _write(self.root / "profile", {
            "prefs.js": "", "chrome/userChrome.css": self.UPSTREAM,
            "chrome/my-tweaks.css": "/* MINE */\n", "chrome/.DS_Store": b"\x00"})
        self.before = _tree(self.profile / "chrome")
        best = adopt.compare(self.profile / "chrome",
                             {"chrome/userChrome.css": adopt.blob_sha(self.UPSTREAM)})
        self.assertEqual(best["extra"], [".DS_Store", "my-tweaks.css"])
        self.source = {"kind": "github", "host": "github.com", "owner": "o", "name": "n",
                       "ref": "v1", "ref_kind": "release", "resolved": "v1", "path": None}
        match = dict({key: best[key] for key in ("matched", "files", "score", "differing",
                                                 "missing", "extra", "eol")}, ref="v1")
        self.manifest = install.adopt_theme(self.profile, "o/n@v1", self.source, match,
                                            stamp="20260101000000")
        self.newer = _write(self.root / "v2", {"chrome/userChrome.css": "#a { }\n#new { }\n"})

    def upgrade(self, **kwargs):
        return install.install_theme(self.newer, self.profile, "o/n@v2",
                                     source=dict(self.source, ref="v2"),
                                     origin_backup=self.manifest["origin_backup"],
                                     stamp="20260102000000", **kwargs)

    def test_only_the_matched_versions_files_are_recorded_as_the_themes(self):
        self.assertEqual(self.manifest["files"], ["chrome/userChrome.css"])
        self.assertEqual(self.manifest["user_files"], ["chrome/my-tweaks.css"])
        self.assertEqual(install.user_additions(self.profile)["files"],
                         ["chrome/my-tweaks.css"])
        self.assertEqual(install.local_changes(self.profile), [])

    def test_the_next_upgrade_keeps_them_active(self):
        for on_collision in ("stop", "theme"):
            with self.subTest(on_collision=on_collision):
                result = self.upgrade(on_collision=on_collision)
                self.assertEqual(result["user_files"], ["chrome/my-tweaks.css"])
                self.assertEqual((self.profile / "chrome" / "my-tweaks.css").read_bytes(),
                                 b"/* MINE */\n")

    def test_uninstall_straight_after_adopting_puts_everything_back(self):
        summary = install.uninstall_theme(self.profile)
        self.assertEqual(summary["restored"], self.manifest["origin_backup"])
        self.assertEqual(_tree(self.profile / "chrome"), self.before)

    def test_uninstall_after_an_upgrade_restores_the_adopted_chrome(self):
        self.upgrade()
        summary = install.uninstall_theme(self.profile)
        self.assertEqual(summary["restored"], self.manifest["origin_backup"])
        self.assertEqual(_tree(self.profile / "chrome"), self.before)

    def test_an_extra_edited_since_is_kept_rather_than_replaced(self):
        self.upgrade()
        _write(self.profile, {"chrome/my-tweaks.css": "/* MINE, and newer */\n"})
        summary = install.uninstall_theme(self.profile)
        self.assertIsNone(summary["restored"])
        self.assertEqual(summary["kept"], ["chrome/my-tweaks.css"])
        self.assertTrue((self.profile / self.manifest["origin_backup"]).is_dir())


class StrayMarkerTests(_Profile):
    """A BEGIN marker with no END of its own never claims a later block's."""

    ORPHAN = (b'user_pref("a", 1);\n/* >>> fxcss install >>> */\n'
              b'user_pref("mine.one", 1);\nuser_pref("mine.two", 2);\n')

    def test_strip_leaves_the_lines_under_an_orphan_marker(self):
        text = self.ORPHAN.decode()
        self.assertEqual(install.strip_user_js_block(text), text)
        with_block = install.user_js_with_block(text, install.STYLESHEET_PREF)
        self.assertEqual(install.strip_user_js_block(with_block), text)
        self.assertEqual(install.user_js_with_block(with_block, install.STYLESHEET_PREF),
                         with_block)

    def test_two_well_formed_blocks_are_both_removed(self):
        block = f"{install.BLOCK_BEGIN}\nuser_pref(\"x\", 1);\n{install.BLOCK_END}\n"
        self.assertEqual(install.strip_user_js_block(f"a\n\n{block}b\n\n{block}"), "a\nb\n")

    def test_install_reinstall_and_uninstall_return_user_js_byte_for_byte(self):
        _write(self.profile, {"user.js": self.ORPHAN})
        self.install()
        self.install("v2")
        text = (self.profile / "user.js").read_bytes()
        self.assertIn(b'user_pref("mine.one", 1);\nuser_pref("mine.two", 2);\n', text)
        self.assertEqual(text.count(install.BLOCK_END.encode()), 1)
        install.uninstall_theme(self.profile)
        self.assertEqual((self.profile / "user.js").read_bytes(), self.ORPHAN)


class PrefNamesTests(unittest.TestCase):
    """Every pref a theme is about to set is named before it is set."""

    SPACED = ('user_pref("a.one", 1);\n'
              'user_pref ("network.proxy.autoconfig_url", "https://example.invalid/p.pac");\n'
              'user_pref\n("network.proxy.type", 2);\n'
              'user_pref/* c */("browser.startup.homepage", "about:blank");\n'
              'pref\t( \'sticky.one\' , true);\n')
    NAMES = ["a.one", "network.proxy.autoconfig_url", "network.proxy.type",
             "browser.startup.homepage", "sticky.one"]

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_whitespace_before_the_parenthesis_does_not_hide_a_pref(self):
        theme = _write(self.root, {"chrome/userChrome.css": "#a { }\n",
                                   "configuration/user.js": self.SPACED})
        self.assertEqual(install.theme_pref_names(theme), self.NAMES)
        self.assertEqual(checklist.pref_names_in(theme / "configuration" / "user.js"),
                         self.NAMES)

    def test_a_longer_function_name_is_not_mistaken_for_one(self):
        theme = _write(self.root, {"chrome/userChrome.css": "#a { }\n",
                                   "configuration/user.js": 'my_user_pref_helper ("x", 1);\n'
                                                            'defaultPref("y", 1);\n'})
        self.assertEqual(install.theme_pref_names(theme), [])

    def test_control_characters_in_a_name_are_escaped(self):
        theme = _write(self.root, {"chrome/userChrome.css": "#a { }\n",
                                   "configuration/user.js":
                                       'user_pref("evil\x1b[2Jname\x07", 1);\n'})
        for names in (install.theme_pref_names(theme),
                      checklist.pref_names_in(theme / "configuration" / "user.js")):
            self.assertEqual(names, ["evil\\x1b[2Jname\\x07"])
        rendered = "\n".join(checklist.render(checklist.build(theme, theme)))
        self.assertNotIn("\x1b[2J", rendered)
        self.assertNotIn("\x07", rendered)

    def test_prefs_fxcss_will_write_are_all_named_and_others_are_summarised(self):
        many = "".join(f'user_pref("theme.pref.{n}", true);\n' for n in range(8))
        theme = _write(self.root, {"chrome/userChrome.css": "#a { }\n",
                                   "configuration/user.js": many,
                                   "prefs/optional.js": many.replace("theme.", "other.")})
        items = checklist.build(theme, theme)
        by_file = {item["pref_file"]: item for item in items if item.get("pref_file")}
        self.assertEqual(by_file["configuration/user.js"]["state"], "applied")
        self.assertNotEqual(by_file["prefs/optional.js"]["state"], "applied")
        with mock.patch.dict(os.environ, {"NO_COLOR": "1", "COLUMNS": "100"}):
            rendered = " ".join(" ".join(checklist.render(items)).split())
        for number in range(8):
            self.assertIn(f"theme.pref.{number}", rendered)
            self.assertIn(f"theme.pref.{number}", by_file["configuration/user.js"]["item"])
        self.assertIn("other.pref.4", rendered)
        self.assertNotIn("other.pref.5", rendered)
        self.assertIn("and 3 more", rendered)


if __name__ == "__main__":
    unittest.main()
