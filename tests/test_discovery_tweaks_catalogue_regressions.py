"""Regressions for browser discovery and generated documentation outputs."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from fxcss import cli, core, tweaks


class DiscoveryRegressions(unittest.TestCase):
    def bundle(self, root, name, executable):
        app = root / f"{name}.app"
        binary = app / "Contents" / "MacOS" / executable
        binary.parent.mkdir(parents=True)
        binary.write_text("fake executable; must never run")
        binary.chmod(0o755)
        return app, binary

    def test_channel_words_do_not_establish_browser_identity(self):
        for name in ("T3 Code (Beta)", "Citizen", "ESR Utility", "Developer Tools",
                     "Nightly Backup", "Zenith"):
            with self.subTest(name=name):
                self.assertIsNone(core._label_for(name))

    def test_unknown_bundle_executable_is_not_a_browser(self):
        with tempfile.TemporaryDirectory() as td:
            app, _ = self.bundle(Path(td), "T3 Code (Beta)", "T3 Code")
            self.assertIsNone(core._mac_binary(app))

    def test_fake_bundles_channel_resolution_and_explicit_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, unrelated = self.bundle(root, "T3 Code (Beta)", "T3 Code")
            expected = {}
            for name, executable, label in (
                ("Firefox", "firefox", "stable"),
                ("Firefox Beta", "firefox", "beta"),
                ("Firefox Developer Edition", "firefox", "developer"),
                ("Firefox Nightly", "firefox", "nightly"),
                ("FirefoxESR", "firefox", "esr"),
                ("LibreWolf", "librewolf", "librewolf"),
                ("Ablaze Floorp", "floorp", "floorp"),
                ("Waterfox", "waterfox", "waterfox"),
                ("Zen Browser", "zen", "zen"),
            ):
                _, binary = self.bundle(root, name, executable)
                expected[label] = str(binary.resolve())
            # Avoid host application directories and PATH, on every platform.
            real_glob = Path.glob
            def fake_glob(path, pattern):
                return real_glob(path, pattern) if path == root else iter(())
            with patch.object(core.sys, "platform", "darwin"), \
                    patch.dict(core.os.environ, {"FXCSS_FIREFOX_ROOTS": td}), \
                    patch.object(Path, "glob", fake_glob), \
                    patch.object(core.subprocess, "Popen") as launch, \
                    patch.object(core.subprocess, "run") as run:
                for label, binary in expected.items():
                    self.assertEqual(core.find_firefox(label), binary)
                (root / "Firefox Beta.app" / "Contents" / "MacOS" / "firefox").unlink()
                with self.assertRaises(SystemExit):
                    core.find_firefox("beta")
                self.assertEqual(core.find_firefox(str(unrelated)), str(unrelated.resolve()))
                launch.assert_not_called()
                run.assert_not_called()


class TweaksRegressions(unittest.TestCase):
    def test_base_option_preserves_baseline_and_links(self):
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as td:
                theme = Path(td)
                sheet = theme / "custom" / "base.css"
                sheet.parent.mkdir()
                sheet.write_text("/* optional */")
                variants = core.parse_variant_spec("all", core.find_variant_sheets(theme))
                self.assertEqual(list(variants), ["base"])
                out = theme / "tweaks"
                session = Mock()
                session.info.return_value = {"version": "test"}
                colors = iter(("white", "black" if changed else "white"))
                def shot(m, directory, name):
                    Image.new("RGB", (400, 200), next(colors)).save(directory / f"{name}.png")
                with patch.object(core, "_shot", side_effect=shot), \
                        patch.object(tweaks.time, "sleep"):
                    entries = tweaks.build(session, theme, out, variants, [])
                with Image.open(out / "base.png") as baseline:
                    self.assertEqual(baseline.getpixel((0, 0)), (255, 255, 255))
                self.assertEqual(entries[0]["slug"], "base")
                md = (out / "TWEAKS.md").read_text()
                self.assertIn("<b>base</b>", md)
                self.assertIn("(base.png)", md)
                if changed:
                    full = entries[0]["full_image"]
                    self.assertNotEqual(full, "base.png")
                    self.assertIn(f"]({full})", md)
                    with Image.open(out / full) as variant:
                        self.assertEqual(variant.getpixel((0, 0)), (0, 0, 0))
                    self.assertTrue((out / entries[0]["image"]).is_file())
                else:
                    self.assertEqual(list(out.glob("*.png")), [out / "base.png"])

    def test_readme_flag_table_escapes_pipes(self):
        md = tweaks.render_markdown(Path("."), [],
                                   [{"flag": "-a|-b", "text": "Choose A | B"}], "test")
        self.assertIn(r"| `-a\|-b` | Choose A \| B |", md)

    def test_option_named_like_a_diff_cannot_remove_another_options_panel(self):
        with tempfile.TemporaryDirectory() as td:
            theme = Path(td)
            out = theme / "out"
            session = Mock()
            session.info.return_value = {"version": "test"}
            colors = iter(("white", "black", "white"))

            def shot(m, directory, name):
                Image.new("RGB", (400, 200), next(colors)).save(directory / f"{name}.png")

            with patch.object(core, "_shot", side_effect=shot), patch.object(tweaks.time, "sleep"):
                entries = tweaks.build(session, theme, out,
                                       {"x": [theme / "x.css"], "x-diff": [theme / "x-diff.css"]}, [])
            self.assertEqual(entries[0]["image"], "x-diff.png")
            self.assertTrue((out / "x-diff.png").is_file())
            self.assertIsNone(entries[1]["image"])


class CatalogueOpeningRegressions(unittest.TestCase):
    def test_platform_openers(self):
        from fxcss import catalogue
        with tempfile.TemporaryDirectory() as td:
            args = SimpleNamespace(theme=Path(td), out=Path(td) / "space & output",
                                   firefox="fake", native_menus=False,
                                   self_contained=False, open=True)
            for platform, opener in (("win32", None), ("darwin", "open"),
                                     ("linux", "xdg-open")):
                with self.subTest(platform=platform), \
                        patch.object(cli.sys, "platform", platform), \
                        patch.object(cli, "choose_firefox", return_value="fake"), \
                        patch.object(core, "Session"), \
                        patch.object(catalogue, "build", return_value={"entries": []}), \
                        patch.object(cli.os, "startfile", create=True) as startfile, \
                        patch.object(cli.subprocess, "run") as run:
                    self.assertEqual(cli.cmd_catalogue(args), 0)
                    target = str(args.out.resolve() / "index.html")
                    if platform == "win32":
                        startfile.assert_called_once_with(target)
                        run.assert_not_called()
                    else:
                        run.assert_called_once_with([opener, target], check=False)
                        startfile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
