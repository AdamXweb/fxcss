"""Profile lifecycle and failure recovery, using only disposable profiles."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fxcss import install
from tests.test_units import _make_profile, _make_theme


class ProfileSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fxcss profile café ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.theme = _make_theme(self.root)
        self.profile = _make_profile(self.root)
        self.original = self.snapshot()
        self.first = install.install_theme(self.theme, self.profile, "o/n@v1", stamp="20260101000000")
        (self.theme / "chrome/userChrome.css").write_text(":root { color: red; }", encoding="utf-8")
        self.second = install.install_theme(self.theme, self.profile, "o/n@v2", stamp="20260102000000",
                                            origin_backup=self.first["backup"])

    def snapshot(self):
        return {p.relative_to(self.profile).as_posix(): p.read_bytes()
                for p in self.profile.rglob("*") if p.is_file()}

    def change(self, operation):
        if operation == "rollback":
            return install.rollback_to(self.profile, self.second["backup"], stamp="20260103000000")
        return install.uninstall_theme(self.profile)

    def test_lifecycle_preserves_original_files_and_preferences(self):
        install.rollback_to(self.profile, self.second["backup"])
        self.assertEqual(install.read_manifest(self.profile)["theme"], "o/n@v1")
        install.uninstall_theme(self.profile)
        for name, contents in self.original.items():
            self.assertEqual((self.profile / name).read_bytes(), contents)

    def test_layouts_upgrade_rollback_and_remove_without_losing_edits(self):
        from tests.theme_fixtures import write_themes
        for theme in write_themes(self.root / "layouts"):
            with self.subTest(theme=theme.name):
                profile = self.root / (theme.name + " profile")
                profile.mkdir()
                (profile / "user.js").write_text('user_pref("mine", true);\n')
                original = (profile / "user.js").read_bytes()
                sheets = sorted((theme / "optional").glob("*.css"))
                first = install.install_theme(theme, profile, "local-v1", sheets=sheets)
                second = install.install_theme(theme, profile, "local-v2", sheets=sheets,
                                                origin_backup=first["origin_backup"])
                install.rollback_to(profile, second["backup"])
                (profile / "chrome/notes.txt").write_text("keep this")
                summary = install.uninstall_theme(profile)
                self.assertIn("chrome/notes.txt", summary["kept"])
                self.assertEqual((profile / "chrome/notes.txt").read_text(), "keep this")
                self.assertEqual((profile / "user.js").read_bytes(), original)

    def test_failed_swaps_and_interrupts_restore_every_file(self):
        rename, replace = Path.rename, Path.replace
        for operation in ("rollback", "uninstall"):
            for phase in ("outgoing", "incoming", "preferences"):
                for error in (PermissionError, KeyboardInterrupt):
                    with self.subTest(operation=operation, phase=phase, error=error):
                        before = self.snapshot()
                        fired = False
                        def fail_rename(path, target):
                            nonlocal fired
                            matches = ((phase == "outgoing" and path == self.profile / "chrome") or
                                       (phase == "incoming" and Path(target) == self.profile / "chrome"))
                            if matches and not fired:
                                fired = True
                                raise error("simulated locked file or interruption")
                            return rename(path, target)
                        def fail_replace(path, target):
                            nonlocal fired
                            if phase == "preferences" and Path(target) == self.profile / "user.js":
                                fired = True
                                raise error("simulated preferences failure")
                            return replace(path, target)
                        with patch.object(Path, "rename", fail_rename), patch.object(Path, "replace", fail_replace):
                            with self.assertRaises((RuntimeError, KeyboardInterrupt)):
                                self.change(operation)
                        self.assertTrue(fired)
                        self.assertEqual(self.snapshot(), before)
                        self.assertFalse(list(self.profile.glob(".fxcss-*")))

    def test_uninstall_preparation_failure_does_not_remove_active_files(self):
        before = self.snapshot()
        unlink = Path.unlink
        def fail(path, *args, **kwargs):
            if path.name == "userChrome.css" and ".fxcss-uninstall-" in str(path):
                raise PermissionError("simulated locked staged file")
            return unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail), self.assertRaises(RuntimeError):
            self.change("uninstall")
        self.assertEqual(self.snapshot(), before)

    def test_failed_recovery_retains_original_and_requested_backup(self):
        rename = Path.rename
        def fail(path, target):
            if Path(target) == self.profile / "chrome":
                raise PermissionError("both activation and recovery are blocked")
            return rename(path, target)
        for operation in ("rollback", "uninstall"):
            with self.subTest(operation=operation):
                # Restore the outgoing folder manually after examining recovery.
                with patch.object(Path, "rename", fail), self.assertRaisesRegex(RuntimeError, "recovery files"):
                    self.change(operation)
                work, = self.profile.glob(f".fxcss-{operation}-*")
                outgoing = (self.profile / "chrome.backup-20260103000000" if operation == "rollback"
                            else work / "previous")
                self.assertEqual(json.loads((outgoing / install.MANIFEST_NAME).read_text())["theme"], "o/n@v2")
                self.assertTrue((self.profile / self.second["backup"]).is_dir())
                outgoing.rename(self.profile / "chrome")

    def test_uninstall_keeps_edited_added_and_unverifiable_files(self):
        chrome = self.profile / "chrome"
        (chrome / "userChrome.css").write_text("my edits", encoding="utf-8")
        (chrome / "notes café.txt").write_text("my notes", encoding="utf-8")
        data = json.loads((chrome / install.MANIFEST_NAME).read_text())
        data["files"].extend([None, 123, "../user.js"])
        (chrome / install.MANIFEST_NAME).write_text(json.dumps(data))
        summary = install.uninstall_theme(self.profile)
        self.assertIn("chrome/userChrome.css", summary["kept"])
        self.assertIn("chrome/notes café.txt", summary["kept"])
        self.assertIsNone(summary["restored"])
        self.assertEqual((chrome / "userChrome.css").read_text(), "my edits")
        self.assertTrue((self.profile / self.first["backup"]).is_dir())

    @unittest.skipUnless(os.name == "nt", "Windows file sharing semantics")
    def test_native_windows_preferences_lock_restores_the_profile(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(self.profile / "user.js"), 0x80000000, 1,
                                    None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            for operation in ("rollback", "uninstall"):
                with self.subTest(operation=operation):
                    before = self.snapshot()
                    with self.assertRaises(RuntimeError):
                        self.change(operation)
                    self.assertEqual(self.snapshot(), before)
        finally:
            kernel.CloseHandle(handle)

    def test_removing_created_preferences_is_also_reversible(self):
        root = self.root / "fresh"
        root.mkdir()
        result = install.install_theme(self.theme, root, "o/n@v1")
        self.assertTrue(result["user_js_created"])
        before = (root / "chrome/userChrome.css").read_bytes()
        unlink = Path.unlink
        def fail(path, *args, **kwargs):
            if path == root / "user.js":
                raise PermissionError("cannot delete preferences")
            return unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail), self.assertRaises(RuntimeError):
            install.uninstall_theme(root)
        self.assertEqual((root / "chrome/userChrome.css").read_bytes(), before)
        self.assertTrue((root / "user.js").is_file())


if __name__ == "__main__":
    unittest.main()
