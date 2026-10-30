"""Upgrades keep the person's own files active; recovery is safe to repeat.

These are the state-machine checks from the 132-theme maintenance evaluation:
an upgrade used to leave a user-added stylesheet only in the backup (so it
stopped applying), and install -> rollback -> uninstall used to reinstall the
theme the rollback had just taken out.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fxcss import install
from tests.test_units import _make_profile, _make_theme


def _tree(root):
    """{relative path: bytes} for every file under root, for exact comparison."""
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


class PreserveAdditionsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.theme = _make_theme(self.root)
        self.profile = _make_profile(self.root)
        self.first = install.install_theme(self.theme, self.profile, "o/n@v1",
                                           stamp="20260101000000")
        self.added = self.profile / "chrome" / "custom" / "mine.css"
        self.added.parent.mkdir(parents=True, exist_ok=True)
        self.added.write_bytes(b"#mine { color: red }\r\n")

    def upgrade(self, **kwargs):
        return install.install_theme(
            self.theme, self.profile, "o/n@v2", stamp="20260102000000",
            origin_backup=self.first["origin_backup"], **kwargs)

    def test_ordinary_upgrade_keeps_the_added_file_active(self):
        result = self.upgrade()
        self.assertEqual(self.added.read_bytes(), b"#mine { color: red }\r\n")
        self.assertEqual(result["user_files"], ["chrome/custom/mine.css"])
        self.assertNotIn("chrome/custom/mine.css", result["files"])
        self.assertNotIn("chrome/custom/mine.css", result["digests"])
        # The backup has it too: recoverable as well as active.
        self.assertTrue((self.profile / result["backup"] / "custom" / "mine.css").is_file())
        # Drift still reports it as the person's own, never as the theme's.
        self.assertEqual(install.drift(self.profile)["extra"], ["chrome/custom/mine.css"])

    def test_forced_upgrade_over_an_edit_still_keeps_additions(self):
        (self.profile / "chrome" / "userChrome.css").write_text("/* edited */\n")
        result = self.upgrade(on_collision="theme")
        self.assertTrue(self.added.is_file())
        self.assertEqual(result["user_files"], ["chrome/custom/mine.css"])
        self.assertEqual(result["displaced"], [])

    def test_a_second_upgrade_carries_the_file_again(self):
        self.upgrade()
        result = install.install_theme(
            self.theme, self.profile, "o/n@v3", stamp="20260103000000",
            origin_backup=self.first["origin_backup"])
        self.assertTrue(self.added.is_file())
        self.assertEqual(result["user_files"], ["chrome/custom/mine.css"])

    def test_uninstall_after_upgrade_keeps_the_added_file(self):
        self.upgrade()
        summary = install.uninstall_theme(self.profile, stamp="20260104000000")
        self.assertTrue(self.added.is_file())
        self.assertEqual(summary["kept"], ["chrome/custom/mine.css"])
        self.assertIsNone(summary["restored"])

    def test_collision_stops_before_anything_moves(self):
        # The next version ships a file exactly where the person put theirs.
        shipped = self.theme / "chrome" / "custom" / "mine.css"
        shipped.parent.mkdir(parents=True)
        shipped.write_text("#mine { color: blue }\n")
        before = _tree(self.profile)
        with self.assertRaises(install.CollisionError) as caught:
            self.upgrade()
        self.assertEqual(caught.exception.collisions, ["chrome/custom/mine.css"])
        self.assertEqual(_tree(self.profile), before)
        self.assertEqual(sorted(p.name for p in self.profile.glob("chrome.backup-*")),
                         ["chrome.backup-20260101000000"])
        self.assertFalse(list(self.profile.glob(".fxcss-install-*")))

    def test_collision_resolved_for_the_theme_records_the_displacement(self):
        shipped = self.theme / "chrome" / "custom" / "mine.css"
        shipped.parent.mkdir(parents=True)
        shipped.write_text("#mine { color: blue }\n")
        result = self.upgrade(on_collision="theme")
        self.assertEqual(self.added.read_text(), "#mine { color: blue }\n")
        self.assertEqual(result["displaced"], ["chrome/custom/mine.css"])
        self.assertEqual(result["user_files"], [])
        self.assertIn("chrome/custom/mine.css", result["files"])
        self.assertEqual((self.profile / result["backup"] / "custom" / "mine.css").read_bytes(),
                         b"#mine { color: red }\r\n")

    def test_identical_upstream_adoption_is_not_a_collision(self):
        shipped = self.theme / "chrome" / "custom" / "mine.css"
        shipped.parent.mkdir(parents=True)
        shipped.write_bytes(b"#mine { color: red }\r\n")
        result = self.upgrade()
        self.assertEqual(result["user_files"], [])
        self.assertEqual(result["displaced"], [])
        self.assertIn("chrome/custom/mine.css", result["files"])

    def test_directory_in_the_way_is_a_collision(self):
        (self.theme / "chrome" / "custom" / "mine.css").mkdir(parents=True)
        (self.theme / "chrome" / "custom" / "mine.css" / "part.css").write_text("")
        with self.assertRaises(install.CollisionError):
            self.upgrade()

    def test_git_metadata_and_links_stay_in_the_backup_only(self):
        git = self.profile / "chrome" / ".git"
        git.mkdir()
        (git / "config").write_text("[core]\n")
        link = self.profile / "chrome" / "linked.css"
        try:
            os.symlink(self.added, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        additions = install.user_additions(self.profile)
        self.assertEqual(additions["files"], ["chrome/custom/mine.css"])
        self.assertEqual(sorted(additions["skipped"]),
                         ["chrome/.git/config", "chrome/linked.css"])
        result = self.upgrade()
        self.assertFalse((self.profile / "chrome" / ".git").exists())
        self.assertFalse((self.profile / "chrome" / "linked.css").exists())
        self.assertEqual(result["user_files"], ["chrome/custom/mine.css"])

    def test_a_fresh_install_over_an_unmanaged_chrome_does_not_carry(self):
        """Without a manifest nothing is known to be 'added': the whole
        chrome/ is the person's and goes to the origin backup as before."""
        profile = _make_profile(self.root / "other")
        (profile / "chrome" / "mine.css").write_text("x")
        result = install.install_theme(self.theme, profile, "o/n@v1")
        self.assertEqual(result["user_files"], [])
        self.assertFalse((profile / "chrome" / "mine.css").exists())
        self.assertTrue((profile / result["backup"] / "mine.css").is_file())

    def test_manifest_lists_what_is_active_and_recoverable(self):
        result = self.upgrade()
        on_disk = json.loads((self.profile / "chrome" / install.MANIFEST_NAME).read_text())
        self.assertEqual(on_disk["user_files"], result["user_files"])
        self.assertEqual(on_disk["displaced"], [])
        self.assertEqual(on_disk["backup"], "chrome.backup-20260102000000")
        self.assertEqual(on_disk["origin_backup"], "chrome.backup-20260101000000")


class RecoveryStateMachineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.theme = _make_theme(self.root)
        self.profile = _make_profile(self.root)
        (self.profile / "chrome" / "notes.css").write_bytes(b"/* mine \xff */\r\n")
        (self.profile / "user.js").write_bytes(b'user_pref("mine", 1);\r\n\xfe')
        self.original = _tree(self.profile)

    def test_install_rollback_uninstall_leaves_the_original_in_place(self):
        first = install.install_theme(self.theme, self.profile, "o/n@v1",
                                      stamp="20260101000000")
        install.rollback_to(self.profile, first["origin_backup"], stamp="20260102000000")
        summary = install.uninstall_theme(self.profile, stamp="20260103000000")
        self.assertTrue(summary["noop"])
        self.assertIsNone(summary["restored"])
        self.assertFalse(list(self.profile.glob("chrome.removed-*")))
        # Only the backup made by the rollback (holding the theme) is extra.
        current = _tree(self.profile)
        self.assertEqual({k: v for k, v in current.items()
                          if not k.startswith("chrome.backup-")}, self.original)
        self.assertEqual(sorted(p.name for p in self.profile.glob("chrome.backup-*")),
                         ["chrome.backup-20260102000000"])
        self.assertFalse(install.read_manifest(self.profile))

    def test_uninstall_after_rollback_is_idempotent(self):
        first = install.install_theme(self.theme, self.profile, "o/n@v1",
                                      stamp="20260101000000")
        install.rollback_to(self.profile, first["origin_backup"], stamp="20260102000000")
        for stamp in ("20260103000000", "20260104000000"):
            summary = install.uninstall_theme(self.profile, stamp=stamp)
            self.assertTrue(summary["noop"])
        self.assertEqual(
            {k: v for k, v in _tree(self.profile).items()
             if not k.startswith("chrome.backup-")}, self.original)

    def test_repeated_rollback_ping_pongs_without_losing_either_side(self):
        first = install.install_theme(self.theme, self.profile, "o/n@v1",
                                      stamp="20260101000000")
        install.rollback_to(self.profile, first["origin_backup"], stamp="20260102000000")
        self.assertFalse(install.read_manifest(self.profile))
        # Back to the theme: the backup the rollback made holds the manifest.
        install.rollback_to(self.profile, "chrome.backup-20260102000000",
                            stamp="20260103000000")
        self.assertEqual(install.read_manifest(self.profile)["theme"], "o/n@v1")
        self.assertEqual((self.profile / "chrome.backup-20260103000000" / "notes.css").read_bytes(),
                         b"/* mine \xff */\r\n")
        self.assertIn(install.BLOCK_BEGIN, (self.profile / "user.js").read_text(errors="replace"))

    def test_upgrade_after_rollback_to_a_managed_version_keeps_the_origin(self):
        first = install.install_theme(self.theme, self.profile, "o/n@v1",
                                      stamp="20260101000000")
        second = install.install_theme(self.theme, self.profile, "o/n@v2",
                                       stamp="20260102000000",
                                       origin_backup=first["origin_backup"])
        install.rollback_to(self.profile, second["backup"], stamp="20260103000000")
        manifest = install.read_manifest(self.profile)
        self.assertEqual(manifest["theme"], "o/n@v1")
        third = install.install_theme(self.theme, self.profile, "o/n@v3",
                                      stamp="20260104000000",
                                      origin_backup=manifest["origin_backup"])
        self.assertEqual(third["origin_backup"], first["origin_backup"])
        summary = install.uninstall_theme(self.profile, stamp="20260105000000")
        self.assertEqual(summary["restored"], first["origin_backup"])
        self.assertEqual((self.profile / "chrome" / "notes.css").read_bytes(),
                         b"/* mine \xff */\r\n")
        self.assertEqual((self.profile / "user.js").read_bytes(),
                         b'user_pref("mine", 1);\r\n\xfe')

    def test_upgrade_after_rollback_to_the_original_is_refused_upstream(self):
        """With no manifest active there is nothing to upgrade; the CLI says
        so. The library sees no previous install and treats a new install as
        a first one -- the original goes to a fresh origin backup."""
        first = install.install_theme(self.theme, self.profile, "o/n@v1",
                                      stamp="20260101000000")
        install.rollback_to(self.profile, first["origin_backup"], stamp="20260102000000")
        again = install.install_theme(self.theme, self.profile, "o/n@v2",
                                      stamp="20260103000000")
        self.assertEqual(again["origin_backup"], "chrome.backup-20260103000000")
        self.assertEqual((self.profile / again["origin_backup"] / "notes.css").read_bytes(),
                         b"/* mine \xff */\r\n")

    def test_noop_uninstall_still_removes_a_stray_block(self):
        first = install.install_theme(self.theme, self.profile, "o/n@v1",
                                      stamp="20260101000000")
        shutil.rmtree(self.profile / "chrome")
        shutil.copytree(self.profile / first["origin_backup"], self.profile / "chrome")
        # user.js still carries the block the install wrote.
        summary = install.uninstall_theme(self.profile, stamp="20260102000000")
        self.assertTrue(summary["noop"])
        self.assertTrue(summary.get("user_js_cleaned"))
        self.assertEqual((self.profile / "user.js").read_bytes(),
                         b'user_pref("mine", 1);\r\n\xfe')


if __name__ == "__main__":
    unittest.main()
