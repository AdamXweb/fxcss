"""Render representative theme layouts in a real Firefox with temporary profiles."""
import tempfile
import time
from pathlib import Path
from fxcss import core, install
from tests.theme_fixtures import EXPECTED_LAYOUTS, SHIPPED_CUSTOM_CHROME, write_themes

CHROME_PROPERTY = '''
    const win = Services.wm.getMostRecentWindow("navigator:browser");
    return win.getComputedStyle(win.document.documentElement)
        .getPropertyValue(arguments[0]).trim();
'''


def chrome_property(session, name):
    return session.m.script(CHROME_PROPERTY, [name])


def wait_for_property(session, name, expected, attempts=40):
    """Poll a computed custom property on the chrome root until it matches."""
    for _ in range(attempts):
        actual = chrome_property(session, name)
        if actual == expected:
            break
        time.sleep(0.25)
    return actual


def check_installed(theme, expected, profile, firefox):
    sheets = sorted((theme / "optional").glob("*.css"))
    install.install_theme(theme, profile, "local-fixture", sheets=sheets)
    with core.Session(profile, firefox) as session:
        session.setup_window()
        actual = wait_for_property(session, "--fxcss-layout", expected)
        assert actual == expected, (theme.name, actual, expected)
        if expected == "nested":
            palette = chrome_property(session, "--fxcss-palette")
            assert palette == "loaded", palette
        if expected == "content":
            session.m.set_context("content")
            try:
                content = session.m.script('return window.getComputedStyle(document.documentElement).getPropertyValue("--fxcss-content").trim();')
                assert content == "loaded", content
            finally:
                session.m.set_context("chrome")
        if expected == "custom":
            shipped = chrome_property(session, "--fxcss-custom-chrome")
            assert shipped == "shipped", (theme.name, shipped)
    install.uninstall_theme(profile)
    assert not (profile / "chrome").exists(), profile
    assert not (profile / "user.js").exists(), profile


def check_custom_chrome_preview(theme, firefox):
    """A theme's own customChrome.css must survive `try` previews and live reloads.

    build_profile and reload_theme both copy chrome/ and write a placeholder
    customChrome.css for themes that only @import it. A theme that ships the
    file used to have its rules overwritten by that placeholder, so the preview
    showed a theme missing part of itself.
    """
    with core.Session(theme, firefox) as session:
        session.setup_window()
        copied = (session.profile / "chrome" / "customChrome.css").read_text(encoding="utf-8")
        assert copied == SHIPPED_CUSTOM_CHROME, copied
        shipped = wait_for_property(session, "--fxcss-custom-chrome", "shipped")
        assert shipped == "shipped", shipped
    # watch mode leaves the profile's userChrome.css empty and loads each copy
    # of chrome/ as a live sheet, so a reload is where an overwrite would show.
    with core.Session(theme, firefox, empty_user_chrome=True) as session:
        session.setup_window()
        session.reload_theme()
        shipped = wait_for_property(session, "--fxcss-custom-chrome", "shipped")
        assert shipped == "shipped", shipped
        (theme / "chrome" / "customChrome.css").write_text(
            ':root { --fxcss-custom-chrome: reloaded; }\n', encoding="utf-8")
        session.reload_theme()
        reloaded = wait_for_property(session, "--fxcss-custom-chrome", "reloaded")
        assert reloaded == "reloaded", reloaded


def main():
    firefox = core.find_firefox()
    with tempfile.TemporaryDirectory(prefix="fxcss-layout-smoke-") as td:
        root = Path(td)
        themes = write_themes(root)
        assert [theme.name for theme in themes] == list(EXPECTED_LAYOUTS), themes
        for theme in themes:
            profile = root / (theme.name + " profile")
            profile.mkdir()
            check_installed(theme, EXPECTED_LAYOUTS[theme.name], profile, firefox)
            print(f"{theme.name}: styling loaded and clean removal restored the empty profile", flush=True)
        check_custom_chrome_preview(themes[-1], firefox)
        print("shipped custom chrome: the theme's own customChrome.css survived preview and live reload", flush=True)


if __name__ == "__main__":
    main()
