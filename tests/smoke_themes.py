"""Render representative theme layouts in a real Firefox with temporary profiles."""
import tempfile
import time
from pathlib import Path
from fxcss import core, install
from tests.theme_fixtures import write_themes


def main():
    firefox = core.find_firefox()
    with tempfile.TemporaryDirectory(prefix="fxcss-layout-smoke-") as td:
        root = Path(td)
        for theme, expected in zip(write_themes(root), ("nested", "content", "last")):
            profile = root / (theme.name + " profile")
            profile.mkdir()
            sheets = sorted((theme / "optional").glob("*.css"))
            install.install_theme(theme, profile, "local-fixture", sheets=sheets)
            with core.Session(profile, firefox) as session:
                session.setup_window()
                for _ in range(40):
                    actual = session.m.script('''
                        const win = Services.wm.getMostRecentWindow("navigator:browser");
                        return win.getComputedStyle(win.document.documentElement)
                            .getPropertyValue("--fxcss-layout").trim();
                    ''')
                    if actual == expected:
                        break
                    time.sleep(0.25)
                assert actual == expected, (theme.name, actual, expected)
                if expected == "nested":
                    palette = session.m.script('''
                        const win = Services.wm.getMostRecentWindow("navigator:browser");
                        return win.getComputedStyle(win.document.documentElement)
                            .getPropertyValue("--fxcss-palette").trim();
                    ''')
                    assert palette == "loaded", palette
                if expected == "content":
                    session.m.set_context("content")
                    try:
                        content = session.m.script('return window.getComputedStyle(document.documentElement).getPropertyValue("--fxcss-content").trim();')
                        assert content == "loaded", content
                    finally:
                        session.m.set_context("chrome")
            install.uninstall_theme(profile)
            assert not (profile / "chrome").exists(), profile
            assert not (profile / "user.js").exists(), profile
            print(f"{theme.name}: styling loaded and clean removal restored the empty profile", flush=True)


if __name__ == "__main__":
    main()
